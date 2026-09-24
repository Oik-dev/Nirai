import { app } from 'electron/main'
import assert from 'node:assert/strict'
import { randomUUID } from 'node:crypto'
import { HoloView } from '../src/main/holo-view.mjs'
import { DEFAULT_SETTINGS } from '../out/src/shared/settings.js'

const html = `<!doctype html><html><body><div id="messages"></div>
<div id="prompt-textarea" contenteditable="true" data-virtualkeyboard="true" style="width:700px;height:220px"></div>
<button data-testid="send-button">Send</button><button data-testid="stop-button" style="display:none">Stop</button>
<script>
const messages=document.getElementById('messages')
const input=document.querySelector('#prompt-textarea')
const send=document.querySelector('[data-testid=send-button]')
const stop=document.querySelector('[data-testid=stop-button]')
const inputValue=()=>input.innerText || input.textContent || ''
const setInput=value=>{input.innerText=value;input.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText',data:value}))}
const add=(role,text,complete=true)=>{
  if(role!=='assistant'){const el=document.createElement('div');el.dataset.messageAuthorRole=role;el.textContent=text;messages.append(el);return el}
  const article=document.createElement('article')
  const el=document.createElement('div');el.dataset.messageAuthorRole='assistant';el.textContent=text;article.append(el)
  if(complete){const copy=document.createElement('button');copy.dataset.testid='copy-turn-action-button';copy.textContent='Copy';article.append(copy)}
  messages.append(article);return el
}
window.clicks=Number(localStorage.getItem('clicks')||0)
window.withholdEvidence=false
window.assistantDelay=20
window.toolGap=false
if(location.pathname==='/c/delayed-ready'){input.style.display='none';send.style.display='none';setTimeout(()=>{input.style.display='block';send.style.display='inline'},250)}
send.onclick=()=>{
  window.clicks++
  localStorage.setItem('clicks',String(window.clicks))
  if(location.pathname==='/')history.pushState({},'', '/c/fixture-new')
  const text=inputValue()
  if(!window.withholdEvidence)add('user',text)
  input.replaceChildren()
  stop.style.display='inline'
  send.style.display='none'
  if(window.toolGap){
    const partial=add('assistant','partial tool preface',false)
    setTimeout(()=>{stop.style.display='none'},10)
    setTimeout(()=>{
      partial.textContent='fixture answer '+window.clicks
      const copy=document.createElement('button');copy.dataset.testid='copy-turn-action-button';copy.textContent='Copy';partial.parentElement.append(copy)
      send.style.display='inline'
    },window.assistantDelay+80)
  } else {
    setTimeout(()=>{
      add('assistant','fixture answer '+window.clicks,true)
      stop.style.display='none'
      send.style.display='inline'
    },window.assistantDelay)
  }
}
stop.onclick=()=>{stop.style.display='none';send.style.display='inline'}
</script></body></html>`

app.setPath('userData', process.env.NIRAI_HOLO_SMOKE_ROOT)
void app.whenReady().then(async () => {
  const turns = new Map()
  let deliveryHook = null
  const request = async (type, value) => {
    if (type === 'holo-observe' || type === 'holo-stopped') return { observed: true }
    const item = turns.get(value.turn_id)
    if (type === 'holo-delivered') {
      item.delivery = value.result
      if (value.result.status === 'not_sent' && value.result.retryable) item.retries++
      await deliveryHook?.(item, value.result)
      return { observed: true }
    }
    if (type === 'holo-assistant') {
      item.answer = value.content
      item.url = value.url
      return { saved: true }
    }
    if (type === 'holo-failed') {
      item.failed = value.reason
      return { ended: true }
    }
    throw new Error('unexpected request '+type)
  }

  const view = new HoloView(request, { partition: `holo-smoke-${randomUUID()}`, visible: false })
  clearInterval(view.poll)
  const wc = view.view.webContents
  await wc.session.protocol.handle('https', () => new Response(html, { headers: { 'content-type': 'text/html' } }))
  await view.open(false)
  assert.equal(view.window.isVisible(), false)
  const js = source => wc.executeJavaScript(source)
  const load = async path => { await wc.loadURL(`https://chatgpt.com${path}`); await js('document.readyState') }
  const make = (target = null) => {
    const turn = randomUUID()
    const dispatch = {
      turn_id: turn,
      task_id: randomUUID(),
      control_epoch: 1,
      target_conversation_id: target,
      prompt: `@nirai-v2\nturn_id=${turn}\nNirai-MCPでGetTaskContextを取得し、その内容に従ってTaskを続行してください。`,
      settings: { ...DEFAULT_SETTINGS, communication_retry_ms: [10,20], delivery_confirmation_ms: 300 },
    }
    const item = { dispatch, delivery: null, answer: null, retries: 0, failed: null }
    turns.set(turn,item)
    return item
  }

  try {
    await load('/c/master-draft')
    await js("setInput('Master draft')")
    const protectedDraft=make('another-task')
    await view.dispatch(protectedDraft.dispatch)
    assert.equal(protectedDraft.delivery.status,'not_sent')
    assert.equal(await js('inputValue()'),'Master draft')
    assert.equal(await js('window.clicks'),0)
    view.release(protectedDraft.dispatch.turn_id)

    await load('/')
    await js('window.toolGap=true')
    const normal=make()
    await view.dispatch(normal.dispatch)
    assert.equal(normal.delivery.status,'confirmed')
    assert.equal(normal.answer,'fixture answer 1')
    assert.equal(normal.url,'https://chatgpt.com/c/fixture-new')
    assert.equal(await js('window.clicks'),1)
    view.release(normal.dispatch.turn_id)

    await load('/')
    const stale=make()
    await js("setInput('@nirai-v2\\nturn_id=old-turn\\n\\n[TASK_CONTEXT]\\nstale')")
    await view.dispatch(stale.dispatch)
    assert.equal(stale.delivery.status,'confirmed')
    assert.equal(stale.answer,'fixture answer 2')
    assert.equal(await js('window.clicks'),2)
    view.release(stale.dispatch.turn_id)

    await load('/')
    const retried=make()
    await js("document.querySelector('[data-testid=send-button]').style.display='none'")
    deliveryHook=async (_item,result)=>{
      if(result.status==='not_sent'&&result.retryable) await js("document.querySelector('[data-testid=send-button]').style.display='inline'")
    }
    await view.dispatch(retried.dispatch)
    deliveryHook=null
    assert.equal(retried.delivery.status,'confirmed')
    assert.equal(retried.retries,1)
    assert.equal(retried.answer,'fixture answer 3')
    assert.equal(await js('window.clicks'),3)
    view.release(retried.dispatch.turn_id)

    await load('/c/fixture-unknown')
    await js('window.withholdEvidence=true')
    const unknown=make('fixture-unknown')
    await view.dispatch(unknown.dispatch)
    assert.equal(unknown.delivery.status,'unknown')
    assert.equal(unknown.answer,null)
    assert.equal(await js('window.clicks'),4)
    view.release(unknown.dispatch.turn_id)

    await load('/')
    const delayed=make('delayed-ready')
    await view.dispatch(delayed.dispatch)
    assert.equal(delayed.delivery.status,'confirmed')
    assert.equal(delayed.answer,'fixture answer 5')
    view.release(delayed.dispatch.turn_id)

    assert.equal(await js('typeof require'),'undefined')
    assert.equal(await js('typeof window.niraiDashboard'),'undefined')
    console.log('Holo WebContentsView fixture smoke passed: draft protection, one-send delivery, assistant text capture, finite pre-click retry, unknown delivery, delayed readiness, isolated web authority. No live ChatGPT connection tested.')
    view.close()
    app.exit(0)
  } catch (error) {
    console.error(error)
    view.close()
    app.exit(1)
  }
}).catch(error => { console.error(error); app.exit(1) })
