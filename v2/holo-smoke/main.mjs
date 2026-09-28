import { app, BrowserWindow } from 'electron/main'
import assert from 'node:assert/strict'
import { randomUUID } from 'node:crypto'
import { HoloView } from '../src/main/holo-view.mjs'
import { DEFAULT_SETTINGS } from '../out/src/shared/settings.js'
import { checkSkinSurfaces } from './skin-checks.mjs'

const html = `<!doctype html><html><body>
<aside id="sidebar" style="width:240px;height:700px"><button type="button">New chat</button><div id="sidebar-black" class="bg-black">History</div></aside>
<div id="mode-switch" style="width:190px;height:48px;background:black;border-radius:24px"><button type="button">Chat</button><button type="button">Work</button></div>
<main>
<div id="messages"></div>
<div id="message-actions" style="width:160px;height:42px"><button type="button" aria-label="Copy">C</button><button type="button" aria-label="Edit message">E</button></div>
<button id="scroll-bottom" type="button" data-testid="scroll-to-bottom-button">↓</button>
<form id="composer-shell" style="width:720px;height:110px">
  <div id="composer-inner" class="bg-token-main-surface-secondary" style="border-radius:22px;background:black">
    <div id="prompt-textarea" contenteditable="true" data-virtualkeyboard="true" style="width:680px;height:60px"></div>
    <button type="button" data-testid="send-button">Send</button><button type="button" data-testid="stop-button" style="display:none">Stop</button><button type="button" aria-label="音声を開始する">Voice</button>
  </div>
</form>
<div id="recommendation-frame" style="display:block;width:650px;height:72px;background:black;border-radius:14px">
  <button id="recommendation" type="button" style="display:block;width:620px;height:56px">Every Friday long-term memory recommendation</button>
</div>
<div id="dynamic-root"></div>
</main><script>
if(location.pathname==='/c/gone')history.replaceState({},'','/')
const messages=document.getElementById('messages')
const input=document.querySelector('#prompt-textarea')
const send=document.querySelector('[data-testid=send-button]')
const stop=document.querySelector('[data-testid=stop-button]')
const voice=document.querySelector('[aria-label="音声を開始する"]')
const inputValue=()=>input.innerText || input.textContent || ''
const setInput=value=>{input.innerText=value;input.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText',data:value}))}
const turn=role=>{
  const section=document.createElement('section')
  section.dataset.turn=role
  const article=document.createElement('article')
  section.append(article)
  section.contentRoot=article
  messages.append(section)
  return section
}
const part=(section,role,text)=>{const el=document.createElement('div');el.dataset.messageAuthorRole=role;el.textContent=text;section.contentRoot.append(el);return el}
const controls=(busy,waiting)=>{stop.style.display=busy?'inline':'none';send.style.display=busy?'none':'inline';voice.style.display=waiting?'inline':'none'}
const transcriptKey=()=>'fixture:'+location.pathname
const saveTranscript=entries=>localStorage.setItem(transcriptKey(),JSON.stringify(entries))
const renderTranscript=entries=>{
  messages.replaceChildren()
  for(const entry of entries){const section=turn(entry.role);part(section,entry.role,entry.text)}
}
const restored=JSON.parse(localStorage.getItem(transcriptKey())||'[]')
if(restored.length){renderTranscript(restored);controls(false,true)}
window.clicks=Number(localStorage.getItem('clicks')||0)
window.withholdEvidence=false
window.toolGap=false
window.reloadFallback=false
if(location.pathname==='/c/delayed-ready'&&!restored.length){input.style.display='none';send.style.display='none';setTimeout(()=>{input.style.display='block';send.style.display='inline'},250)}
send.onclick=()=>{
  window.clicks++
  localStorage.setItem('clicks',String(window.clicks))
  if(location.pathname==='/')history.pushState({},'', '/c/fixture-new')
  const text=inputValue()
  const answer='fixture answer '+window.clicks
  const saved=[]
  if(!window.withholdEvidence)saved.push({role:'user',text})
  if(window.toolGap)saved.push({role:'assistant',text:'調査します。'})
  saved.push({role:'assistant',text:answer})
  saveTranscript(saved)

  messages.replaceChildren()
  if(!window.withholdEvidence){const user=turn('user');part(user,'user',text)}
  input.replaceChildren()
  controls(true,false)
  if(window.toolGap){
    const interim=turn('assistant')
    part(interim,'assistant','調査します。')
    setTimeout(()=>controls(false,false),20)
    setTimeout(()=>controls(true,false),300)
    setTimeout(()=>{const reply=turn('assistant');part(reply,'assistant',answer);controls(false,true)},500)
  } else if(window.reloadFallback){
    const reply=turn('assistant')
    part(reply,'assistant','')
    setTimeout(()=>controls(false,true),20)
  } else {
    const reply=turn('assistant')
    setTimeout(()=>{part(reply,'assistant',answer);controls(false,true)},20)
  }
}
stop.onclick=()=>controls(false,true)
</script></body></html>`

app.setPath('userData', process.env.NIRAI_HOLO_SMOKE_ROOT)
void app.whenReady().then(async () => {
  const turns = new Map()
  const nativeSends = []
  const receipts = new Map()
  let view

  const request = async (type, value) => {
    if (type === 'holo-observe') return { observed: true }
    if (type === 'holo-native-send') {
      nativeSends.push(value)
      const result = { message_id: 'fixture-master-message' }
      receipts.set(value.event_id, result)
      return result
    }
    if (type === 'receipt') return receipts.get(value.command_id) ?? null

    const item = turns.get(value.turn_id)
    if (!item) throw new Error('unexpected Turn request '+type)
    if (type === 'holo-delivered') item.url = value.url
    else if (type === 'holo-sync') {
      item.syncs.push({ content: value.content, complete: value.complete })
      if (value.complete) item.answer = value.content
    } else if (type === 'holo-ended') item.ended = value
    else throw new Error('unexpected request '+type)
    return { observed: true }
  }

  const host = new BrowserWindow({ width: 1000, height: 800, show: false })
  view = new HoloView(request, host, { partition: `holo-smoke-${randomUUID()}` })
  clearInterval(view.poll)
  const wc = view.view.webContents
  await wc.session.protocol.handle('https', () => new Response(html, { headers: { 'content-type': 'text/html; charset=utf-8' } }))
  await view.open()
  const js = source => wc.executeJavaScript(source)
  const setTask = async (taskId, target = null) => {
    await view.setSurface({
      visible: true,
      bounds: { x: 0, y: 0, width: 900, height: 700 },
      task_id: taskId,
      capture: true,
      external_conversation_id: target,
      external_url: target ? `https://chatgpt.com/c/${target}` : null,
    })
  }
  const load = async path => { await wc.loadURL(`https://chatgpt.com${path}`); await js('document.readyState') }
  const run = async (target = null, beforeSend = null) => {
    const taskId = randomUUID()
    await setTask(taskId, target)
    if (beforeSend) await beforeSend()
    const turn = randomUUID()
    const item = { url: null, answer: null, ended: null, syncs: [] }
    turns.set(turn, item)
    await view.dispatch({
      task_id: taskId,
      turn_id: turn,
      target_conversation_id: target,
      prompt: `@nirai-v2\nturn_id=${turn}\nHolo Web Adapter smoke`,
      settings: { ...DEFAULT_SETTINGS, communication_retry_ms: [10, 20], delivery_confirmation_ms: 300 },
    })
    view.release(turn)
    return item
  }

  try {
    await view.applySkin()
    assert.notEqual(
      await js("getComputedStyle(document.documentElement).getPropertyValue('--glass').trim()"),
      '',
      'Holo skin must receive the same Nirai theme tokens as the renderer',
    )
    assert.equal(
      await js("document.documentElement.getAttribute('data-nirai-holo-skin')"),
      'product',
      'Holo skin marker must be applied after the composer is ready',
    )
    assert.equal(await js("getComputedStyle(document.getElementById('mode-switch')).display"), 'none', 'Chat/Work switch must be suppressed')
    assert.notEqual(await js("getComputedStyle(document.getElementById('recommendation-frame')).display"), 'none', 'native recommendations must not be hidden by geometry guesses')
    assert.equal(await js("document.querySelector('[data-nirai-holo-composer-shell]')?.id"), 'composer-inner', 'only the audited inner composer surface is themed')
    assert.equal(await js("getComputedStyle(document.getElementById('composer-inner')).borderTopWidth"), '1px', 'composer boundary comes from the owned inner surface')
    await setTask(randomUUID())
    // Coordinate input needs a presented native view, not a hidden window.
    host.show()
    await checkSkinSurfaces(js, wc)

    const nativeTaskId = randomUUID()
    await setTask(nativeTaskId)
    await js("setInput('Master raw native input')")
    await view.handleNativeSend({
      id: 'native-send-fixture',
      kind: 'send',
      task_id: nativeTaskId,
      content: 'Master raw native input',
      url: 'https://chatgpt.com/',
      conversation_id: null,
      issued_at: new Date().toISOString(),
    })
    assert.equal(nativeSends.length, 1)
    assert.equal(nativeSends[0].content, 'Master raw native input')
    assert.equal((await js('inputValue()')).trim(), '')

    await load('/c/master-draft')
    const protectedDraft = await run('another-task', () => js("setInput('Master draft')"))
    assert.deepEqual([protectedDraft.ended?.sent, protectedDraft.answer], [false, null])
    assert.equal(await js('inputValue()'), 'Master draft')
    assert.equal(await js('window.clicks'), 0)

    await load('/')
    const toolUse = await run(null, () => js('window.toolGap=true'))
    assert.equal(toolUse.syncs.length, 1, 'streaming fragments must not be mirrored into Task Chat')
    assert.equal(toolUse.syncs[0]?.complete, true)
    assert.equal(toolUse.answer, '調査します。\n\nfixture answer 1')
    assert.equal(toolUse.url, 'https://chatgpt.com/c/fixture-new')

    await load('/')
    const stale = await run(null, () => js("setInput('@nirai-v2\\nturn_id=old-turn\\nstale')"))
    assert.equal(stale.answer, 'fixture answer 2')

    await load('/')
    const retried = await run(null, () => js("document.querySelector('[data-testid=send-button]').style.display='none'; setTimeout(()=>document.querySelector('[data-testid=send-button]').style.display='inline',1200)"))
    assert.equal(retried.answer, 'fixture answer 3')
    assert.equal(await js('window.clicks'), 3)

    await load('/')
    const fallback = await run(null, () => js('window.reloadFallback=true'))
    assert.equal(fallback.answer, 'fixture answer 4', 'reload fallback must recover a complete saved reply when live DOM has no final text')

    await load('/c/fixture-unknown')
    const unknown = await run('fixture-unknown', () => js('window.withholdEvidence=true'))
    assert.deepEqual([unknown.ended?.sent, unknown.answer], [true, null])

    const delayed = await run('delayed-ready')
    assert.equal(delayed.answer, 'fixture answer 6')

    const gone = await run('gone')
    assert.deepEqual([gone.ended?.sent, gone.answer], [false, null], 'an unusable saved Conversation must block instead of silently rebinding')

    await load('/')
    await setTask(randomUUID())
    await js("setInput('programmatic click must not look like Master'); document.querySelector('[data-testid=send-button]').click()")
    const synthetic = await view.page({ operation: 'native-events', capture: true })
    assert.equal(synthetic.events.length, 0, 'programmatic Provider actions must not be interpreted as Master gestures')

    assert.equal(await js('typeof require'), 'undefined')
    assert.equal(await js('typeof window.niraiDashboard'), 'undefined')
    console.log('Holo WebContentsView fixture smoke passed: embedded native surface, Hub-first native input, no streaming mirror, final reply capture with reload fallback, finite pre-send retry, unknown delivery protection, delayed readiness, no silent Conversation rebinding, isolated web authority. No live ChatGPT connection tested.')
    view.close()
    host.destroy()
    app.exit(0)
  } catch (error) {
    console.error(error)
    view.close()
    host.destroy()
    app.exit(1)
  }
}).catch(error => { console.error(error); app.exit(1) })
