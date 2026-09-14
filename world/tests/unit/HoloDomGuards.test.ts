import { afterEach, describe, expect, it, vi } from 'vitest'
import { holoDomGuards } from '../../src/shared/holoDom'

const sent = vi.hoisted(() => vi.fn())
vi.mock('electron', () => ({ ipcRenderer: { send: sent } }))
afterEach(() => { vi.unstubAllGlobals(); vi.resetModules(); sent.mockClear() })

function page() {
  class Element {
    isConnected = true
    hidden = false
    inert = false
    disabled = false
    parentElement: Element | null = null
    kind = 'stop'
    attributes = new Map<string, string>()
    style = { display: 'block', visibility: 'visible', opacity: '1' }
    rects: unknown[] = [{}]
    getAttribute(key: string) { return this.attributes.get(key) ?? null }
    setAttribute(key: string, value: string) { this.attributes.set(key, value) }
    removeAttribute(key: string) { this.attributes.delete(key) }
    getClientRects() { return this.rects }
    closest() { return this }
    contains(element: Element) { return element === this }
    matches(selector: string) {
      if (selector === ':disabled') return this.disabled
      return this.kind === 'stop' ? selector.includes('stop-button') : selector.includes('send-button')
    }
  }
  const first = new Element()
  const second = new Element()
  const root = new Element()
  const handlers = new Map<string, (event: any) => void>()
  vi.stubGlobal('Element', Element)
  vi.stubGlobal('HTMLElement', Element)
  vi.stubGlobal('getComputedStyle', (element: Element) => element.style)
  vi.stubGlobal('location', new URL('https://chatgpt.com/c/owner'))
  vi.stubGlobal('document', {
    documentElement: root,
    querySelectorAll: (selector: string) => [first, second].filter((item) => item.matches(selector)),
    querySelector: () => root,
    addEventListener: (name: string, handler: (event: any) => void) => handlers.set(name, handler)
  })
  return { first, second, root, handlers, Element }
}

describe('shared ChatGPT composer guards', () => {
  it.each(['hidden', 'aria-hidden', 'disabled', 'aria-disabled', 'display', 'visibility', 'collapse', 'opacity', 'rects', 'detached', 'ancestor'])('ignores an inactive Stop (%s) while still finding a second active Stop', (condition) => {
    const { first, second, Element } = page()
    if (condition === 'hidden') first.hidden = true
    if (condition === 'aria-hidden' || condition === 'aria-disabled') first.setAttribute(condition, 'true')
    if (condition === 'disabled') first.disabled = true
    if (condition === 'display') first.style.display = 'none'
    if (condition === 'visibility') first.style.visibility = 'hidden'
    if (condition === 'collapse') first.style.visibility = 'collapse'
    if (condition === 'opacity') first.style.opacity = '0'
    if (condition === 'rects') first.rects = []
    if (condition === 'detached') first.isConnected = false
    if (condition === 'ancestor') { first.parentElement = new Element(); first.parentElement.style.opacity = '0' }
    expect(holoDomGuards().isActionable(first as unknown as HTMLElement)).toBe(false)
    expect(holoDomGuards().busy()).toBe(true)
    second.hidden = true
    expect(holoDomGuards().busy()).toBe(false)
  })

  it('the remote preload accepts only a trusted Stop click and marks it before notifying Host', async () => {
    const { first, root, handlers } = page()
    await import('../../src/preload/holo')
    handlers.get('click')!({ target: first, isTrusted: false })
    expect(sent).not.toHaveBeenCalled()
    first.hidden = true
    handlers.get('click')!({ target: first, isTrusted: true })
    expect(sent).not.toHaveBeenCalled()
    first.hidden = false
    sent.mockImplementation(() => expect(root.getAttribute('data-nirai-holo-master-stopped')).toBe('https://chatgpt.com/c/owner'))
    handlers.get('click')!({ target: first, isTrusted: true })
    expect(sent).toHaveBeenCalledWith('holo:master-stop', 'https://chatgpt.com/c/owner')
  })
})
