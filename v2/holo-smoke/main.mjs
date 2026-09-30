import { app, BrowserWindow } from 'electron/main'
import assert from 'node:assert/strict'
import { randomUUID } from 'node:crypto'
import { HoloView } from '../src/main/holo-view.mjs'
import { DEFAULT_SETTINGS } from '../out/src/shared/settings.js'
import { checkSkinSurfaces } from './skin-checks.mjs'

const smokeSettings = { ...DEFAULT_SETTINGS, holo_app_name: 'Nirai-v2' }

const html = `<!doctype html><html><body>
<aside id="sidebar" style="width:240px;height:700px"><button type="button">New chat</button><div id="sidebar-black" class="bg-black">History</div></aside>
<div id="mode-switch" style="width:190px;height:48px;background:black;border-radius:24px"><button type="button">Chat</button><button type="button">Work</button></div>
<main>
<div id="messages"></div>
<div id="message-actions" style="width:160px;height:42px"><button type="button" aria-label="Copy">C</button><button type="button" aria-label="Edit message">E</button></div>
<button id="scroll-bottom" type="button" data-testid="scroll-to-bottom-button">↓</button>
<form id="composer-shell" style="width:720px;height:110px">
  <div id="composer-inner" class="bg-token-main-surface-secondary" style="border-radius:22px;background:black">
    <div id="prompt-textarea" contenteditable="true" data-virtualkeyboard="true" style="width:680px;height:60px;white-space:pre-wrap"></div>
    <button type="button" data-testid="send-button">Send</button><button type="button" data-testid="stop-button" style="display:none">Stop</button><button type="button" aria-label="音声を開始する">Voice</button><button id="attachment" type="button" aria-label="添付">Attach</button>
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
const attachment=document.getElementById('attachment')
let currentDom=false
const inputValue=()=>input.innerText || input.textContent || ''
const setInput=value=>{input.innerText=value;input.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText',data:value}))}
const expectedApp=${JSON.stringify(smokeSettings.holo_app_name)}
window.mentionCandidates=[{name:expectedApp,description:'Local Nirai Task tools',id:'fixture-nirai'}]
window.mentionSelections=0
window.invalidAppSends=0
window.sentApps=[]
const mentionList=document.createElement('div')
mentionList.setAttribute('data-mention-list-scroll-area','')
mentionList.style.cssText='width:400px;min-height:40px'
document.body.append(mentionList)
const appToken=()=>input.querySelector('[app-mention-display-name]')
const selectApp=app=>{
  const token=document.createElement('span')
  token.setAttribute('app-mention-display-name',app.name)
  token.setAttribute('app-mention-name',app.name.toLowerCase())
  token.setAttribute('app-mention-path','app://'+app.id)
  token.setAttribute('data-prompt-link-href','app://'+app.id)
  token.contentEditable='false'
  token.textContent=app.name
  const paragraph=document.createElement('p');paragraph.append(token,' ')
  input.replaceChildren(paragraph)
  mentionList.replaceChildren();mentionList.hidden=true
  const selection=getSelection();const range=document.createRange()
  range.selectNodeContents(paragraph);range.collapse(false)
  selection.removeAllRanges();selection.addRange(range)
  input.focus()
  window.mentionSelections++
}
const appendAppBody=text=>{
  for(const line of text.split('\\n'))input.firstElementChild.append(document.createElement('br'),line)
}
const updateMentions=()=>{
  mentionList.replaceChildren()
  const query=inputValue().trim()
  mentionList.hidden=Boolean(appToken())||!query.startsWith('@')||query.includes('\\n')
  if(mentionList.hidden)return
  for(const app of window.mentionCandidates.filter(app=>app.name.toLowerCase().includes(query.slice(1).toLowerCase()))){
    const button=document.createElement('button');button.type='button';button.setAttribute('data-list-navigation-item','true')
    const row=document.createElement('div');row.setAttribute('data-menu-row-content','')
    const name=document.createElement('span');name.textContent=app.name
    const description=document.createElement('span');description.textContent=app.description
    row.append(name,description);button.append(row);button.onclick=()=>selectApp(app)
    mentionList.append(button)
  }
}
input.addEventListener('input',updateMentions)
const setCurrentDom=()=>{
  currentDom=true
  input.removeAttribute('id');input.removeAttribute('data-virtualkeyboard');input.setAttribute('aria-label','ChatGPT に聞く')
  send.removeAttribute('data-testid');send.type='submit';send.setAttribute('aria-label','送信')
  stop.removeAttribute('data-testid');stop.type='button';stop.setAttribute('aria-label','停止')
  voice.type='button';voice.setAttribute('aria-label','音声会話を開始')
}
const turn=role=>{
  if(currentDom){
    if(role==='assistant'&&messages.lastElementChild?.hasAttribute('data-turn-key'))return messages.lastElementChild
    const container=document.createElement('div')
    container.dataset.turnKey='fixture-'+messages.childElementCount
    container.contentRoot=container
    messages.append(container)
    return container
  }
  const section=document.createElement('section')
  section.dataset.turn=role
  const article=document.createElement('article')
  section.append(article)
  section.contentRoot=article
  messages.append(section)
  return section
}
const part=(section,role,text)=>{
  const el=document.createElement('div')
  if(currentDom){
    if(role==='user')el.dataset.userMessageBubble='true'
    else{
      const activity=document.createElement('button');activity.type='button';activity.textContent='20s考えました'
      const heading=document.createElement('h4');heading.className='sr-only';heading.textContent='ChatGPT の発言:'
      section.contentRoot.append(activity,heading)
      el.dataset.markdownTextStyle='assistant-message'
    }
  }else el.dataset.messageAuthorRole=role
  el.textContent=text
  section.contentRoot.append(el)
  if(currentDom&&role==='assistant'){
    const actions=document.createElement('div');actions.dataset.testid='turn-action-controls'
    const copy=document.createElement('button');copy.type='button';copy.textContent='コピー'
    const retry=document.createElement('button');retry.type='button';retry.textContent='再生成'
    actions.append(copy,retry);section.contentRoot.append(actions)
  }
  return el
}
const controls=(busy,waiting)=>{stop.style.display=busy?'inline':'none';send.style.display=busy?'none':'inline';voice.style.display=waiting?'inline':'none'}
const transcriptKey=()=>'fixture:'+location.pathname
const saveTranscript=entries=>{
  localStorage.setItem(transcriptKey(),JSON.stringify(entries))
  localStorage.setItem(transcriptKey()+':current-dom',String(currentDom))
}
const renderTranscript=entries=>{
  messages.replaceChildren()
  for(const entry of entries){const section=turn(entry.role);part(section,entry.role,entry.text)}
}
const restored=JSON.parse(localStorage.getItem(transcriptKey())||'[]')
if(localStorage.getItem(transcriptKey()+':current-dom')==='true')setCurrentDom()
if(restored.length){renderTranscript(restored);controls(false,true)}
window.clicks=Number(localStorage.getItem('clicks')||0)
window.withholdEvidence=false
window.toolGap=false
window.reloadFallback=false
if(location.pathname==='/c/delayed-ready'&&!restored.length){input.style.display='none';send.style.display='none';setTimeout(()=>{input.style.display='block';send.style.display='inline'},250)}
const sendPrompt=()=>{
  const text=inputValue()
  const token=appToken()
  if(text.includes('turn_id=')&&(!token||token.getAttribute('app-mention-display-name')!==expectedApp
    ||token.getAttribute('app-mention-path')!==token.getAttribute('data-prompt-link-href')
    ||!token.getAttribute('app-mention-path')?.startsWith('app://')||token.contentEditable!=='false')){
    window.invalidAppSends++;return
  }
  window.clicks++
  window.sentApps.push(token?.getAttribute('app-mention-display-name')??null)
  localStorage.setItem('clicks',String(window.clicks))
  if(location.pathname==='/')history.pushState({},'', '/c/fixture-new')
  const answer='fixture answer '+window.clicks
  const saved=[]
  if(!window.withholdEvidence)saved.push({role:'user',text})
  if(window.toolGap)saved.push({role:'assistant',text:'調査します。'})
  saved.push({role:'assistant',text:answer})
  saveTranscript(saved)

  messages.replaceChildren()
  if(!window.withholdEvidence){const user=turn('user');part(user,'user',text)}
  input.replaceChildren()
  mentionList.replaceChildren();mentionList.hidden=true
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
send.onclick=event=>{
  if(currentDom&&send.type==='submit')return
  event.preventDefault();sendPrompt()
}
stop.onclick=()=>controls(false,true)
attachment.onclick=()=>{window.attachmentClicks=(window.attachmentClicks||0)+1}
document.addEventListener('keydown',event=>{if(event.key==='Enter')window.nativeEnterTrusted=event.isTrusted},true)
document.addEventListener('click',event=>{window.nativeClickTrusted=event.isTrusted},true)
// Simulate the Provider's own Enter send. A bridge that misses the trusted
// gesture must visibly send here, rather than silently passing this fixture.
input.addEventListener('keydown',event=>{
  if(event.key!=='Enter'||event.shiftKey||event.altKey||event.ctrlKey||event.metaKey||event.isComposing||event.keyCode===229||send.disabled)return
  if(!mentionList.hidden&&mentionList.firstElementChild){event.preventDefault();mentionList.firstElementChild.click();return}
  event.preventDefault();send.click()
})
document.getElementById('composer-shell').addEventListener('submit',event=>{
  event.preventDefault();window.providerSubmitTrusted=event.isTrusted;sendPrompt()
})
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
  const nativeInput = async action => {
    await view.applySkin()
    await view.applySurface()
    assert.equal(view.attached, true, 'trusted input requires the native surface to be presented after navigation')
    host.focus()
    wc.focus()
    wc.debugger.attach('1.3')
    try {
      await wc.debugger.sendCommand('Emulation.setFocusEmulationEnabled', { enabled: true })
      await action()
      await js('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
    } finally { wc.debugger.detach() }
  }
  const pressEnter = async (modifiers = 0, keyCode = 13) => nativeInput(async () => {
    await js('input.scrollIntoView({block:"center"});input.focus()')
    await wc.debugger.sendCommand('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: keyCode, nativeVirtualKeyCode: keyCode, modifiers })
    await wc.debugger.sendCommand('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: keyCode, nativeVirtualKeyCode: keyCode, modifiers })
  })
  const clickNative = async name => nativeInput(async () => {
    await js(`${name}.scrollIntoView({block:"center"})`)
    await js('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
    const rect = await js(`${name}.getBoundingClientRect().toJSON()`)
    const point = { x: Math.round(rect.x + rect.width / 2), y: Math.round(rect.y + rect.height / 2) }
    await wc.debugger.sendCommand('Input.dispatchMouseEvent', { type: 'mouseMoved', ...point })
    await wc.debugger.sendCommand('Input.dispatchMouseEvent', { type: 'mousePressed', button: 'left', clickCount: 1, ...point })
    await wc.debugger.sendCommand('Input.dispatchMouseEvent', { type: 'mouseReleased', button: 'left', clickCount: 1, ...point })
  })
  const assertNativeSend = async (content, gesture) => {
    const count = nativeSends.length
    const providerCount = await js('window.clicks')
    await js(`setInput(${JSON.stringify(content)})`)
    await gesture()
    assert.equal(await js('window.clicks'), providerCount, 'native gesture must not reach the Provider before Hub acceptance')
    await view.drainNativeEvents()
    await view.drainNativeEvents()
    assert.equal(nativeSends.length, count + 1, 'one trusted gesture must save the Master input exactly once')
    assert.equal(nativeSends.at(-1).content, content)
    assert.equal(nativeSends.at(-1).task_id, view.surface.task_id)
    assert.equal((await js('inputValue()')).trim(), '')
    assert.equal(await js('window.clicks'), providerCount, 'Hub acceptance alone must not bypass Turn dispatch')
  }
  const run = async (target = null, beforeSend = null, settingsOverride = {}) => {
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
      prompt: `@${smokeSettings.holo_app_name}\nturn_id=${turn}\nHolo Web Adapter smoke`,
      settings: { ...smokeSettings, communication_retry_ms: [10, 20], delivery_confirmation_ms: 300, ...settingsOverride },
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
    await assertNativeSend('Master raw native input', pressEnter)
    assert.equal(await js('window.nativeEnterTrusted'), true)

    await js('setCurrentDom()')
    await assertNativeSend('今の画面で Enter 送信', pressEnter)
    await assertNativeSend('今の画面でボタン送信', () => clickNative('send'))
    assert.equal(await js('window.nativeClickTrusted'), true)

    await js("send.removeAttribute('aria-label')")
    await assertNativeSend('名前が未知でも明確な submit は捕捉', () => clickNative('send'))
    await js("send.removeAttribute('aria-label');send.type='button'")
    await assertNativeSend('送信ボタンが未知でも原文を先に保存', pressEnter)
    await js('setCurrentDom()')

    const beforeOtherGestures = nativeSends.length
    await js("setInput('改行・添付は送信しない')")
    await pressEnter(8) // CDP Shift modifier.
    await clickNative('attachment')
    await js('send.disabled=true')
    await clickNative('send')
    await pressEnter(0, 229)
    await view.drainNativeEvents()
    assert.equal(nativeSends.length, beforeOtherGestures, 'newline, attachment, disabled control and IME key code must not become Master sends')
    assert.equal(await js('window.attachmentClicks'), 1)
    assert.equal(await js('window.clicks'), 0)
    assert.match(await js('inputValue()'), /改行・添付は送信しない/)
    await js("send.disabled=false;setInput('');controls(true,false)")
    assert.equal((await view.page({ operation: 'observe' })).busy, true, 'current 停止 label must identify generation')
    await clickNative('stop')
    const stopped = await view.page({ operation: 'native-events', capture: true, task_id: nativeTaskId, conversation_id: null })
    assert.deepEqual(stopped.events.map(event => event.kind), ['stop'])
    const waiting = await view.page({ operation: 'observe' })
    assert.equal(waiting.busy, false)
    assert.equal(waiting.waiting, true, 'current 音声会話を開始 label must identify the final input-wait state')

    const openingTurn = randomUUID()
    const openingBridge = { operation: 'native-events', capture: true, task_id: nativeTaskId, conversation_id: null, turn_id: openingTurn }
    await view.page(openingBridge)
    await js("history.pushState({},'', '/c/opening-conversation')")
    for (const marker of [openingTurn, randomUUID(), null]) {
      await js(`messages.replaceChildren();${marker ? `part(turn('user'),'user',${JSON.stringify(`@${smokeSettings.holo_app_name}\nturn_id=${marker}\nfixture input`)});` : ''}controls(true,false)`)
      await clickNative('stop')
      const openingStop = await view.page(openingBridge)
      assert.deepEqual(openingStop.events.map(event => event.kind), marker === openingTurn ? ['stop'] : [],
        'before the first Conversation binding arrives, only its exact active Turn evidence may authorize Stop capture')
    }

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
    assert.deepEqual(await js('window.sentApps'), [smokeSettings.holo_app_name], 'Task dispatch must select the native app token before sending')
    assert.equal(await js('window.invalidAppSends'), 0, 'plain @ text must never reach Provider send')

    await load('/')
    const stale = await run(null, () => js(`setInput(${JSON.stringify(`@${smokeSettings.holo_app_name}\nturn_id=old-turn\nstale`)})`))
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
    const currentDom = await run(null, () => js('setCurrentDom()'))
    assert.equal(currentDom.answer, 'fixture answer 7', 'current send/stop/waiting labels must support complete Turn observation')
    assert.equal(await js('window.providerSubmitTrusted'), true, 'current Provider sends from the browser submit event, including programmatic button activation')
    assert.equal(await js("messages.querySelectorAll('section,article,[data-message-author-role]').length"), 0, 'current message markup must not accidentally pass through legacy selectors')
    assert.equal(await js("messages.querySelectorAll('[data-turn-key]').length"), 1, 'current user and assistant bodies share their surrounding Turn element')

    await load('/')
    const currentFallback = await run(null, () => js('setCurrentDom();window.reloadFallback=true'))
    assert.equal(currentFallback.answer, 'fixture answer 8', 'reload fallback must preserve the current message-body shape')
    assert.equal(await js("messages.querySelectorAll('[data-message-author-role]').length"), 0)

    for (const prepare of [
      `setInput(${JSON.stringify('@' + smokeSettings.holo_app_name)})`,
      `selectApp(${JSON.stringify({ name: smokeSettings.holo_app_name, description: 'Already selected by Master', id: 'master-selected' })})`,
    ]) {
      await load('/')
      let masterDraft
      let masterSelections
      const preexistingApp = await run(null, async () => {
        await js(prepare)
        masterDraft = await js('inputValue()')
        masterSelections = await js('window.mentionSelections')
      })
      assert.deepEqual([preexistingApp.ended?.sent, preexistingApp.answer], [false, null], 'a preexisting Master query or app token must block new Turn preparation')
      assert.equal(await js('inputValue()'), masterDraft, 'preexisting app preparation must not be cleared')
      assert.equal(await js('window.mentionSelections'), masterSelections, 'the Adapter must not select or replace a Master-owned app')
    }

    for (const candidates of [
      [{ name: smokeSettings.holo_app_name + '-extra', description: smokeSettings.holo_app_name, id: 'similar-name' }],
      [{ name: smokeSettings.holo_app_name.toLowerCase(), description: 'Different case', id: 'different-case' }],
      [
        { name: smokeSettings.holo_app_name, description: 'First matching app', id: 'first-match' },
        { name: smokeSettings.holo_app_name, description: 'Second matching app', id: 'second-match' },
      ],
    ]) {
      await load('/')
      const beforeAppFailure = await js('window.clicks')
      const unavailableApp = await run(null, () => js(`window.mentionCandidates=${JSON.stringify(candidates)}`), { communication_attempts: 1 })
      assert.deepEqual([unavailableApp.ended?.sent, unavailableApp.answer], [false, null], 'missing, differently cased or ambiguous app names must remain proven unsent')
      assert.equal(await js('window.clicks'), beforeAppFailure)
      assert.equal(await js('window.mentionSelections'), 0, 'descriptions and duplicate exact names must not choose an app')
      assert.equal(await js('window.invalidAppSends'), 0, 'app selection failure must be detected before the Provider send button')
    }

    await load('/')
    const selectionTask = randomUUID()
    await setTask(selectionTask)
    const selectionTurn = randomUUID()
    const selectionPrompt = `@${smokeSettings.holo_app_name}\nturn_id=${selectionTurn}\nApp selection boundary`
    const selectionRequest = { conversation_id: null, turn_id: selectionTurn, prompt: selectionPrompt }
    const beforeSelectionSends = await js('window.clicks')
    const beforeSelectionMaster = nativeSends.length
    await js(`setInput(${JSON.stringify('@' + smokeSettings.holo_app_name)})`)
    await pressEnter()
    await view.drainNativeEvents()
    assert.equal(nativeSends.length, beforeSelectionMaster, 'Enter on a native app candidate must remain Provider-local')
    assert.equal(await js('window.clicks'), beforeSelectionSends, 'candidate Enter must select the app without sending a message')
    assert.equal(await js('appToken()?.getAttribute("app-mention-display-name")'), smokeSettings.holo_app_name,
      JSON.stringify(await js('({draft:inputValue(),menuHidden:mentionList.hidden,candidates:mentionList.childElementCount,trustedEnter:window.nativeEnterTrusted,selections:window.mentionSelections})')))
    await pressEnter()
    await view.drainNativeEvents()
    assert.equal(nativeSends.length, beforeSelectionMaster, 'an app token alone is not a Master instruction')
    const selectedMasterInput = '  Master raw input with native app already selected\n    indented second line  \n'
    await js(`appendAppBody(${JSON.stringify(selectedMasterInput)})`)
    await pressEnter()
    await view.drainNativeEvents()
    assert.equal(nativeSends.length, beforeSelectionMaster + 1)
    assert.equal(nativeSends.at(-1).content, selectedMasterInput, 'Hub saves the Master body without the immutable app token display name')
    assert.equal((await js('inputValue()')).trim(), '', 'Hub acceptance clears the selected app and saved Master body together')
    assert.equal(await js('window.clicks'), beforeSelectionSends, 'a selected-app Master send still requires Hub-authorized Turn dispatch')

    const assertNoAppSend = async (prepare, explanation) => {
      await js(prepare)
      assert.equal((await view.page({ operation: 'observe', ...selectionRequest })).ready_to_send, false, explanation)
      assert.equal((await view.page({ operation: 'send', ...selectionRequest })).ok, false, explanation)
      assert.equal(await js('window.clicks'), beforeSelectionSends)
    }
    await assertNoAppSend(`setInput(${JSON.stringify(selectionPrompt)})`, 'the complete @ envelope as plain text is not an app selection')
    const correctCandidate = { name: smokeSettings.holo_app_name, description: 'Nirai Task tools', id: 'selected-app' }
    const selectedBody = `turn_id=${selectionTurn}\nApp selection boundary`
    const selectedWithBody = `selectApp(${JSON.stringify(correctCandidate)});appendAppBody(${JSON.stringify(selectedBody)})`
    await assertNoAppSend(`selectApp(${JSON.stringify({ ...correctCandidate, name: 'Another app' })});appendAppBody(${JSON.stringify(selectedBody)})`, 'a different selected app must not authorize the Task send')
    await assertNoAppSend(`${selectedWithBody};appToken().removeAttribute('app-mention-path')`, 'a display label without the Provider app path must not authorize the Task send')
    await assertNoAppSend(`${selectedWithBody};appToken().setAttribute('data-prompt-link-href','app://different')`, 'the native token path and link must match')
    await assertNoAppSend(`selectApp(${JSON.stringify(correctCandidate)})`, 'selected app without the Turn body must not be ready to send')
    await js(selectedWithBody)
    assert.equal((await view.page({ operation: 'observe', ...selectionRequest })).ready_to_send, true, 'the selected native app token and matching Turn body form the ready state')
    await js(`${selectedWithBody};input.firstElementChild.append(' Master changed this draft')`)
    const changedDraft = await js('inputValue()')
    assert.equal((await view.page({ operation: 'fill', ...selectionRequest })).ok, false, 'Master changes to a prepared app draft must be protected')
    assert.equal(await js('inputValue()'), changedDraft)
    await js("setInput('')")
    const queryPrepared = await view.page({ operation: 'fill', ...selectionRequest })
    assert.equal(queryPrepared.ok, true)
    assert.equal(queryPrepared.pending, true, 'native app selection is staged before the full Turn body')
    assert.equal(queryPrepared.started, true, 'only Adapter preparation from an empty composer owns the staged query')
    await js("setInput('Master edited the staged query')")
    assert.equal((await view.page({ operation: 'fill', ...selectionRequest, preparing: true })).ok, false, 'Master edits during staged selection must not be overwritten')
    assert.equal(await js('inputValue()'), 'Master edited the staged query')
    assert.equal(await js('window.clicks'), beforeSelectionSends)

    await load('/')
    const beforeCancelledSend = await js('window.clicks')
    const normalPage = view.page
    let cancelledAfterQuery = false
    let cancelledBeforeClick = false
    view.page = async request => {
      const result = await normalPage.call(view, request)
      if (request.operation === 'fill' && result.started && !cancelledAfterQuery) {
        cancelledAfterQuery = true
        assert.equal(await js('inputValue()'), '@' + smokeSettings.holo_app_name, 'the Provider has mutated the composer before Main receives fill ownership')
        cancelledBeforeClick = view.active?.clicked === false
        await view.cancel(view.active.dispatch.turn_id)
      }
      return result
    }
    let cancelledPreparation
    try { cancelledPreparation = await run() }
    finally { view.page = normalPage }
    assert.equal(cancelledAfterQuery, true, 'cancel must interleave with the first completed page mutation')
    assert.equal(cancelledBeforeClick, true)
    assert.deepEqual([cancelledPreparation.url, cancelledPreparation.answer], [null, null])
    assert.equal(await js('window.clicks'), beforeCancelledSend, 'cancelled preparation must never send to the Provider')
    assert.equal(await js('window.mentionSelections'), 0, 'cancellation before the fill reply must not continue app selection')
    assert.equal((await js('inputValue()')).trim(), '', 'a query inserted before cancellation still belongs to the Adapter and must be cleaned up')
    assert.equal(await js('appToken() === null'), true)

    await load('/c/body-boundaries')
    const bodyTurn = randomUUID()
    const entries = [
      { role: 'user', text: 'previous input' },
      { role: 'assistant', text: 'previous answer must stay outside' },
      { role: 'user', text: `@${smokeSettings.holo_app_name}\nturn_id=${bodyTurn}\nbody boundary input` },
      { role: 'assistant', text: '前半の本文' },
      { role: 'assistant', text: '後半の本文' },
      { role: 'user', text: 'next user input' },
      { role: 'assistant', text: 'next answer must stay outside' },
    ]
    await js(`setCurrentDom();renderTranscript(${JSON.stringify(entries)});controls(false,true)`)
    const currentBodies = await view.page({ operation: 'turn', turn_id: bodyTurn })
    assert.equal(currentBodies.received, true)
    assert.equal(currentBodies.text, '前半の本文\n\n後半の本文', 'capture body text only, excluding activity, headings, action labels and adjacent Turns')
    await js(`(() => {
      const users = [...messages.querySelectorAll('[data-user-message-bubble]')]
      const replies = [...messages.querySelectorAll('[data-markdown-text-style]')]
      const wrapLegacy = (element, role) => {
        const wrapper = document.createElement('div')
        wrapper.dataset.messageAuthorRole = role
        wrapper.append('outer legacy wrapper UI')
        element.replaceWith(wrapper)
        wrapper.append(element)
      }
      wrapLegacy(users[1], 'user')
      replies[1].dataset.messageAuthorRole = 'assistant'
      wrapLegacy(replies[2], 'assistant')
      users[2].removeAttribute('data-user-message-bubble')
      users[2].dataset.messageAuthorRole = 'user'
      replies[3].removeAttribute('data-markdown-text-style')
      replies[3].dataset.messageAuthorRole = 'assistant'
    })()`)
    const mixedBodies = await view.page({ operation: 'turn', turn_id: bodyTurn })
    assert.equal(mixedBodies.received, true)
    assert.equal(mixedBodies.text, currentBodies.text, 'mixed aliases and nested legacy wrappers must capture each body once and preserve the next-user boundary')

    await load('/')
    await setTask(randomUUID())
    await js("setInput('programmatic click must not look like Master'); document.querySelector('[data-testid=send-button]').click()")
    const synthetic = await view.page({ operation: 'native-events', capture: true })
    assert.equal(synthetic.events.length, 0, 'programmatic Provider actions must not be interpreted as Master gestures')

    await setTask(randomUUID(), 'bound-task')
    await load('/c/provider-only')
    await js('setCurrentDom()')
    await view.refresh()
    const beforeForeignSends = nativeSends.length
    const beforeForeignProvider = await js('window.clicks')
    await js("setInput('別の会話では通常の ChatGPT 操作')")
    await pressEnter()
    await view.drainNativeEvents()
    assert.equal(await js('window.clicks'), beforeForeignProvider + 1,
      'Enter in another Conversation must remain a Provider-local send: ' + JSON.stringify(await js('({draft:inputValue(),trustedEnter:window.nativeEnterTrusted,menuHidden:mentionList.hidden,disabled:send.disabled,busy:stop.style.display})')))
    await js("setInput('別の会話の送信ボタンも維持')")
    await clickNative('send')
    await view.drainNativeEvents()
    assert.equal(await js('window.clicks'), beforeForeignProvider + 2)
    assert.equal(nativeSends.length, beforeForeignSends, 'another Conversation must not update the selected Task')

    assert.equal(await js('typeof require'), 'undefined')
    assert.equal(await js('typeof window.niraiDashboard'), 'undefined')
    console.log('Holo WebContentsView fixture smoke passed: embedded native surface, trusted native Enter/click with Hub-first input across old/current DOM, exact unique native app selection and token checks, missing/ambiguous app rejection, Provider-local candidate Enter, protected edits during app preparation, unknown send protection, current Stop/waiting, first-Conversation Stop correlation, foreign Conversation isolation, no streaming mirror, old/current final reply capture and reload fallback, mixed message-body aliases without controls or adjacent Turns, finite pre-send retry, unknown delivery protection, delayed readiness, no silent Conversation rebinding, isolated web authority. No live ChatGPT connection tested.')
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
