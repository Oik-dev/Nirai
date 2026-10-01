// Runs inside the ChatGPT page. It observes the native surface and mediates
// Task-bound send/Stop gestures and Task-free chat delivery; authority stays in the Hub.
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
  const mentionLists = () => [...document.querySelectorAll('[data-mention-list-scroll-area]')].filter(actionable)
  // A Provider-selected app is an immutable mention, not the literal @name.
  // These attributes and the candidate row were audited on the live surface.
  const appMention = target => {
    const mentions = target?.querySelectorAll('[app-mention-display-name]') ?? []
    if (mentions.length !== 1) return null
    const token = mentions[0]
    const name = token.getAttribute('app-mention-display-name')
    const path = token.getAttribute('app-mention-path')
    if (!name || !path?.startsWith('app://') || token.getAttribute('data-prompt-link-href') !== path
      || token.getAttribute('contenteditable') !== 'false' || !sameText(valueOf(token), name)) return null
    const before = document.createRange()
    before.selectNodeContents(target); before.setEndBefore(token)
    if (normalized(before.toString())) return null
    const text = valueOf(target).trimStart()
    if (!text.startsWith(name)) return null
    // The picker inserts one separator after its chip. Preserve Master body
    // whitespace beyond that separator, including intentional indentation.
    return { token, name, body: text.slice(name.length).replace(/^[ \t\u00A0]?(?:\n)?/, '') }
  }
  const masterValue = target => appMention(target)?.body ?? valueOf(target)
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

  if (request.operation === 'presentation') {
    const key = '__niraiV2Presentation'
    const existing = window[key]
    const matches = () => origin() && (request.conversation_id === null
      ? current() === null && location.pathname === '/'
      : typeof request.conversation_id === 'string' && current() === request.conversation_id)
    if (request.preparing !== true || !matches() || stopButton() || !document.body) {
      existing?.dispose?.()
      return { presenting: false }
    }
    if (existing?.url === location.href && existing.conversation_id === request.conversation_id) {
      existing.update()
      return { presenting: window[key] === existing }
    }
    existing?.dispose?.()

    // Cover only the audited input and app picker. The actual Provider nodes
    // stay actionable for app selection, draft checks and native Stop.
    const root = document.createElement('div')
    root.setAttribute('data-nirai-holo-presentation', 'preparing')
    root.style.cssText = 'position:fixed;inset:0;z-index:2147483646;pointer-events:none;overflow:hidden;'
    const masks = new Map()
    let frameId = 0
    let disposed = false
    const url = location.href
    const dispose = () => {
      if (disposed) return
      disposed = true
      observer.disconnect()
      clearInterval(timerId)
      if (frameId) cancelAnimationFrame(frameId)
      window.removeEventListener('resize', schedule)
      document.removeEventListener('scroll', schedule, true)
      window.removeEventListener('keydown', guardInput, true)
      window.removeEventListener('beforeinput', guardInput, true)
      window.removeEventListener('popstate', dispose)
      window.removeEventListener('hashchange', dispose)
      window.removeEventListener('pagehide', dispose)
      root.remove()
      masks.clear()
      if (window[key] === state) delete window[key]
    }
    const update = () => {
      if (disposed) return
      if (!matches() || location.href !== url || stopButton()) { dispose(); return }
      const target = composer()
      const form = target?.closest('form') ?? target
      const wanted = new Map()
      if (form && actionable(form)) wanted.set(form, 'composer')
      for (const list of mentionLists()) wanted.set(list, 'mention')
      for (const [element, mask] of masks) if (!wanted.has(element)) { mask.remove(); masks.delete(element) }
      for (const [element, kind] of wanted) {
        const rect = element.getBoundingClientRect()
        let mask = masks.get(element)
        if (!mask) {
          mask = document.createElement('div')
          mask.setAttribute('data-nirai-holo-presentation-mask', kind)
          mask.style.cssText = 'position:absolute;display:flex;align-items:center;justify-content:center;pointer-events:auto;border:1px solid var(--line-soft,rgba(145,207,229,.25));border-radius:14px;background:rgb(5 25 40);color:var(--text-soft,#bbd4df);font:14px/1.5 system-ui,sans-serif;box-sizing:border-box;'
          mask.textContent = '送信中…'
          if (kind === 'composer') { mask.setAttribute('role', 'status'); mask.setAttribute('aria-live', 'polite') }
          else mask.setAttribute('aria-hidden', 'true')
          masks.set(element, mask)
          root.appendChild(mask)
        }
        for (const [name, value] of Object.entries({ left: rect.left, top: rect.top, width: rect.width, height: rect.height })) {
          const pixels = `${value}px`
          if (mask.style[name] !== pixels) mask.style[name] = pixels
        }
      }
    }
    const schedule = () => {
      if (disposed || frameId) return
      frameId = requestAnimationFrame(() => { frameId = 0; update() })
    }
    const guardInput = event => {
      if (disposed) return
      if (!matches() || location.href !== url || stopButton()) { dispose(); return }
      const target = composer()
      const scope = target?.closest('form') ?? target
      if (!event.isTrusted || !scope?.contains(event.target)) return
      // Keep the focused Provider input from turning preparation text into a
      // Master gesture. Programmatic input used by the adapter still passes.
      event.preventDefault()
      event.stopImmediatePropagation()
    }
    const observer = new MutationObserver(records => {
      if (records.some(record => !root.contains(record.target))) schedule()
    })
    // pushState does not emit a navigation event; check only while preparing.
    const timerId = setInterval(update, 250)
    const state = { url, conversation_id: request.conversation_id, update, dispose }
    Object.defineProperty(window, key, { configurable: true, writable: true, value: state })
    document.body.appendChild(root)
    observer.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['style', 'class', 'hidden', 'aria-hidden', 'aria-disabled', 'disabled', 'aria-label', 'data-testid', 'type'] })
    window.addEventListener('resize', schedule)
    document.addEventListener('scroll', schedule, true)
    window.addEventListener('keydown', guardInput, true)
    window.addEventListener('beforeinput', guardInput, true)
    window.addEventListener('popstate', dispose)
    window.addEventListener('hashchange', dispose)
    window.addEventListener('pagehide', dispose)
    update()
    return { presenting: window[key] === state }
  }

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
    if (existing?.version === 3) {
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
      const text = masterValue(composer())
      if (!normalized(text) && !appMention(composer())) return
      event.preventDefault()
      event.stopImmediatePropagation()
      pushSend(text)
    }

    const onKeyDown = event => {
      if (!event.isTrusted || !bound() || event.key !== 'Enter' || event.shiftKey || event.altKey || event.ctrlKey || event.metaKey
        || event.isComposing || event.keyCode === 229 || stopButton()) return
      const target = composer()
      if (!(target instanceof HTMLElement) || !(event.target instanceof Node) || !(event.target === target || target.contains(event.target))) return
      // Enter in the Provider's app picker selects an app; it is not a send.
      if (mentionLists().length) return
      const text = masterValue(target)
      if (!normalized(text) && !appMention(target)) return
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
      const text = masterValue(target)
      if (!normalized(text) && !appMention(target)) return
      event.preventDefault()
      event.stopImmediatePropagation()
      pushSend(text)
    }

    document.addEventListener('click', onClick, true)
    document.addEventListener('keydown', onKeyDown, true)
    document.addEventListener('submit', onSubmit, true)
    const bridge = {
      version: 3,
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
  const envelope = /^@([^\n]+)\n(turn_id=[^\n]+\n[\s\S]*)$/.exec(promptText.replaceAll('\r\n', '\n'))
  const chatId = typeof request.message_id === 'string' && /^[a-zA-Z0-9-]{1,128}$/.test(request.message_id) ? request.message_id : null
  const chatEnvelope = Boolean(chatId && !request.turn_id && promptText.replaceAll('\r\n', '\n').startsWith(`message_id=${chatId}\n`))
  const appName = envelope?.[1]
  const mention = appMention(composer())
  const selected = Boolean(envelope && mention && mention.name === appName)
  const currentDraft = chatEnvelope ? !mention && sameText(draftText, promptText) : selected && sameText(mention.body, envelope[2])
  const appQuery = appName && !composer()?.querySelector('[app-mention-display-name]') && sameText(draftText, `@${appName}`)
  const selectedEmpty = selected && !normalized(mention.body)
  const canonicalDraft = mention ? `@${mention.name}\n${mention.body}` : draftText
  const draftMarker = /^turn_id=\S+/.exec(mention?.body ?? canonicalDraft.split('\n').slice(1).join('\n').trimStart())?.[0]
  const sameTurnDraft = envelope && draftMarker === envelope[2].split('\n')[0]
  const draftKind = !normalized(draftText) ? 'empty'
    : currentDraft ? 'current'
    : request.preparing === true && (appQuery || selectedEmpty) ? 'preparing'
    : sameTurnDraft && !sameText(canonicalDraft, promptText) ? 'user'
    : request.preparing === true ? 'user'
    : isNiraiEnvelope(canonicalDraft) ? 'nirai'
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
    const marker = chatId ? `message_id=${chatId}` : `turn_id=${request.turn_id}`
    const marked = element => chatId
      ? [element.textContent, element.innerText].some(text => String(text ?? '').replaceAll('\r\n', '\n').trimStart().split('\n')[0].trim() === marker)
      : element.textContent?.includes(marker)
    const users = userMessages()
    const anchors = users.filter(marked)
    const conversationMatches = origin() && Boolean(current())
      && (typeof request.conversation_id !== 'string' || request.conversation_id === current())
    const empty = { ...observation, received: false, provider_turn_key: null, text: '' }
    if (!conversationMatches || anchors.length > 1 || (request.message_id !== undefined && !chatId) || (chatId && request.turn_id)) return empty
    const anchor = anchors[0]
    const providerTurns = [...document.querySelectorAll('[data-turn-key]')]
    const uniqueTurn = key => {
      if (typeof key !== 'string' || !key || key.length > 512) return null
      const matches = providerTurns.filter(element => element.getAttribute('data-turn-key') === key)
      return matches.length === 1 ? matches[0] : null
    }
    const conflictingUser = turn => users.some(element => turn.contains(element) && !marked(element))
    const providerTurn = anchor?.closest('[data-turn-key]')
    const key = providerTurn?.getAttribute('data-turn-key')
    const providerKey = providerTurn && uniqueTurn(key) === providerTurn && !conflictingUser(providerTurn) ? key : null

    // During a new conversation's server reconciliation ChatGPT can remove the
    // user bubble. Main may retain its proven Provider Turn only in this document.
    // Never infer a reply from the latest assistant or the conversation title.
    if (!anchor) {
      const bound = typeof request.conversation_id === 'string' && uniqueTurn(request.provider_turn_key)
      if (!bound || conflictingUser(bound)) return empty
      const text = assistantMessages().filter(element => bound.contains(element))
        .map(element => (element.innerText ?? '').trim()).filter(Boolean).join('\n\n')
      return { ...observation, received: true, provider_turn_key: request.provider_turn_key, text }
    }

    const nextUser = users.find(element => follows(anchor, element))
    const responses = assistantMessages().filter(element =>
      follows(anchor, element) && (providerKey ? providerTurn.contains(element) : !nextUser || follows(element, nextUser)))
    const text = responses
      .map(element => (element.innerText ?? '').trim())
      .filter(Boolean)
      .join('\n\n')
    return { ...observation, received: true, provider_turn_key: providerKey, text }
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
    if (matches && targetComposer && (currentDraft || draftKind === 'preparing' || sameText(draftText, promptText))) return { cleared: setText('') }
    return { cleared: false }
  }
  if (request.operation === 'clear-native') {
    if (targetComposer && sameText(masterValue(targetComposer), request.content)) return { cleared: setText('') }
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
    if (chatEnvelope) {
      if (draftKind === 'current') return { ok: true }
      if (draftKind !== 'empty' || mention) return { ok: false, retryable: false, reason: 'Masterの下書きを保護しました' }
      const started = setText(promptText)
      return { ok: started, started, retryable: false, reason: started ? undefined : '通常会話を入力できません' }
    }
    if (!envelope || appName !== appName.trim()) return { ok: false, retryable: false, reason: '接続名を確認できません' }
    if (draftKind === 'current') return { ok: true }
    if (draftKind === 'user') return { ok: false, retryable: false, reason: 'Masterの下書きを保護しました' }
    if (appQuery) {
      const candidates = mentionLists().flatMap(list => [...list.querySelectorAll('button[data-list-navigation-item]')])
        .filter(button => actionable(button) && [...(button.querySelector('[data-menu-row-content]')?.querySelectorAll('span') ?? [])]
          .filter(span => !span.querySelector('span') && normalized(span.textContent))[0]?.textContent?.trim() === appName)
      if (candidates.length > 1) return { ok: false, retryable: false, reason: `接続「${appName}」の候補が複数あるため送信しません` }
      if (!candidates.length) return { ok: true, pending: true, reason: `ChatGPTで接続「${appName}」を選択できません` }
      candidates[0].click()
      return { ok: true, pending: true, reason: 'アプリ選択を確認しています' }
    }
    if (selectedEmpty) {
      targetComposer.focus()
      const range = document.createRange(); range.selectNodeContents(targetComposer); range.collapse(false)
      const selection = getSelection(); selection?.removeAllRanges(); selection?.addRange(range)
      document.execCommand('insertText', false, `\n${envelope[2]}`)
      targetComposer.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: `\n${envelope[2]}` }))
      return { ok: true, pending: true, reason: 'アプリを保持して本文を入力しています' }
    }
    const started = setText(`@${appName}`)
    return { ok: started, started, pending: true, retryable: true, reason: 'アプリ一覧を確認しています' }
  }
  if (request.operation === 'send') {
    if (!currentDraft) return { ok: false, retryable: false, reason: '送信直前のアプリ選択または下書きを確認できません' }
    const currentSend = sendButton()
    if (!currentSend) return { ok: false, retryable: true, reason: '送信ボタンを確認できません' }
    // A programmatic button click may produce a trusted form submit event.
    // Do not turn our own Hub-authorized send into another Master message.
    const bridge = window.__niraiV2NativeBridge
    if (chatEnvelope) {
      // A normal message has no Task Turn or MCP app selection.
      // Remove stale Task input capture before submitting its own envelope.
      bridge?.dispose?.()
      try { delete window.__niraiV2NativeBridge } catch {}
      currentSend.click()
    } else if (bridge?.version === 3) bridge.send(currentSend, request.turn_id)
    else currentSend.click()
    return { ok: true }
  }
  return { ok: false, reason: '不明な操作です' }
}
