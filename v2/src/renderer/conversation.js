// Hub owns conversations and saved timestamps. Only drafts and reading positions
// belong to this view; neither camera focus nor Task selection chooses an audience.
window.createUsualConversationUI = function ({ state, send, escapeHtml, timeLabel, residentName }) {
  const panel = document.getElementById('usualChat')
  const channels = document.getElementById('usualChatChannels')
  const messages = document.getElementById('usualChatMessages')
  const form = document.getElementById('usualChatForm')
  const input = document.getElementById('usualChatInput')
  const handle = document.getElementById('usualChatResize')
  const widthHandle = document.getElementById('usualChatResizeWidth')
  const cornerHandle = document.getElementById('usualChatResizeCorner')
  const drafts = new Map()
  const reading = new Map()
  let channel = 'say'
  let signature = null
  let channelSignature = null
  let resizeDrag = null
  let preferredWidth = null
  let preferredHeight = null
  let scrollRevision = 0

  function availableResidents() {
    return (state().snapshot.residents ?? []).filter(resident => !['master', 'control'].includes(resident.id))
  }

  function label() {
    return channel === 'say' ? 'Say' : residentName(channel.slice('whisper:'.length))
  }

  function saveView() {
    cancelResize()
    drafts.set(channel, input.value)
    if (!panel.hidden) reading.set(channel, readPosition())
  }

  function setChannel(next) {
    if (next === channel) return
    saveView()
    channel = next
    input.value = drafts.get(channel) ?? ''
    signature = null
    render()
    resizeComposer()
  }

  function resizeComposer() {
    input.style.height = 'auto'
    input.style.height = `${Math.min(input.scrollHeight, 100)}px`
  }

  function readPosition() {
    const viewport = messages.getBoundingClientRect()
    const anchor = [...messages.querySelectorAll('[data-message-id]')].find(article => article.getBoundingClientRect().bottom > viewport.top)
    return {
      top: messages.scrollTop,
      latest: messages.scrollHeight - messages.clientHeight - messages.scrollTop < 36,
      anchor: anchor?.dataset.messageId,
      offset: anchor ? anchor.getBoundingClientRect().top - viewport.top : 0,
    }
  }

  function restorePosition(position) {
    if (!position || position.latest) { messages.scrollTop = messages.scrollHeight; return }
    const anchor = position.anchor && messages.querySelector(`[data-message-id="${CSS.escape(position.anchor)}"]`)
    if (anchor) messages.scrollTop += anchor.getBoundingClientRect().top - messages.getBoundingClientRect().top - position.offset
    else messages.scrollTop = position.top
  }

  function localDate(value) {
    const date = new Date(value)
    if (!Number.isFinite(date.getTime())) return ''
    return `${date.getFullYear()}/${String(date.getMonth() + 1).padStart(2, '0')}/${String(date.getDate()).padStart(2, '0')}`
  }

  function render() {
    const current = state()
    const residents = availableResidents()
    if (channel !== 'say' && !residents.some(resident => `whisper:${resident.id}` === channel)) setChannel('say')
    const nextChannelSignature = JSON.stringify([channel, residents.map(resident => [resident.id, resident.display_name])])
    if (channelSignature !== nextChannelSignature) {
      const focusedChannel = channels.contains(document.activeElement) ? document.activeElement?.dataset.channel : null
      channels.innerHTML = [{ id: 'say', name: 'Say' }, ...residents.map(resident => ({ id: `whisper:${resident.id}`, name: resident.display_name ?? resident.id }))]
        .map(item => `<button type="button" role="tab" data-channel="${escapeHtml(item.id)}" aria-selected="${item.id === channel}" tabindex="${item.id === channel ? '0' : '-1'}" aria-controls="usualChatMessages" class="usual-chat-channel${item.id === channel ? ' is-selected' : ''}">${escapeHtml(item.name)}</button>`).join('')
      channelSignature = nextChannelSignature
      if (focusedChannel) channels.querySelector(`[data-channel="${CSS.escape(focusedChannel)}"]`)?.focus({ preventScroll: true })
    }
    const targetName = label()
    input.setAttribute('aria-label', `${targetName}への発言`)
    messages.setAttribute('aria-label', `${targetName}の履歴`)
    const disabled = !current.connected || current.locked
    input.disabled = disabled
    form.querySelector('.send-button').disabled = disabled || !input.value.trim()
    if (panel.hidden) return
    const conversation = (current.snapshot.conversations ?? []).find(item => item.kind === (channel === 'say' ? 'say' : 'whisper')
      && (channel === 'say' || item.resident_id === channel.slice('whisper:'.length)))
    const history = conversation ? (current.snapshot.messages ?? []).filter(message => message.conversation_id === conversation.id && message.sender !== 'control') : []
    const responseStates = (current.snapshot.chat_responses ?? []).filter(response => history.some(message => message.id === response.message_id))
    const nextSignature = JSON.stringify([channel, history, responseStates])
    if (signature === nextSignature) return
    const previous = signature === null ? reading.get(channel) : readPosition()
    const activeArticle = messages.contains(document.activeElement) ? document.activeElement?.closest('[data-message-id]')?.dataset.messageId : null
    const dates = new Set(history.map(message => localDate(message.created_at)).filter(Boolean))
    let lastDate = null
    messages.replaceChildren()
    for (const message of history) {
      const date = localDate(message.created_at)
      if (dates.size > 1 && date && date !== lastDate) {
        const separator = document.createElement('div')
        separator.className = 'usual-chat-date'
        separator.textContent = date
        messages.appendChild(separator)
      }
      lastDate = date
      const article = document.createElement('article')
      article.className = `usual-message ${message.sender === 'master' ? 'master' : message.sender === 'system' ? 'system' : 'agent'}`
      article.dataset.messageId = message.id
      const who = message.sender === 'master' ? 'Master' : message.sender === 'system' ? 'Nirai' : residentName(message.sender)
      article.innerHTML = `<div class="usual-message-meta"><strong>${escapeHtml(who)}</strong><time datetime="${escapeHtml(message.created_at)}">${escapeHtml(timeLabel(message.created_at))}</time></div><div class="usual-message-text">${escapeHtml(message.content)}</div>`
      for (const response of responseStates.filter(item => item.message_id === message.id && item.state !== 'completed')) {
        const status = document.createElement('small')
        status.className = 'usual-response-status'
        status.dataset.responseState = response.state
        const text = response.state === 'running' ? '応答中…' : response.state === 'pending' ? '応答待ち'
          : response.state === 'interrupted' ? '中断' : /ログイン/.test(response.error ?? '') ? 'ログインが必要'
            : /未接続|接続されていません|unavailable|not connected|provider|capability/i.test(response.error ?? '') ? '未接続' : '応答に失敗'
        status.textContent = `${residentName(response.resident_id)} · ${text}`
        if (response.error) status.title = response.error
        article.appendChild(status)
      }
      messages.appendChild(article)
    }
    signature = nextSignature
    const revision = ++scrollRevision
    requestAnimationFrame(() => {
      if (revision !== scrollRevision || panel.hidden) return
      restorePosition(previous)
      if (activeArticle) messages.querySelector(`[data-message-id="${CSS.escape(activeArticle)}"]`)?.focus({ preventScroll: true })
    })
  }

  function heightBounds() {
    return { min: 180, max: Math.max(180, Math.min(innerHeight * .75, innerHeight - 96)) }
  }

  function widthBounds() {
    const left = parseFloat(getComputedStyle(panel).left)
    const margin = Number.isFinite(left) ? Math.max(0, left) : 20
    const max = Math.max(1, innerWidth - margin * 2)
    return { min: Math.min(280, max), max }
  }

  function applySize(next = {}, remember = false) {
    const width = widthBounds(), height = heightBounds()
    const clamp = (value, bounds) => Math.max(bounds.min, Math.min(bounds.max, value))
    const box = panel.getBoundingClientRect()
    const initialWidth = parseFloat(getComputedStyle(panel).getPropertyValue('--usual-chat-initial-width'))
    if (!Number.isFinite(preferredWidth) || preferredWidth <= 0) preferredWidth = Number.isFinite(initialWidth) && initialWidth > 0 ? initialWidth : 528
    if (!Number.isFinite(preferredHeight) || preferredHeight <= 0) preferredHeight = box.height > 0 ? box.height : Math.min(340, innerHeight * .5)
    if (remember && Number.isFinite(next.width)) preferredWidth = clamp(next.width, width)
    if (remember && Number.isFinite(next.height)) preferredHeight = clamp(next.height, height)
    const position = panel.hidden ? null : readPosition()
    const appliedWidth = clamp(preferredWidth, width), appliedHeight = clamp(preferredHeight, height)
    panel.style.width = `${appliedWidth}px`
    panel.style.height = `${appliedHeight}px`
    resizeComposer()
    if (position) restorePosition(position)
    for (const [grip, bounds, value] of [[handle, height, appliedHeight], [widthHandle, width, appliedWidth]]) {
      grip.setAttribute('aria-valuemin', String(Math.round(bounds.min)))
      grip.setAttribute('aria-valuemax', String(Math.round(bounds.max)))
      grip.setAttribute('aria-valuenow', String(Math.round(value)))
      grip.setAttribute('aria-valuetext', `${Math.round(value)}ピクセル`)
    }
    cornerHandle.setAttribute('aria-label', `会話窓の幅と高さ、幅${Math.round(appliedWidth)}、高さ${Math.round(appliedHeight)}ピクセル`)
  }

  function cancelResize() {
    const drag = resizeDrag
    resizeDrag = null
    if (drag?.handle.hasPointerCapture(drag.id)) drag.handle.releasePointerCapture(drag.id)
  }

  channels.addEventListener('click', event => {
    const tab = event.target.closest('[data-channel]')
    if (tab) setChannel(tab.dataset.channel)
  })
  channels.addEventListener('keydown', event => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
    const tabs = [...channels.querySelectorAll('[data-channel]')]
    const index = tabs.findIndex(tab => tab === document.activeElement)
    if (index < 0) return
    event.preventDefault()
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length
    const key = tabs[next].dataset.channel
    setChannel(key)
    channels.querySelector(`[data-channel="${CSS.escape(key)}"]`)?.focus({ preventScroll: true })
  })
  input.addEventListener('input', () => { drafts.set(channel, input.value); resizeComposer(); render() })
  input.addEventListener('keydown', event => {
    if (event.key !== 'Enter' || event.shiftKey || event.isComposing || event.keyCode === 229) return
    event.preventDefault()
    form.requestSubmit()
  })
  messages.addEventListener('scroll', () => {
    if (!panel.hidden) reading.set(channel, readPosition())
  }, { passive: true })
  form.addEventListener('submit', async event => {
    event.preventDefault()
    const content = input.value
    const sentChannel = channel
    if (!content.trim() || input.disabled) return
    const payload = { channel: sentChannel === 'say' ? 'say' : 'whisper', content,
      ...(sentChannel === 'say' ? {} : { resident_id: sentChannel.slice('whisper:'.length) }) }
    const accepted = await send('SendChatMessage', payload)
    if (!accepted) return
    if (drafts.get(sentChannel) === content) drafts.delete(sentChannel)
    if (channel === sentChannel && input.value === content) {
      input.value = ''
      resizeComposer()
    }
    render()
  })
  for (const [grip, horizontal, vertical] of [[handle, false, true], [widthHandle, true, false], [cornerHandle, true, true]]) {
    grip.addEventListener('pointerdown', event => {
      if (event.button !== 0 || event.isPrimary === false || panel.hidden || panel.inert) return
      cancelResize()
      const box = panel.getBoundingClientRect()
      resizeDrag = { handle: grip, id: event.pointerId, x: event.clientX, y: event.clientY, width: box.width, height: box.height }
      grip.setPointerCapture(event.pointerId)
      grip.focus({ preventScroll: true })
      event.preventDefault()
    })
    grip.addEventListener('pointermove', event => {
      if (resizeDrag?.handle !== grip || resizeDrag.id !== event.pointerId) return
      if (panel.hidden || panel.inert) { cancelResize(); return }
      applySize({ ...(horizontal ? { width: resizeDrag.width + event.clientX - resizeDrag.x } : {}),
        ...(vertical ? { height: resizeDrag.height + resizeDrag.y - event.clientY } : {}) }, true)
    })
    const finish = event => {
      if (resizeDrag?.handle === grip && resizeDrag.id === event.pointerId) cancelResize()
    }
    grip.addEventListener('pointerup', finish)
    grip.addEventListener('pointercancel', finish)
    grip.addEventListener('lostpointercapture', finish)
    grip.addEventListener('keydown', event => {
      const horizontalKey = horizontal && ['ArrowLeft', 'ArrowRight'].includes(event.key)
      const verticalKey = vertical && ['ArrowUp', 'ArrowDown'].includes(event.key)
      if (!horizontalKey && !verticalKey && !['Home', 'End'].includes(event.key)) return
      event.preventDefault()
      const box = panel.getBoundingClientRect()
      const width = widthBounds(), height = heightBounds()
      applySize({ ...(horizontal && !verticalKey ? { width: event.key === 'Home' ? width.min : event.key === 'End' ? width.max : box.width + (event.key === 'ArrowRight' ? 24 : -24) } : {}),
        ...(vertical && !horizontalKey ? { height: event.key === 'Home' ? height.min : event.key === 'End' ? height.max : box.height + (event.key === 'ArrowUp' ? 24 : -24) } : {}) }, true)
    })
  }
  window.addEventListener('nirai:world-focus', event => {
    const id = event.detail?.resident_id
    if (!panel.hidden && id && input.value.length === 0 && availableResidents().some(resident => resident.id === id)) setChannel(`whisper:${id}`)
  })
  window.addEventListener('resize', () => {
    cancelResize()
    applySize()
  })
  window.addEventListener('blur', cancelResize)
  document.addEventListener('visibilitychange', () => { if (document.hidden) cancelResize() })
  return {
    render, saveView,
    activate() {
      signature = null
      render()
      resizeComposer()
      applySize()
    },
  }
}
