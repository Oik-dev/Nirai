import { afterEach, describe, expect, it, vi } from 'vitest'
import { buildHoloAutoResumeSubmissionScript, buildHoloAutoResumeCancellationScript, buildHoloAutoResumePrompt } from '../../src/main/holo/holoWeb'

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

function composer(
  initialDraft = '',
  messages: string[] = [],
  stopState: 'none' | 'hidden' | 'visible' = 'none'
) {
  class Element {
    isConnected = true
    parentElement = null
    inert = false
    matches(selector: string) { return selector === ':disabled' && this instanceof Button && this.disabled }
    innerText = initialDraft
    textContent = initialDraft
    hidden = false
    focus() {}
    dispatchEvent() {}
    closest() { return null }
    getAttribute() { return null }
    getClientRects() { return this.hidden ? [] : [{}] }
  }
  class TextArea extends Element {
    value = initialDraft
  }
  class Button extends Element {
    disabled = false
    click = vi.fn()
  }
  const target = new TextArea()
  const button = new Button()
  const stopButton = stopState === 'none' ? null : new Button()
  if (stopButton && stopState === 'hidden') stopButton.hidden = true
  vi.stubGlobal('HTMLElement', Element)
  vi.stubGlobal('HTMLTextAreaElement', TextArea)
  vi.stubGlobal('HTMLButtonElement', Button)
  vi.stubGlobal('HTMLFormElement', class {})
  vi.stubGlobal('getComputedStyle', () => ({ display: 'block', visibility: 'visible', opacity: '1' }))
  vi.stubGlobal('location', new URL('https://chatgpt.com/c/owner'))
  vi.stubGlobal('document', {
    querySelector: (selector: string) => {
      if (selector === '#prompt-textarea') return target
      if (selector.includes('stop-button') || selector.includes('Stop generating') || selector.includes('生成を停止')) return stopButton
      if (selector.includes('send-button')) return button
      return null
    },
    querySelectorAll: (selector: string) => selector.includes('stop-button')
      ? (stopButton ? [stopButton] : []) : selector.includes('send-button')
        ? [button] : messages.map((textContent) => ({ textContent }))
  })
  return { target, button, stopButton }
}

async function submit(text: string, deliveryId: string, deadline?: number) {
  // The optional deadline is an absolute host clock value, so a throttled script
  // cannot begin sending after the host has already timed out.
  return new Function('return ' + buildHoloAutoResumeSubmissionScript(
    `${text}\n再開ID: ${deliveryId}`, 'https://chatgpt.com/c/owner', deadline, undefined, deliveryId
  ))()
}

describe('Auto Resume submission behavior', () => {
  it('confirms the sent message even when browser storage is unavailable', async () => {
    vi.useFakeTimers()
    const messages: string[] = []
    const { target, button } = composer('', messages)
    vi.stubGlobal('localStorage', {
      getItem: () => { throw new Error('storage unavailable') },
      setItem: () => { throw new Error('storage unavailable') }
    })
    button.click.mockImplementation(() => { messages.push(target.value); target.value = '' })
    const pending = submit('resume', 'legacy-key')
    await vi.advanceTimersByTimeAsync(100)
    expect(await pending).toEqual({ status: 'submitted' })
    expect(button.click).toHaveBeenCalledTimes(1)
  })

  it.each([false, true])('cleans only its exact minimal Auto Resume draft (Master edited=%s)', (edited) => {
    const trigger = {
      task_id: 'WF-1', reason: 'workflow_stalled' as const, request_id: 'REV-1',
      dive_session_id: 'DIVE-1', workflow_id: '1', delivery_id: 'delivery-1'
    }
    const prompt = buildHoloAutoResumePrompt(trigger)
    expect(prompt).toBe('[Nirai Auto Resume]\n\n以下を続行してください。\n\nDive Session ID: DIVE-1\nWorkflow ID: 1\n再開ID: delivery-1')
    const draft = prompt + (edited ? '\nMaster: keep this note' : '')
    const { target } = composer(draft)
    vi.stubGlobal('window', {})
    new Function(buildHoloAutoResumeCancellationScript(trigger.task_id, [prompt]))()
    expect(target.value).toBe(edited ? draft : '')
  })
  it('rechecks Stop state immediately before clicking Send', async () => {
    const { target, button, stopButton } = composer('', [], 'hidden')
    target.dispatchEvent = () => { stopButton!.hidden = false }
    expect(await submit('resume', 'T-BECAME-BUSY')).toEqual({ status: 'busy' })
    expect(button.click).not.toHaveBeenCalled()
  })

  it('does not use a detached composer after a SPA replaces it during send-button wait', async () => {
    vi.useFakeTimers()
    const { target, button } = composer()
    button.disabled = true
    const pending = submit('resume', 'T-REPLACED')
    target.isConnected = false
    button.disabled = false
    await vi.advanceTimersByTimeAsync(100)
    expect(await pending).toEqual({ status: 'not_ready' })
    expect(button.click).not.toHaveBeenCalled()
  })

  it('does not send into another Conversation after SPA navigation during send-button wait', async () => {
    vi.useFakeTimers()
    const { button } = composer()
    button.disabled = true
    const pending = submit('resume', 'T-NAVIGATED')
    vi.stubGlobal('location', new URL('https://chatgpt.com/c/other'))
    button.disabled = false
    await vi.advanceTimersByTimeAsync(100)
    expect(await pending).toEqual({ status: 'not_ready' })
    expect(button.click).not.toHaveBeenCalled()
  })

  it('does not bypass a disabled send button by submitting its form', async () => {
    vi.useFakeTimers()
    const { button } = composer()
    button.disabled = true
    const pending = submit('resume', 'T-DISABLED')
    await vi.advanceTimersByTimeAsync(2100)
    expect(await pending).toEqual({ status: 'not_ready' })
    expect(button.click).not.toHaveBeenCalled()
  })

  it('honors a Master Stop marker immediately, scoped to the owner Conversation', async () => {
    const { target, button } = composer()
    Object.assign(document, { documentElement: { getAttribute: () => 'https://chatgpt.com/c/owner' } })
    expect(await submit('resume', 'T-STOPPED')).toEqual({ status: 'not_ready' })
    expect(target.value).toBe('')
    expect(button.click).not.toHaveBeenCalled()
  })

  it('preserves an unrelated Master draft even when an older Auto Resume exists in the transcript', async () => {
    const { button } = composer('Master draft', ['[Nirai Auto Resume]\n\n以下を続行してください。'])
    expect(await submit('resume', 'legacy-key')).toEqual({ status: 'draft_present' })
    expect(button.click).not.toHaveBeenCalled()
  })

  it('deduplicates after reload even when older copies of the prompt are no longer visible', async () => {
    const deliveryId = 'delivery-1'
    const { button } = composer('', ['resume\n再開ID: delivery-1'])
    expect(await submit('resume', deliveryId)).toEqual({
      status: 'submitted', duplicate: true
    })
    expect(button.click).not.toHaveBeenCalled()
  })

  it('allows a new delivery attempt for the same minimal prompt after an older delivery was confirmed', async () => {
    vi.useFakeTimers()
    const { target, button } = composer('', ['resume\n再開ID: delivery-old'])
    const pending = submit('resume', 'delivery-new')
    await Promise.resolve()
    expect(button.click).toHaveBeenCalledTimes(1)
    expect(target.value).toBe('resume\n再開ID: delivery-new')
    await vi.advanceTimersByTimeAsync(6000)
    expect(await pending).toEqual({ status: 'not_ready' })
  })

  it('does not treat a longer delivery ID sharing the same prefix as a receipt', async () => {
    vi.useFakeTimers()
    const { button } = composer('', ['resume\n再開ID: delivery-12'])
    const pending = submit('resume', 'delivery-1')
    await vi.advanceTimersByTimeAsync(6000)
    expect(await pending).toEqual({ status: 'not_ready' })
    expect(button.click).toHaveBeenCalledTimes(1)
  })

  it('does not accept an Assistant quotation as delivery of a user message', async () => {
    vi.useFakeTimers()
    const { button } = composer()
    const select = document.querySelectorAll.bind(document)
    Object.assign(document, { querySelectorAll: (selector: string) =>
      selector === '[data-message-author-role="assistant"]'
        ? [{ textContent: 'resume\n再開ID: delivery-1' }] : select(selector) })
    const pending = submit('resume', 'delivery-1')
    await vi.advanceTimersByTimeAsync(6000)
    expect(await pending).toEqual({ status: 'not_ready' })
    expect(button.click).toHaveBeenCalledTimes(1)
  })

  it('preserves an Auto Resume draft that Master has edited', async () => {
    vi.useFakeTimers()
    const prompt = '[Nirai Auto Resume]\nTrigger Key: T-1:AS-1:done:-\nTask ID: T-1'
    const modified = prompt + '\nMaster correction: stop here'
    const { target, button } = composer(modified)
    const pending = submit(prompt, 'T-1:AS-1:done:-')
    await vi.advanceTimersByTimeAsync(6000)
    expect(await pending).toEqual({ status: 'draft_present' })
    expect(target.value).toBe(modified)
    expect(button.click).not.toHaveBeenCalled()
  })

  it('does not start an expired submission after the host has timed out', async () => {
    vi.useFakeTimers()
    const { target, button } = composer()
    const pending = submit('resume', 'T-EXPIRED', Date.now() - 1)
    await vi.advanceTimersByTimeAsync(6000)
    expect(await pending).toEqual({ status: 'not_ready' })
    expect(target.value).toBe('')
    expect(button.click).not.toHaveBeenCalled()
  })

  it('stops a delayed send loop when its host deadline expires', async () => {
    vi.useFakeTimers()
    const { button } = composer()
    button.disabled = true
    const pending = submit('resume', 'T-DELAYED', Date.now() + 50)
    button.disabled = false
    await vi.advanceTimersByTimeAsync(6000)
    expect(await pending).toEqual({ status: 'not_ready' })
    expect(button.click).not.toHaveBeenCalled()
  })

  it('ignores a stale hidden Stop button left in the DOM after generation ends', async () => {
    vi.useFakeTimers()
    const { button } = composer('', [], 'hidden')
    const pending = submit('resume', 'T-HIDDEN-STOP', Date.now() + 250)
    await Promise.resolve()
    expect(button.click).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1000)
    expect(await pending).toEqual({ status: 'not_ready' })
  })

  it('still blocks Auto Resume while a visible Stop button is active', async () => {
    const { button } = composer('', [], 'visible')
    expect(await submit('resume', 'T-VISIBLE-STOP')).toEqual({ status: 'busy' })
    expect(button.click).not.toHaveBeenCalled()
  })
})
