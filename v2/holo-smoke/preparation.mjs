import { app, BrowserWindow } from 'electron/main'
import { readFileSync } from 'node:fs'
import assert from 'node:assert/strict'
import { holoPageOperation } from '../src/main/holo-dom.mjs'

app.setPath('userData', process.env.NIRAI_HOLO_SMOKE_ROOT)

app.whenReady().then(async () => {
  // Reuse the Provider fixture without opening its foreground interaction tests.
  // Keep fixture failures inside this caught promise so Electron cannot open
  // its default startup-error dialog on the user's desktop.
  const fixtureSource = readFileSync(new URL('./main.mjs', import.meta.url), 'utf8')
  const first = fixtureSource.indexOf('const html = ')
  const last = fixtureSource.indexOf("app.setPath('userData'", first)
  assert.ok(first >= 0 && last > first)
  const html = new Function('smokeSettings', `${fixtureSource.slice(first, last)}\nreturn html`)({ holo_app_name: 'Nirai-v2' })
  const host = new BrowserWindow({ width: 1000, height: 800, show: false, focusable: false, skipTaskbar: true,
    webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false, backgroundThrottling: false } })
  const wc = host.webContents
  try {
    await wc.session.protocol.handle('https', () => new Response(html, { headers: { 'content-type': 'text/html; charset=utf-8' } }))
    const debug = async (method, params) => {
      let timer
      try {
        return await Promise.race([wc.debugger.sendCommand(method, params),
          new Promise((_resolve, reject) => { timer = setTimeout(() => reject(new Error(`hidden debugger timeout: ${method}`)), 5000) })])
      } finally { clearTimeout(timer) }
    }
    const page = request => wc.executeJavaScript(`(${holoPageOperation.toString()})(${JSON.stringify(request)})`, true)
    const js = text => wc.executeJavaScript(text, true)
    for (const currentDom of [false, true]) {
      await wc.loadURL('https://chatgpt.com/')
      if (!wc.debugger.isAttached()) wc.debugger.attach('1.3')
      // Focus emulation affects this hidden renderer only, not the OS window.
      await debug('Emulation.setFocusEmulationEnabled', { enabled: true })
      if (currentDom) await js('setCurrentDom()')
      await page({ operation: 'native-events', capture: true, task_id: 'fixture-task', conversation_id: null, turn_id: null })
      const clicks = await js('window.clicks')
      await js("window.trustedBeforeInput = []; window.addEventListener('beforeinput', event => window.trustedBeforeInput.push(event.isTrusted), true)")
      await js("setInput('Master draft')")
      assert.equal((await page({ operation: 'presentation', preparing: true, conversation_id: null })).presenting, true)
      assert.equal(await js('inputValue()'), 'Master draft', 'presentation must not replace the input')
      assert.equal((await page({ operation: 'observe' })).draft, true, 'original draft remains visible to the input guard')
      await js('input.focus()')
      await debug('Input.insertText', { text: 'extra' })
      await debug('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13 })
      assert.equal(await js('inputValue()'), 'Master draft', 'trusted input must not edit a covered composer')
      assert.equal(await js('window.clicks'), clicks, 'Enter must not submit preparation as a Master message')
      assert.deepEqual((await page({ operation: 'native-events', capture: true, task_id: 'fixture-task', conversation_id: null, turn_id: null })).events, [])
      assert.deepEqual(await js('window.trustedBeforeInput'), [true])
      await page({ operation: 'presentation', preparing: false })
      await debug('Input.insertText', { text: 'extra' })
      assert.notEqual(await js('inputValue()'), 'Master draft', 'normal input must resume after preparation')
      await js("setInput('Master draft')")
      await page({ operation: 'presentation', preparing: true, conversation_id: null })
      await page({ operation: 'clear-native', content: 'Master draft' })
      const prompt = '@Nirai-v2\nturn_id=preparation-check\nfixture input'
      let ready
      for (let attempt = 0; attempt < 15; attempt++) {
        const fill = await page({ operation: 'fill', conversation_id: null, prompt, preparing: attempt > 0 })
        assert.equal(fill.ok, true, fill.reason)
        ready = await page({ operation: 'observe', prompt, preparing: true })
        if (ready.ready_to_send) break
        await new Promise(resolve => setTimeout(resolve, 50))
      }
      assert.equal(ready.ready_to_send, true, 'presentation must allow native app selection and programmatic editing')
      assert.equal((await page({ operation: 'send', conversation_id: null, turn_id: 'preparation-check', prompt })).ok, true)
      assert.equal(await js('window.invalidAppSends'), 0)
      assert.equal(await js('window.clicks'), clicks + 1)
      await new Promise(resolve => setTimeout(resolve, 300))
      assert.equal(await js("Boolean(document.querySelector('[data-nirai-holo-presentation]'))"), false, 'Stop or conversation change must remove the preparation overlay')
      await js('controls(true, false)')
      assert.equal((await page({ operation: 'presentation', preparing: true, conversation_id: 'fixture-new' })).presenting, false, 'preparation must never cover native Stop')
      assert.equal((await page({ operation: 'stop', conversation_id: 'fixture-new' })).requested, true, 'native Stop remains actionable')

      // A Provider can retain its Turn wrapper while removing the user bubble.
      // Exercise that native DOM transition, without borrowing a neighboring
      // answer or treating a reusable Provider key as Hub execution authority.
      await js(`(() => {
        controls(false, true)
        messages.replaceChildren()
        const userAttribute = ${JSON.stringify(currentDom ? 'data-user-message-bubble' : 'data-message-author-role')}
        const assistantAttribute = ${JSON.stringify(currentDom ? 'data-markdown-text-style' : 'data-message-author-role')}
        const body = (role, text) => {
          const element = document.createElement('div')
          element.setAttribute(role === 'user' ? userAttribute : assistantAttribute,
            role === 'user' ? ${JSON.stringify(currentDom ? 'true' : 'user')} : ${JSON.stringify(currentDom ? 'assistant-message' : 'assistant')})
          element.textContent = text
          return element
        }
        const wrapper = document.createElement('div')
        wrapper.id = 'binding-turn'
        wrapper.dataset.turnKey = 'provider-key-a'
        const user = body('user', 'turn_id=binding-check\\nMaster request')
        user.id = 'binding-user'
        wrapper.append(user, body('assistant', '途中メモ'), body('assistant', '最終回答'))
        const neighbor = document.createElement('div')
        neighbor.dataset.turnKey = 'different-provider-key'
        neighbor.append(body('user', 'turn_id=another-turn\\nDifferent request'), body('assistant', '別Turnの回答'))
        messages.append(wrapper, neighbor)
      })()`)
      const turnRequest = {
        operation: 'turn', turn_id: 'binding-check', conversation_id: 'fixture-new', provider_turn_key: null,
      }
      const rejected = async (request, reason) => {
        const result = await page(request)
        assert.equal(result.received, false, reason)
        assert.equal(result.text, '', reason)
      }
      const anchored = await page(turnRequest)
      assert.equal(anchored.received, true)
      assert.equal(anchored.provider_turn_key, 'provider-key-a', 'Provider key must originate from the real user marker')
      assert.equal(anchored.text, '途中メモ\n\n最終回答', 'only assistant bodies belonging to the bound Turn are read')
      const boundRequest = { ...turnRequest, provider_turn_key: anchored.provider_turn_key }
      await js("window.bindingNeighborUser = document.querySelector('[data-turn-key=\"different-provider-key\"]').firstElementChild; window.bindingNeighborUser.remove()")
      assert.equal((await page(turnRequest)).text, anchored.text, 'an adjacent removed user must not widen the current Turn response')
      assert.equal((await page(boundRequest)).text, anchored.text, 'a proven Provider key excludes an adjacent assistant even while its user is absent')
      await js("document.querySelector('[data-turn-key=\"different-provider-key\"]').prepend(window.bindingNeighborUser); delete window.bindingNeighborUser")
      await js("(() => { const duplicate = document.querySelector('#binding-user').cloneNode(true); duplicate.id = 'binding-marker-duplicate'; messages.append(duplicate) })()")
      await rejected(turnRequest, 'two real user markers cannot identify one Turn')
      await rejected(boundRequest, 'a previous Provider binding cannot resolve duplicate real markers')
      await js("document.querySelector('#binding-marker-duplicate').remove()")
      await js("document.querySelector('#binding-user').remove()")
      await js("(() => { const title = document.createElement('button'); title.textContent = 'turn_id=binding-check'; messages.before(title) })()")
      const withoutUser = await page(boundRequest)
      assert.equal(withoutUser.received, true, 'removing the user bubble must not lose the already observed Turn')
      assert.equal(withoutUser.provider_turn_key, 'provider-key-a')
      assert.equal(withoutUser.text, anchored.text, 'multiple assistant blocks survive the Provider redraw')
      await rejected(turnRequest, 'a sidebar title cannot substitute for a real user marker or prior binding')
      await rejected({ ...boundRequest, conversation_id: 'different-conversation' }, 'a Provider key cannot cross Conversations')
      await rejected({ ...boundRequest, provider_turn_key: 'missing-provider-key' }, 'a missing key cannot adopt the latest assistant')
      await js("document.querySelector('#binding-turn').removeAttribute('data-turn-key')")
      await rejected(boundRequest, 'a removed key cannot adopt another Turn')
      await js("document.querySelector('#binding-turn').dataset.turnKey = 'provider-key-a'")
      await js("(() => { const duplicate = document.createElement('div'); duplicate.id = 'binding-duplicate'; duplicate.dataset.turnKey = 'provider-key-a'; messages.append(duplicate) })()")
      await rejected(boundRequest, 'duplicate Provider keys cannot identify one Turn')
      await js("document.querySelector('#binding-duplicate').remove()")
      await js(`(() => {
        const user = document.createElement('div')
        user.id = 'binding-user'
        user.setAttribute(${JSON.stringify(currentDom ? 'data-user-message-bubble' : 'data-message-author-role')}, ${JSON.stringify(currentDom ? 'true' : 'user')})
        user.textContent = 'turn_id=conflicting-turn\\nAnother request'
        document.querySelector('#binding-turn').prepend(user)
      })()`)
      await rejected(boundRequest, 'a contradictory user body invalidates the Provider key')
      await js("document.querySelector('#binding-user').textContent = 'turn_id=binding-check\\nMaster request'; document.querySelector('#binding-turn').dataset.turnKey = 'provider-key-b'")
      const rebound = await page(boundRequest)
      assert.equal(rebound.received, true)
      assert.equal(rebound.provider_turn_key, 'provider-key-b', 'the real marker may rebind a changed Provider key')
      assert.equal(rebound.text, anchored.text)
      await js("document.querySelector('#binding-turn').removeAttribute('data-turn-key')")
      const legacyAnchor = await page(turnRequest)
      assert.equal(legacyAnchor.received, true, 'the original marker path remains valid without Provider keys')
      assert.equal(legacyAnchor.text, anchored.text)

      const chatPrompt = 'message_id=normal-binding-check\nchannel=Whisper\naudience=Master,Holo\nMaster body'
      const chatRequest = { conversation_id: 'fixture-new', message_id: 'normal-binding-check', prompt: chatPrompt }
      await js("setInput('Master native draft')")
      assert.equal((await page({ operation: 'fill', ...chatRequest })).ok, false, 'normal speech must protect a native draft')
      assert.equal((await page({ operation: 'clear', ...chatRequest })).cleared, false)
      assert.equal(await js('inputValue()'), 'Master native draft')
      await js("setInput('')")
      assert.equal((await page({ operation: 'fill', ...chatRequest })).ok, true)
      assert.equal((await page({ operation: 'observe', ...chatRequest })).ready_to_send, true)
      assert.equal((await page({ operation: 'send', ...chatRequest })).ok, true)
      await new Promise(resolve => setTimeout(resolve, 100))
      const normalReply = await page({ operation: 'turn', ...chatRequest })
      assert.equal(normalReply.received, true, 'a Task-free Message marker identifies its real input')
      assert.match(normalReply.text, /^fixture answer /)
      assert.equal(await js('window.sentApps.at(-1)'), null, 'normal speech sends without a Task MCP app')
      assert.deepEqual((await page({ operation: 'native-events', capture: false })).events, [], 'ordinary speech cannot become a Task-native send')
      await rejected({ operation: 'turn', ...chatRequest, turn_id: 'normal-binding-check' }, 'mixing Message and Task identities must be rejected')
      await js(`(() => {
        const marker = [...messages.querySelectorAll('[data-message-author-role="user"],[data-user-message-bubble="true"]')][0]
        const duplicate = marker.cloneNode(true)
        duplicate.id = 'normal-marker-duplicate'
        duplicate.textContent = 'message_id=normal-binding-check-other\\nNested context: message_id=normal-binding-check'
        messages.append(duplicate)
      })()`)
      assert.equal((await page({ operation: 'turn', ...chatRequest })).received, true, 'a prefix or nested Context marker cannot duplicate the current Message')
      await js("document.querySelector('#normal-marker-duplicate').textContent = 'message_id=normal-binding-check\\nDuplicate input'")
      await rejected({ operation: 'turn', ...chatRequest }, 'two real Message markers cannot borrow the latest answer')
      assert.equal(host.isVisible(), false)
      assert.equal(host.isFocused(), false)
      console.log(`PASS hidden Provider DOM (${currentDom ? 'current' : 'legacy'}): draft and trusted input protection -> app selection -> send -> live controls; no foreground window`)
      console.log(`PASS hidden Provider Turn (${currentDom ? 'current' : 'legacy'}): marker binding -> user redraw -> assistant bodies; ambiguity, Conversation and conflicting user rejected; marker rebind and keyless path preserved`)
      console.log(`PASS hidden normal Message (${currentDom ? 'current' : 'legacy'}): protected native draft -> plain send without MCP/Task -> matching reply; mixed identities and ambiguous markers rejected`)
    }
  } finally { host.destroy() }
  app.quit()
}).catch(error => { console.error(error); app.exit(1) })
