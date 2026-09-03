import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  HOLO_SKIN_CSS,
  buildHoloBootstrapTemplate,
  buildHoloDisclaimerSuppressionScript,
  buildHoloSkinAppliedProbeScript,
  buildHoloSkinMarkerScript,
  buildHoloSkinProbeScript,
  clampHoloSurfaceBounds,
  deriveHoloAddonPhase,
  isHealthyHoloSkinProbe,
  isHoloAllowedNavigationUrl,
  isHoloConversationUrl,
  isSafeHoloExternalUrl,
  shouldResetHoloSkinForNavigation,
  shouldAllowHoloWebPermission
} from '../../src/main/holo/holoWeb'

class FakeStyle {
  private readonly values = new Map<string, string>()

  setProperty(name: string, value: string): void {
    this.values.set(name, value)
  }

  get(name: string): string | undefined {
    return this.values.get(name)
  }
}

class FakeElement {
  readonly children: FakeElement[] = []
  readonly style = new FakeStyle()
  parentElement: FakeElement | null = null
  private readonly attributes = new Map<string, string>()

  constructor(
    readonly tagName: string,
    attributes: Readonly<Record<string, string>> = {},
    private readonly ownText = ''
  ) {
    for (const [name, value] of Object.entries(attributes)) {
      this.attributes.set(name, value)
    }
  }

  get textContent(): string {
    return this.ownText + this.children.map((child) => child.textContent).join('')
  }

  append(...children: FakeElement[]): this {
    for (const child of children) {
      child.parentElement = this
      this.children.push(child)
    }
    return this
  }

  contains(descendant: FakeElement): boolean {
    return descendant === this || this.children.some((child) => child.contains(descendant))
  }

  closest(selector: string): FakeElement | null {
    for (let element: FakeElement | null = this; element; element = element.parentElement) {
      if (element.matches(selector)) return element
    }
    return null
  }

  matches(selector: string): boolean {
    return selector.split(',').some((part) => this.matchesOne(part.trim()))
  }

  querySelector(selector: string): FakeElement | null {
    return this.querySelectorAll(selector)[0] ?? null
  }

  querySelectorAll(selector: string): FakeElement[] {
    const matches: FakeElement[] = []
    for (const child of this.children) {
      if (child.matches(selector)) matches.push(child)
      matches.push(...child.querySelectorAll(selector))
    }
    return matches
  }

  setAttribute(name: string, value: string): void {
    this.attributes.set(name, value)
  }

  private matchesOne(selector: string): boolean {
    const tag = this.tagName.toLowerCase()
    if (selector === '*') return true
    if (selector === 'main' || selector === 'article' || selector === 'a'
      || selector === 'button' || selector === 'input' || selector === 'textarea'
      || selector === 'select' || selector === 'option') {
      return tag === selector
    }
    if (selector === '#prompt-textarea') return this.attributes.get('id') === 'prompt-textarea'
    if (selector === 'textarea[placeholder]') {
      return tag === 'textarea' && this.attributes.has('placeholder')
    }
    const exactAttribute = selector.match(/^\[([^=]+)="([^"]+)"\]$/)
    if (exactAttribute) return this.attributes.get(exactAttribute[1]) === exactAttribute[2]
    if (selector === '[data-message-author-role]') {
      return this.attributes.has('data-message-author-role')
    }
    return false
  }
}

class FakeMutationObserver {
  disconnect(): void {}
  observe(): void {}
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('Holo Addon Web helpers', () => {
  it('builds a Dive bootstrap without an automatic-send instruction', () => {
    const bootstrap = buildHoloBootstrapTemplate('2026-08-31')
    expect(bootstrap).toContain('[2026-08-31 Nirai Dive]')
    expect(bootstrap).toContain('Local MCPを使用してNiraiへ接続してください。')
    expect(bootstrap).toContain('tools\\holo-local-client.mjs attach')
    expect(bootstrap).toContain('同じLocal Clientのsnapshot')
    expect(bootstrap).toContain('同じLocal Clientのskills')
    expect(bootstrap).toContain('0件なら追加のSkill指示はありません。')
    expect(bootstrap).toContain('認証情報を直接読み取ったり')
    expect(bootstrap).toContain('このConversationの通常Assistant返答はMasterへのHolo Whisperです。')
    expect(bootstrap).not.toContain('自動送信')
  })

  it('limits the product Skin to background integration and keeps Conversation elements visible', () => {
    expect(HOLO_SKIN_CSS).toContain('html[data-nirai-holo-skin="product"]')
    expect(HOLO_SKIN_CSS).toContain('--nirai-holo-skin-probe: 1')
    expect(HOLO_SKIN_CSS).toContain('background-image:')
    // The history sidebar stays visible and usable, blended into the glass.
    expect(HOLO_SKIN_CSS).toContain('html[data-nirai-holo-skin="product"] nav')
    expect(HOLO_SKIN_CSS).not.toContain('display: none')
    // ChatGPT's own dark surfaces and composer fade become see-through,
    // scoped to main so portal menus keep their readable fill.
    expect(HOLO_SKIN_CSS).toContain('main [class*="bg-token-main-surface-primary"]')
    expect(HOLO_SKIN_CSS).toContain('.content-fade::after')
    expect(HOLO_SKIN_CSS).toContain('[class*="bg-token-sidebar-surface"]')
    // The retired Wide chrome-width compensation must not come back.
    expect(HOLO_SKIN_CSS).not.toContain('--nirai-holo-chrome-width')
    expect(HOLO_SKIN_CSS).not.toContain('margin-inline-start:')
    expect(HOLO_SKIN_CSS).not.toContain('#prompt-textarea')
    expect(buildHoloSkinProbeScript()).toContain("location.hostname === 'chatgpt.com'")
    expect(buildHoloSkinMarkerScript(true)).toContain("setAttribute('data-nirai-holo-skin', 'product')")
    expect(buildHoloSkinMarkerScript(false)).toContain("removeAttribute('data-nirai-holo-skin')")
    expect(buildHoloSkinAppliedProbeScript()).toContain('--nirai-holo-skin-probe')
    const disclaimerScript = buildHoloDisclaimerSuppressionScript()
    expect(disclaimerScript).toContain("document.querySelector('#prompt-textarea')")
    expect(disclaimerScript).toContain("nextComposer.closest('main')")
    expect(disclaimerScript).not.toContain("nextComposer.closest('form')")
    expect(disclaimerScript).toContain('回答は必ずしも正しいとは限りません')
    expect(disclaimerScript).toContain('ChatGPT can make mistakes')
    expect(disclaimerScript).toContain('[data-message-author-role]')
    expect(disclaimerScript).toContain('[contenteditable="true"]')
    expect(disclaimerScript).toContain('new MutationObserver((records)')
    expect(disclaimerScript).toContain("record.type === 'characterData'")
    expect(disclaimerScript).toContain('record.addedNodes')
    expect(disclaimerScript).toContain('requestAnimationFrame')
    // ChatGPT may construct or replace the composer after did-finish-load.
    // Rebind the local observer without ever observing the whole document body.
    expect(disclaimerScript).toContain('const bindCurrentComposer = () =>')
    expect(disclaimerScript).toContain('setInterval(() =>')
    expect(disclaimerScript).toContain('clearInterval(existing.timerId)')
    expect(disclaimerScript).toContain('nextComposer === composer && nextRoot === root')
    expect(disclaimerScript).not.toContain('document.createTreeWalker(document.body')
    expect(disclaimerScript).not.toContain('observer.observe(document.body')
    expect(disclaimerScript).toContain('const deepestMatches = matches.filter')
    expect(disclaimerScript).toContain('candidate.contains(descendant)')
    expect(disclaimerScript).toContain('normalize(parent.textContent) !== text')
    expect(disclaimerScript).toContain('containsComposer(parent)')
    expect(disclaimerScript).toContain('isConversationContent(parent)')
    expect(disclaimerScript).toContain('isInteractiveContent(parent)')
    expect(disclaimerScript).toContain('for (const target of targets)')
    expect(disclaimerScript).toContain("style.setProperty('display', 'none', 'important')")
    expect(isHealthyHoloSkinProbe({ host_ok: true, body_ok: true, chrome_ok: true, composer_ok: true })).toBe(true)
    expect(isHealthyHoloSkinProbe({ host_ok: true, body_ok: true, chrome_ok: false, composer_ok: true })).toBe(false)
    expect(isHealthyHoloSkinProbe({ host_ok: true, body_ok: true, chrome_ok: true, composer_ok: false })).toBe(false)
    expect(isHealthyHoloSkinProbe(null)).toBe(false)
    expect(shouldResetHoloSkinForNavigation(true, false)).toBe(true)
    expect(shouldResetHoloSkinForNavigation(true, true)).toBe(false)
    expect(shouldResetHoloSkinForNavigation(false, false)).toBe(false)
  })

  it('hides the dedicated disclaimer wrapper so its empty background cannot remain', () => {
    const disclaimer = 'ChatGPTの回答は必ずしも正しいとは限りません。重要な情報は確認するようにしてください。'
    const composer = new FakeElement('textarea', { id: 'prompt-textarea', placeholder: '' })
    const label = new FakeElement('span', {}, disclaimer)
    const decorativeSibling = new FakeElement('span')
    const disclaimerWrapper = new FakeElement('div').append(label, decorativeSibling)
    const conversation = new FakeElement('article').append(new FakeElement('p', {}, disclaimer))
    const main = new FakeElement('main').append(conversation, disclaimerWrapper, composer)

    vi.stubGlobal('HTMLElement', FakeElement)
    vi.stubGlobal('document', { querySelector: (selector: string) => main.querySelector(selector) })
    vi.stubGlobal('window', {})
    vi.stubGlobal('MutationObserver', FakeMutationObserver)
    vi.stubGlobal('requestAnimationFrame', vi.fn(() => 1))
    vi.stubGlobal('setInterval', vi.fn(() => 1))
    vi.stubGlobal('clearInterval', vi.fn())

    expect(eval(buildHoloDisclaimerSuppressionScript())).toBe(true)
    expect(disclaimerWrapper.style.get('display')).toBe('none')
    expect(conversation.style.get('display')).toBeUndefined()
    expect(composer.style.get('display')).toBeUndefined()
  })

  it('derives the Addon phase only from observed Web lifecycle state', () => {
    expect(deriveHoloAddonPhase('idle')).toBe('loading')
    expect(deriveHoloAddonPhase('loading')).toBe('loading')
    expect(deriveHoloAddonPhase('ready')).toBe('ready')
    expect(deriveHoloAddonPhase('unavailable')).toBe('unavailable')
    expect(deriveHoloAddonPhase('error')).toBe('error')
  })

  it('accepts only ChatGPT conversation URLs as persisted Dive references', () => {
    expect(isHoloConversationUrl('https://chatgpt.com/c/1234')).toBe(true)
    expect(isHoloConversationUrl('https://chatgpt.com/g/example/c/1234')).toBe(true)
    expect(isHoloConversationUrl('https://chatgpt.com/')).toBe(false)
    expect(isHoloConversationUrl('https://example.com/c/1234')).toBe(false)
    expect(isHoloConversationUrl('not-a-url')).toBe(false)
  })

  it('allows only ChatGPT and known authentication origins for in-surface navigation', () => {
    expect(isHoloAllowedNavigationUrl('https://chatgpt.com/c/1234')).toBe(true)
    expect(isHoloAllowedNavigationUrl('https://sub.chatgpt.com/path')).toBe(true)
    expect(isHoloAllowedNavigationUrl('https://auth.openai.com/login')).toBe(true)
    expect(isHoloAllowedNavigationUrl('https://accounts.google.com/o/oauth2/v2/auth')).toBe(true)
    expect(isHoloAllowedNavigationUrl('https://login.microsoftonline.com/common/oauth2/v2.0/authorize')).toBe(true)
    expect(isHoloAllowedNavigationUrl('https://appleid.apple.com/auth/authorize')).toBe(true)
    expect(isHoloAllowedNavigationUrl('https://chatgpt.com.attacker.example/')).toBe(false)
    expect(isHoloAllowedNavigationUrl('http://chatgpt.com/')).toBe(false)
    expect(isHoloAllowedNavigationUrl('about:blank')).toBe(false)
    expect(isHoloAllowedNavigationUrl('https://example.com/')).toBe(false)
  })

  it('sends only HTTPS popup targets to the external browser', () => {
    expect(isSafeHoloExternalUrl('https://example.com/path')).toBe(true)
    expect(isSafeHoloExternalUrl('http://example.com/path')).toBe(false)
    expect(isSafeHoloExternalUrl('file:///C:/Windows/System32/calc.exe')).toBe(false)
    expect(isSafeHoloExternalUrl('javascript:alert(1)')).toBe(false)
    expect(isSafeHoloExternalUrl('not-a-url')).toBe(false)
  })

  it('requires an explicit Master gesture for sanitized clipboard writes and denies all other remote-content permissions', () => {
    expect(shouldAllowHoloWebPermission('clipboard-sanitized-write', 'https://chatgpt.com/c/1234')).toBe(false)
    expect(shouldAllowHoloWebPermission('clipboard-sanitized-write', 'https://chatgpt.com/', true)).toBe(true)
    expect(shouldAllowHoloWebPermission('clipboard-sanitized-write', 'https://auth.openai.com/', true)).toBe(false)
    expect(shouldAllowHoloWebPermission('clipboard-sanitized-write', 'https://example.com/', true)).toBe(false)
    expect(shouldAllowHoloWebPermission('clipboard-sanitized-write', undefined, true)).toBe(false)

    for (const permission of [
      'media',
      'display-capture',
      'geolocation',
      'clipboard-read',
      'notifications',
      'fileSystem',
      'window-management',
      'idle-detection',
      'unknown'
    ]) {
      expect(shouldAllowHoloWebPermission(permission, 'https://chatgpt.com/c/1234', true)).toBe(false)
    }
  })

  it('clamps native WebContentsView bounds inside the BrowserWindow content area', () => {
    expect(clampHoloSurfaceBounds({ x: -20, y: 15, width: 2000, height: 900 }, 1280, 720)).toEqual({
      x: 0,
      y: 15,
      width: 1280,
      height: 705
    })
  })
})
