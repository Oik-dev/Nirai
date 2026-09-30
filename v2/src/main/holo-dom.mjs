// Runs inside the ChatGPT page. It observes the native surface and mediates
// Task-bound send/Stop gestures; Task authority stays in the Hub.
export function holoPageOperation(request) {
  const stopSelector = 'button[data-testid="stop-button"],button[aria-label="Stop generating"],button[aria-label="生成を停止"],button[aria-label="停止"]'
  const sendSelector = 'button[data-testid="send-button"],button[data-testid="composer-submit-button"],button#composer-submit-button,button[aria-label="Send prompt"],button[aria-label="Send"],button[aria-label="メッセージを送信"],button[aria-label="送信"]'
  const waitingSelector = 'button[aria-label="音声を開始する"],button[aria-label="音声会話を開始"],button[aria-label="Start voice mode"],button[aria-label="Start voice"]'
  const composerSelector = '#prompt-textarea,textarea[placeholder],[contenteditable="true"][data-virtualkeyboard="true"],[contenteditable="true"]'

  const actionable = element => {
    if (!(element instanceof HTMLElement) || !element.isConnected || element.matches(':disabled')) return false
    for (let node = element; node; node = node.parentElement) {
      const style = getComputedStyle(node)
      if (node.hidden || node.inert || node.getAttribute('aria-hidden') === 'true' || node.getAttribute('aria-disabled') === 'true'
        || style.display === 'none' || ['hidden', 'collapse'].includes(style.visibility) || Number(style.opacity) === 0) return false
    }
    return element.getClientRects().length > 0
  }
  const composer = () => [...document.querySelectorAll(composerSelector)].find(actionable)
  const composerScope = () => composer()?.closest('form') ?? document
  const composerSubmit = button => button instanceof HTMLButtonElement && button.type === 'submit'
    && Boolean(button.form?.contains(composer()))
  const stopButton = () => [...composerScope().querySelectorAll(stopSelector)].find(actionable)
  const sendButton = () => [...composerScope().querySelectorAll('button')].find(element =>
    (element.matches(sendSelector) || composerSubmit(element)) && !element.matches(stopSelector) && actionable(element))
  const waitingButton = () => [...composerScope().querySelectorAll(waitingSelector)].find(actionable)
  const valueOf = target => (target instanceof HTMLTextAreaElement ? target.value : target?.innerText ?? target?.textContent ?? '').replaceAll('\r\n', '\n')
  const value = () => valueOf(composer())
  const normalized = text => (text ?? '').replace(/[\s\u200B\uFEFF]+/g, ' ').trim()
  const sameText = (a, b) => normalized(a) === normalized(b)
  const isNiraiEnvelope = text => {
    const lines = String(text ?? '').replaceAll('\r\n', '\n').split('\n').map(line => line.trim()).filter(Boolean)
    return Boolean(lines[0]?.startsWith('@') && lines[1]?.startsWith('turn_id='))
  }
  // Read message bodies, not their surrounding activity headers and controls.
  // Some Provider versions nest the new body marker inside the legacy role.
  const messageBodies = selector => [...document.querySelectorAll(selector)].filter(element => !element.querySelector(selector))
  const userMessages = () => messageBodies('[data-message-author-role="user"],[data-user-message-bubble="true"]')
  const assistantMessages = () => messageBodies('[data-message-author-role="assistant"],[data-markdown-text-style="assistant-message"]')
  const current = () => /^\/(?:g\/[^/]+\/)?c\/([a-zA-Z0-9-]+)\/?$/.exec(location.pathname)?.[1] ?? null
  const origin = () => location.origin === 'https://chatgpt.com'
  const loginVisible = () => [...document.querySelectorAll('button,a')].some(element => actionable(element) && /^(?:Log in|Sign in|ログイン)$/.test(element.textContent?.trim() ?? ''))
  const eventId = () => globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`

  const installDecorator = () => {
    const key = '__niraiV2Decorator'
    if (!origin()) {
      window[key]?.dispose?.()
      try { delete window[key] } catch {}
      return false
    }
    const existing = window[key]
    if (existing?.version === 5) { existing.decorate(); return true }
    existing?.dispose?.()

    const markNames = ['composer-shell', 'sidebar', 'sidebar-header', 'sidebar-footer', 'mode-switch'].map(name => `data-nirai-holo-${name}`)
    const allMarks = markNames.map(name => `[${name}]`).join(',')
    let wanted
    const mark = (node, name) => {
      if (!(node instanceof HTMLElement)) return
      if (!wanted.has(node)) wanted.set(node, new Set())
      wanted.get(node).add(name)
      if (!node.hasAttribute(name)) node.setAttribute(name, 'true')
    }
    const decorate = () => {
      wanted = new Map()
      const main = document.querySelector('main')
      const target = document.querySelector(composerSelector)
      // Only a rounded inner composer owns a fill. Never paint the outer form.
      if (target instanceof HTMLElement) {
        const form = target.closest('form')
        for (let node = target.parentElement; node && node !== main && node !== form; node = node.parentElement) {
          if (Number.parseFloat(getComputedStyle(node).borderRadius) >= 10) {
            mark(node, 'data-nirai-holo-composer-shell')
            break
          }
        }
      }

      const headerButtons = [...document.querySelectorAll('button')].filter(button =>
        button.closest('header') || !button.closest('main,nav,aside,[role="dialog"]'))
      const chat = headerButtons.find(button => button.getAttribute('data-tpp-toggle-value') === 'chatgpt')
        ?? headerButtons.find(button => normalized(button.textContent) === 'Chat')
      const work = headerButtons.find(button => button.getAttribute('data-tpp-toggle-value') === 'work')
        ?? headerButtons.find(button => normalized(button.textContent) === 'Work')
      if (chat && work) {
        const group = chat.closest('[role="radiogroup"],[role="tablist"]')
        let switcher = group?.contains(work) ? group : null
        if (!switcher) for (let node = chat.parentElement; node && node !== document.body; node = node.parentElement) {
          if (node.contains(work)) { switcher = node; break }
        }
        if (switcher) {
          const rect = switcher.getBoundingClientRect()
          if (switcher === group || (rect.width <= 520 && rect.height <= 140)) mark(switcher, 'data-nirai-holo-mode-switch')
        }
      }

      // Both provider sidebar generations were observed live. The explicit
      // root includes header + history + account; a navigation child does not.
      const sidebars = new Set(document.querySelectorAll('#browser-sidebar-popover, #app-shell-sidebar, #stage-popover-sidebar, #stage-slideover-sidebar'))
      if (!sidebars.size) for (const nav of document.querySelectorAll('aside,nav')) {
        if (nav.closest('[inert]') || nav.contains(main)) continue
        if ([...nav.querySelectorAll('a,button')].some(node => /^(New chat|新しいチャット)$/.test(normalized(node.textContent)))) {
          sidebars.add(nav.closest('[role="dialog"],aside') ?? nav)
        }
      }
      for (const sidebar of sidebars) {
        mark(sidebar, 'data-nirai-holo-sidebar')
        const header = [...sidebar.querySelectorAll('#sidebar-header')].find(node => !node.closest('#stage-sidebar-tiny-bar'))
        if (header) {
          let surface = header
          for (let node = header.parentElement; node && node !== sidebar && !node.matches('nav'); node = node.parentElement) {
            if (getComputedStyle(node).position === 'sticky') { surface = node; break }
          }
          mark(surface, 'data-nirai-holo-sidebar-header')
        }
        const nav = sidebar.querySelector('nav:not(#stage-sidebar-tiny-bar)')
        const account = [...sidebar.querySelectorAll('[data-testid="accounts-profile-button"]')].find(node => !node.closest('#stage-sidebar-tiny-bar'))
        if (nav && account) for (let node = account; node && node !== sidebar; node = node.parentElement) {
          if (node.contains(nav)) break
          if (node.parentElement?.contains(nav)) { mark(node, 'data-nirai-holo-sidebar-footer'); break }
        }
      }
      for (const node of document.querySelectorAll(allMarks)) {
        for (const name of markNames) if (!wanted.get(node)?.has(name)) node.removeAttribute(name)
      }
      return Boolean(target)
    }
    const observer = new MutationObserver(decorate)
    observer.observe(document.documentElement, {
      childList: true, subtree: true, attributes: true,
      attributeFilter: ['role', 'aria-label', 'data-testid', 'data-tpp-toggle-value', 'id'],
    })
    Object.defineProperty(window, key, { configurable: true, writable: true, value: {
      version: 5, decorate,
      dispose() {
        observer.disconnect()
        for (const node of document.querySelectorAll(allMarks)) for (const name of markNames) node.removeAttribute(name)
      },
    } })
    decorate()
    return true
  }

  const installScrollGuard = () => {
    const key = '__niraiV2ScrollGuard'
    if (!origin()) {
      window[key]?.dispose?.()
      try { delete window[key] } catch {}
      return false
    }
    const existing = window[key]
    if (existing?.version === 1) return true
    existing?.dispose?.()

    let scroller = null
    let observer = null
    let frameId = 0
    let followingLatest = true
    let lastScrollTop = 0

    const bottomDistance = element => Math.max(0, element.scrollHeight - element.scrollTop - element.clientHeight)
    const atBottom = element => bottomDistance(element) <= 6
    const findScroller = () => {
      const messages = document.querySelectorAll('[data-message-author-role]')
      const anchors = []
      const lastMessage = messages.length ? messages[messages.length - 1] : null
      if (lastMessage instanceof HTMLElement) anchors.push(lastMessage)
      const target = composer()
      if (target instanceof HTMLElement) anchors.push(target)
      for (const anchor of anchors) {
        for (let node = anchor.parentElement; node && node !== document.body; node = node.parentElement) {
          const overflowY = getComputedStyle(node).overflowY
          if ((overflowY === 'auto' || overflowY === 'scroll') && node.scrollHeight > node.clientHeight + 8) return node
        }
      }
      const page = document.scrollingElement
      return page && page.scrollHeight > page.clientHeight + 8 ? page : null
    }
    const follow = () => {
      if (!followingLatest || !scroller || frameId) return
      frameId = requestAnimationFrame(() => {
        frameId = 0
        if (!followingLatest || !scroller) return
        scroller.scrollTop = scroller.scrollHeight
        lastScrollTop = scroller.scrollTop
      })
    }
    const stopFollowing = () => {
      followingLatest = false
      if (frameId) cancelAnimationFrame(frameId)
      frameId = 0
    }
    const onScroll = () => {
      if (!scroller) return
      const next = scroller.scrollTop
      if (next < lastScrollTop - 1) stopFollowing()
      else if (atBottom(scroller)) followingLatest = true
      lastScrollTop = next
    }
    const onWheel = event => { if (event.deltaY < 0) stopFollowing() }
    const onKey = event => {
      if (event.key === 'ArrowUp' || event.key === 'PageUp' || event.key === 'Home') stopFollowing()
      else if (event.key === 'End') { followingLatest = true; follow() }
    }
    const unbind = () => {
      observer?.disconnect?.()
      observer = null
      if (!scroller) return
      scroller.removeEventListener('scroll', onScroll)
      scroller.removeEventListener('wheel', onWheel)
    }
    const bind = () => {
      const next = findScroller()
      if (next === scroller) return
      const had = Boolean(scroller)
      const wasFollowing = followingLatest
      unbind()
      scroller = next
      if (!scroller) return
      followingLatest = had ? wasFollowing : atBottom(scroller)
      lastScrollTop = scroller.scrollTop
      scroller.addEventListener('scroll', onScroll, { passive: true })
      scroller.addEventListener('wheel', onWheel, { passive: true })
      observer = new MutationObserver(follow)
      observer.observe(scroller, { childList: true, subtree: true, characterData: true })
      if (followingLatest) follow()
    }

    document.addEventListener('keydown', onKey, true)
    const timerId = setInterval(bind, 750)
    bind()
    Object.defineProperty(window, key, {
      value: {
        version: 1,
        dispose() {
          clearInterval(timerId)
          document.removeEventListener('keydown', onKey, true)
          unbind()
          scroller = null
          if (frameId) cancelAnimationFrame(frameId)
          frameId = 0
        },
      },
      configurable: true,
      writable: true,
    })
    return true
  }

  if (request.operation === 'decorate') {
    return { decorated: installDecorator(), scroll_guard: installScrollGuard() }
  }
  const nativeBridge = (capture, taskId, conversationId, turnId) => {
    const key = '__niraiV2NativeBridge'
    const existing = window[key]
    if (!capture) {
      existing?.dispose?.()
      try { delete window[key] } catch {}
      return null
    }
    if (existing?.version === 2) {
      existing.task_id = taskId
      existing.conversation_id = conversationId
      existing.turn_id = turnId
      return existing
    }

    existing?.dispose?.()
    const queue = []
    let lastSend = null
    let providerSending = false
    // A selected Task must not capture input from a different Provider conversation.
    const bound = () => origin() && (bridge.conversation_id === null
      ? current() === null && location.pathname === '/'
      : current() === bridge.conversation_id)
    // A first send can create its URL before Main receives delivery evidence.
    // Only Stop may use this exact active Turn marker during that short interval.
    const activeResponse = () => origin() && bridge.conversation_id === null && Boolean(current()) && bridge.turn_id
      && userMessages().some(element => element.textContent?.includes(`turn_id=${bridge.turn_id}`))

    const pushSend = text => {
      if (!normalized(text)) return
      const now = Date.now()
      if (lastSend && lastSend.text === text && now - lastSend.at < 250) return
      lastSend = { text, at: now }
      queue.push({
        id: eventId(),
        kind: 'send',
        task_id: bridge.task_id,
        content: text,
        url: location.href,
        conversation_id: current(),
        issued_at: new Date().toISOString(),
      })
    }

    const onClick = event => {
      if (!event.isTrusted || !(event.target instanceof Element)) return
      const button = event.target.closest('button')
      if (!(button instanceof HTMLElement)) return
      if (button === stopButton()) {
        if ((bound() || activeResponse()) && !normalized(value()) && Number(event.detail) > 0) {
          queue.push({
            id: eventId(),
            kind: 'stop',
            task_id: bridge.task_id,
            url: location.href,
            conversation_id: current(),
            issued_at: new Date().toISOString(),
          })
        }
        return
      }
      if (!bound()) return
      if (button !== sendButton()) return
      if (button.matches(stopSelector) || !actionable(button) || stopButton()) return
      const text = value()
      if (!normalized(text)) return
      event.preventDefault()
      event.stopImmediatePropagation()
      pushSend(text)
    }

    const onKeyDown = event => {
      if (!event.isTrusted || !bound() || event.key !== 'Enter' || event.shiftKey || event.altKey || event.ctrlKey || event.metaKey
        || event.isComposing || event.keyCode === 229 || stopButton()) return
      const target = composer()
      if (!(target instanceof HTMLElement) || !(event.target instanceof Node) || !(event.target === target || target.contains(event.target))) return
      const text = valueOf(target)
      if (!normalized(text)) return
      // Capture the Master gesture independently of Provider button discovery.
      // Only the existing Hub-authorized dispatch may later send this text.
      event.preventDefault()
      event.stopImmediatePropagation()
      pushSend(text)
    }

    const onSubmit = event => {
      if (!event.isTrusted || providerSending || !bound() || stopButton()) return
      const target = composer()
      if (!(target instanceof HTMLElement) || !(event.target instanceof Element) || !event.target.contains(target)) return
      const text = valueOf(target)
      if (!normalized(text)) return
      event.preventDefault()
      event.stopImmediatePropagation()
      pushSend(text)
    }

    document.addEventListener('click', onClick, true)
    document.addEventListener('keydown', onKeyDown, true)
    document.addEventListener('submit', onSubmit, true)
    const bridge = {
      version: 2,
      task_id: taskId,
      conversation_id: conversationId,
      turn_id: turnId,
      send(button, id) {
        bridge.turn_id = id
        providerSending = true
        try { button.click() }
        finally { providerSending = false }
      },
      take() { return queue.splice(0, queue.length) },
      dispose() {
        document.removeEventListener('click', onClick, true)
        document.removeEventListener('keydown', onKeyDown, true)
        document.removeEventListener('submit', onSubmit, true)
      },
    }
    Object.defineProperty(window, key, { value: bridge, configurable: true, writable: true })
    return bridge
  }

  if (request.operation === 'native-events') {
    const bridge = nativeBridge(Boolean(request.capture), typeof request.task_id === 'string' ? request.task_id : null,
      typeof request.conversation_id === 'string' ? request.conversation_id : null,
      typeof request.turn_id === 'string' ? request.turn_id : null)
    return { events: bridge?.take?.() ?? [] }
  }

  const draftText = value()
  const promptText = typeof request.prompt === 'string' ? request.prompt : ''
  const draftKind = !normalized(draftText) ? 'empty'
    : promptText && sameText(draftText, promptText) ? 'current'
    : isNiraiEnvelope(draftText) ? 'nirai'
    : 'user'
  const userDraft = draftKind === 'user'
  const stop = stopButton()
  const send = sendButton()
  const targetComposer = composer()
  const waiting = waitingButton()
  const observation = {
    url: location.href,
    conversation_id: current(),
    busy: Boolean(stop),
    waiting: Boolean(waiting),
    draft: userDraft,
    state: !origin() ? 'unavailable' : loginVisible() || !targetComposer ? 'blocked' : stop ? 'busy' : userDraft ? 'blocked' : 'ready',
    reason: !origin() ? 'Holoのログイン画面または対象外のページです'
      : loginVisible() ? 'ChatGPTへログインしてください'
        : !targetComposer ? '入力欄を確認できません'
          : stop ? 'Holoが応答を生成しています'
            : userDraft ? 'Masterが入力中です'
              : 'Holo接続の準備ができています',
    retryable: origin() && !loginVisible() && !stop && !userDraft,
  }
  if (request.operation === 'observe') return { ...observation, ready_to_send: Boolean(targetComposer && send && !stop && draftKind === 'current') }

  const follows = (anchor, node) => Boolean(anchor.compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING)

  if (request.operation === 'turn') {
    const marker = `turn_id=${request.turn_id}`
    const users = userMessages()
    const anchor = users.filter(element => element.textContent?.includes(marker)).at(-1)
    const received = origin() && Boolean(current()) && Boolean(anchor)
    if (!anchor) return { ...observation, received, text: '' }

    const nextUser = users.find(element => follows(anchor, element))
    const responses = assistantMessages().filter(element =>
      follows(anchor, element) && (!nextUser || follows(element, nextUser)))
    const text = responses
      .map(element => (element.innerText ?? '').trim())
      .filter(Boolean)
      .join('\n\n')
    return { ...observation, received, text }
  }

  const matches = origin() && (request.conversation_id === null
    ? current() === null && location.pathname === '/'
    : current() === request.conversation_id)

  const setText = text => {
    const target = composer()
    if (!(target instanceof HTMLElement)) return false
    target.focus()
    if (target instanceof HTMLTextAreaElement) {
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set
      if (setter) setter.call(target, text)
      else target.value = text
      target.dispatchEvent(new Event('input', { bubbles: true }))
    } else {
      const range = document.createRange(); range.selectNodeContents(target)
      const selection = getSelection(); selection?.removeAllRanges(); selection?.addRange(range)
      if (text) document.execCommand('insertText', false, text)
      else document.execCommand('delete', false)
      target.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: text ? 'insertText' : 'deleteContentBackward', data: text || null }))
    }
    return sameText(valueOf(target), text)
  }

  if (request.operation === 'clear') {
    if (matches && targetComposer && sameText(value(), request.prompt)) return { cleared: setText('') }
    return { cleared: false }
  }
  if (request.operation === 'clear-native') {
    if (targetComposer && sameText(value(), request.content)) return { cleared: setText('') }
    return { cleared: false }
  }
  if (request.operation === 'stop') {
    if (!matches) return { stopped: false }
    const currentStop = stopButton()
    if (currentStop) { currentStop.click(); return { stopped: false, requested: true } }
    return { stopped: true }
  }
  if (!matches || loginVisible() || !targetComposer || stopButton()) {
    return { ok: false, retryable: !loginVisible() && !userDraft, reason: '対象会話・ログイン・生成状態を再確認してください' }
  }
  if (request.operation === 'fill') {
    if (draftKind === 'current') return { ok: true }
    if (draftKind === 'user') return { ok: false, retryable: false, reason: 'Masterの下書きを保護しました' }
    return { ok: setText(request.prompt), retryable: true, reason: '入力結果を確認できません' }
  }
  if (request.operation === 'send') {
    if (!sameText(value(), request.prompt)) return { ok: false, retryable: false, reason: '送信直前に下書きが変更されました' }
    const currentSend = sendButton()
    if (!currentSend) return { ok: false, retryable: true, reason: '送信ボタンを確認できません' }
    // A programmatic button click may produce a trusted form submit event.
    // Do not turn our own Hub-authorized send into another Master message.
    const bridge = window.__niraiV2NativeBridge
    if (bridge?.version === 2) bridge.send(currentSend, request.turn_id)
    else currentSend.click()
    return { ok: true }
  }
  return { ok: false, reason: '不明な操作です' }
}
