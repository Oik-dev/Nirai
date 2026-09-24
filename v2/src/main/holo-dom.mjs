// The actionable-control checks reuse the v1 Holo DOM boundary; no v1 runtime,
// workflow, queue, storage or execution authority is imported into v2.
export function holoPageOperation(request) {
  const actionable = element => {
    if (!(element instanceof HTMLElement) || !element.isConnected || element.matches(':disabled')) return false
    for (let node = element; node; node = node.parentElement) {
      const style = getComputedStyle(node)
      if (node.hidden || node.inert || node.getAttribute('aria-hidden') === 'true' || node.getAttribute('aria-disabled') === 'true'
        || style.display === 'none' || ['hidden', 'collapse'].includes(style.visibility) || Number(style.opacity) === 0) return false
    }
    return element.getClientRects().length > 0
  }
  const stopSelector = 'button[data-testid="stop-button"],button[aria-label="Stop generating"],button[aria-label="生成を停止"]'
  const sendSelector = 'button[data-testid="send-button"],button[data-testid="composer-submit-button"],button#composer-submit-button,button[aria-label="Send prompt"],button[aria-label="メッセージを送信"]'
  const stop = [...document.querySelectorAll(stopSelector)].find(actionable)
  const composer = [...document.querySelectorAll('#prompt-textarea,textarea[placeholder],[contenteditable="true"][data-virtualkeyboard="true"]')].find(actionable)
  const send = [...document.querySelectorAll(sendSelector)].find(element => !element.matches(stopSelector) && actionable(element))
  const value = () => (composer instanceof HTMLTextAreaElement ? composer.value : composer?.innerText ?? composer?.textContent ?? '').replaceAll('\r\n', '\n')
  const normalized = text => (text ?? '').replace(/[\s\u200B\uFEFF]+/g, ' ').trim()
  const sameText = (a, b) => normalized(a) === normalized(b)
  const isNiraiEnvelope = text => {
    const lines = String(text ?? '').replaceAll('\r\n', '\n').split('\n').map(line => line.trim()).filter(Boolean)
    return Boolean(lines[0]?.startsWith('@') && lines[1]?.startsWith('turn_id='))
  }
  const current = /^\/(?:g\/[^/]+\/)?c\/([a-zA-Z0-9-]+)\/?$/.exec(location.pathname)?.[1] ?? null
  const origin = location.origin === 'https://chatgpt.com'
  const login = [...document.querySelectorAll('button,a')].some(element => actionable(element) && /^(?:Log in|Sign in|ログイン)$/.test(element.textContent?.trim() ?? ''))
  const draftText = value()
  const promptText = typeof request.prompt === 'string' ? request.prompt : ''
  const draftKind = !normalized(draftText) ? 'empty'
    : promptText && sameText(draftText, promptText) ? 'current'
    : isNiraiEnvelope(draftText) ? 'nirai'
    : 'user'
  const userDraft = draftKind === 'user'
  const assistants = [...document.querySelectorAll('[data-message-author-role="assistant"]')]
  const lastAssistant = assistants.at(-1)
  const completionButtons = [...document.querySelectorAll(
    '[data-testid="copy-turn-action-button"],button[aria-label="Copy"],button[aria-label="コピー"]'
  )]
  const observation = { url: location.href, conversation_id: current, busy: Boolean(stop), draft: userDraft,
    state: !origin ? 'unavailable' : login || !composer ? 'blocked' : stop ? 'busy' : userDraft ? 'blocked' : 'ready',
    reason: !origin ? 'Holoのログイン画面または対象外のページです' : login ? 'ChatGPTへログインしてください' : !composer ? '入力欄を確認できません' : stop ? 'Holoが応答を生成しています' : userDraft ? '入力中の下書きがあります' : 'Holo接続の準備ができています',
    retryable: origin && !login && !stop && !userDraft,
    assistant_count: assistants.length,
    assistant_complete: Boolean(lastAssistant && !stop && completionButtons.length >= assistants.length),
    last_assistant_text: (lastAssistant?.innerText ?? lastAssistant?.textContent ?? '').trim() }
  if (request.operation === 'observe') return { ...observation, ready_to_send: Boolean(composer && send && !stop && draftKind === 'current') }
  const matches = origin && (request.conversation_id === null ? current === null && location.pathname === '/' : current === request.conversation_id)
  if (request.operation === 'evidence') {
    const marker = `turn_id=${request.turn_id}`
    return { ...observation, received: origin && Boolean(current) && (request.conversation_id === null || current === request.conversation_id)
      && [...document.querySelectorAll('[data-message-author-role="user"]')].some(element => element.textContent?.includes(marker)) }
  }
  const setText = text => {
    composer.focus()
    if (composer instanceof HTMLTextAreaElement) {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(composer, text)
      composer.dispatchEvent(new Event('input', { bubbles: true }))
    } else {
      const range = document.createRange(); range.selectNodeContents(composer)
      const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range)
      document.execCommand('insertText', false, text)
      composer.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: text }))
    }
  }
  if (request.operation === 'clear') {
    if (matches && composer && sameText(value(), request.prompt)) { setText(''); return { cleared: true } }
    return { cleared: false }
  }
  if (request.operation === 'stop') {
    if (!matches) return { stopped: false }
    if (stop) { stop.click(); return { stopped: false, requested: true } }
    return { stopped: true }
  }
  if (!matches || login || !composer || stop) return { ok: false, retryable: !login && !userDraft, reason: '対象会話・ログイン・生成状態を再確認してください' }
  if (request.operation === 'fill') {
    if (draftKind === 'current') return { ok: true }
    if (draftKind === 'user') return { ok: false, retryable: false, reason: '下書きを保護しました' }
    setText(request.prompt)
    return { ok: sameText(value(), request.prompt), retryable: true, reason: '入力結果を確認できません' }
  }
  if (request.operation === 'send') {
    if (!sameText(value(), request.prompt)) return { ok: false, retryable: false, reason: '送信直前に下書きが変更されました' }
    if (!send) return { ok: false, retryable: true, reason: '送信ボタンを確認できません' }
    send.click()
    return { ok: true }
  }
  return { ok: false, reason: '不明な操作です' }
}
