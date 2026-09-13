import { afterEach, describe, expect, it, vi } from 'vitest'
import { buildHoloAutoResumeSubmissionScript } from '../../src/main/holo/holoWeb'

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

function composer(initialDraft = '', messages: string[] = []) {
  class Element {
    innerText = initialDraft
    textContent = initialDraft
    focus() {}
    dispatchEvent() {}
    closest() { return null }
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
  vi.stubGlobal('HTMLElement', Element)
  vi.stubGlobal('HTMLTextAreaElement', TextArea)
  vi.stubGlobal('HTMLButtonElement', Button)
  vi.stubGlobal('HTMLFormElement', class {})
  vi.stubGlobal('location', new URL('https://chatgpt.com/c/owner'))
  vi.stubGlobal('document', {
    querySelector: (selector: string) => {
      if (selector === '#prompt-textarea') return target
      if (selector.includes('send-button')) return button
      return null
    },
    querySelectorAll: () => messages.map((textContent) => ({ textContent }))
  })
  return { target, button }
}

async function submit(text: string, key: string, deadline?: number) {
  // The optional deadline is an absolute host clock value, so a throttled script
  // cannot begin sending after the host has already timed out.
  const build = buildHoloAutoResumeSubmissionScript as (
    text: string, key: string, url: string, deadline?: number
  ) => string
  return new Function('return ' + build(text, key, 'https://chatgpt.com/c/owner', deadline))()
}

describe('Auto Resume submission behavior', () => {
  it('does not confuse a longer trigger key with delivery of its prefix', async () => {
    const { button } = composer('Master draft', ['[Nirai Auto Resume]\nTrigger Key: T-1:AS-1:waiting_for_master:REQ-10\nTask ID: T-1'])
    expect(await submit('resume', 'T-1:AS-1:waiting_for_master:REQ-1')).toEqual({ status: 'draft_present' })
    expect(button.click).not.toHaveBeenCalled()
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
})
