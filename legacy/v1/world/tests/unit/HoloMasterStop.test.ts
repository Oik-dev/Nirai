import { afterEach, describe, expect, it, vi } from 'vitest'
import { holoDomGuards } from '../../src/shared/holoDom'

afterEach(() => {
  vi.unstubAllGlobals()
})

function setup(draft: string) {
  class FakeElement {
    isConnected = true
    parentElement = null
    hidden = false
    inert = false
    textContent = ''
    getAttribute() { return null }
    getClientRects() { return [{}] }
    matches(_selector: string) { return false }
  }
  class FakeHTMLElement extends FakeElement {}
  class Composer extends FakeHTMLElement {
    value = draft
  }
  class StopButton extends FakeHTMLElement {
    matches(selector: string) {
      if (selector === ':disabled') return false
      return selector.includes('data-testid="stop-button"')
    }
  }

  const composer = new Composer()
  const stopButton = new StopButton()
  vi.stubGlobal('Element', FakeElement)
  vi.stubGlobal('HTMLElement', FakeHTMLElement)
  vi.stubGlobal('getComputedStyle', () => ({ display: 'block', visibility: 'visible', opacity: '1' }))
  vi.stubGlobal('document', {
    querySelector: (selector: string) => selector === '#prompt-textarea' ? composer : null,
    querySelectorAll: () => [stopButton]
  })
  return { dom: holoDomGuards(), stopButton: stopButton as unknown as Element }
}

describe('Holo Master Stop discrimination', () => {
  it('does not reinterpret a follow-up draft on a stop-shaped control as Workflow cancellation', () => {
    const { dom, stopButton } = setup('追加でこれも見て')
    expect(dom.isStopButton(stopButton)).toBe(true)
    expect(dom.composerHasDraft()).toBe(true)
    expect(dom.isExplicitMasterStop(stopButton, 1)).toBe(false)
  })

  it('accepts an actual pointer Stop only when the composer is empty', () => {
    const { dom, stopButton } = setup('')
    expect(dom.composerHasDraft()).toBe(false)
    expect(dom.isExplicitMasterStop(stopButton, 1)).toBe(true)
  })

  it('does not turn a keyboard or synthesized click into Master Stop', () => {
    const { dom, stopButton } = setup('')
    expect(dom.isExplicitMasterStop(stopButton, 0)).toBe(false)
  })
})
