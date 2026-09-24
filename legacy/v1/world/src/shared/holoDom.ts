// This function is also serialized into the isolated ChatGPT page scripts.
// Keep it self-contained so the probe, submission and trusted Stop listener
// always use the same definition of an actionable composer control.
export function holoDomGuards() {
  const stopSelector = 'button[data-testid="stop-button"], button[aria-label="Stop generating"], button[aria-label="生成を停止"]'
  const sendSelector = 'button[data-testid="send-button"], button[data-testid="composer-submit-button"], button#composer-submit-button, button[aria-label="Send prompt"], button[aria-label="Send"], button[aria-label="メッセージを送信"]'
  const isActionable = (element: Element | null): element is HTMLElement => {
    if (!(element instanceof HTMLElement) || !element.isConnected) return false
    if (element.matches(':disabled')) return false
    for (let node: HTMLElement | null = element; node; node = node.parentElement) {
      if (node.hidden || node.inert || node.getAttribute('aria-hidden') === 'true'
        || node.getAttribute('aria-disabled') === 'true') return false
      const style = getComputedStyle(node)
      if (style.display === 'none' || ['hidden', 'collapse'].includes(style.visibility)
        || Number(style.opacity) === 0) return false
    }
    return element.getClientRects().length > 0
  }
  const isStopButton = (element: Element | null): boolean => Boolean(
    element?.matches(stopSelector) && isActionable(element)
  )
  const busy = (): boolean => Array.from(document.querySelectorAll(stopSelector)).some(isActionable)
  const composer = (): Element | null => document.querySelector('#prompt-textarea')
    ?? document.querySelector('textarea[placeholder]')
    ?? document.querySelector('[contenteditable="true"][data-virtualkeyboard="true"]')
    ?? document.querySelector('[contenteditable="true"]')
  const composerValue = (): string => {
    const target = composer()
    if (!target) return ''
    const value = (target as HTMLTextAreaElement).value
    return typeof value === 'string' ? value : (target.textContent ?? '')
  }
  const composerHasDraft = (): boolean => composerValue().replace(/[\s\u200B\uFEFF]+/g, '').length > 0
  // ChatGPT can reuse or transition the composer control while a response is
  // generating. A send/interrupt click with a Master draft must not be
  // reinterpreted as an explicit cancellation of the whole Nirai Workflow.
  // Require an actual pointer click on Stop while the composer is empty.
  const isExplicitMasterStop = (element: Element | null, clickDetail: number): boolean => (
    isStopButton(element) && !composerHasDraft() && Number.isFinite(clickDetail) && clickDetail > 0
  )
  const sendButton = (): Element | undefined => Array.from(document.querySelectorAll(sendSelector))
    .find((element) => !element.matches(stopSelector) && isActionable(element))
  return { isActionable, isStopButton, isExplicitMasterStop, busy, composer, composerHasDraft, sendButton }
}
