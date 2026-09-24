import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  buildHoloAutoResumePrompt,
  buildHoloAutoResumeSubmissionScript,
  buildHoloBootstrapTemplate,
  buildHoloDisclaimerSuppressionScript,
  buildHoloGenerationBusyProbeScript,
  buildHoloScrollStabilityScript,
  buildHoloSkinProbeScript,
  clampHoloSurfaceBounds,
  deriveHoloAddonPhase,
  isHealthyHoloSkinProbe,
  isHoloAllowedNavigationUrl,
  isHoloConversationUrl,
  isSameHoloConversationUrl,
  isSafeHoloExternalUrl,
  holoAutoResumeTriggerKey,
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
  it('refuses to submit an Auto Resume after navigation to a different conversation', async () => {
    vi.stubGlobal('location', { href: 'https://chatgpt.com/c/other' })
    const script = buildHoloAutoResumeSubmissionScript('resume', 'https://chatgpt.com/c/owner')
    expect(await new Function(`return ${script}`)()).toEqual({ status: 'not_ready' })
  })

  it('treats query and hash variants as the same ChatGPT conversation', () => {
    expect(isSameHoloConversationUrl(
      'https://chatgpt.com/c/WEB:owner',
      'https://chatgpt.com/c/WEB:owner?model=gpt-5#latest'
    )).toBe(true)
    expect(isSameHoloConversationUrl(
      'https://chatgpt.com/c/WEB:owner',
      'https://chatgpt.com/c/owner?model=gpt-5'
    )).toBe(true)
    expect(isSameHoloConversationUrl(
      'https://chatgpt.com/c/WEB:owner',
      'https://chatgpt.com/c/WEB:other?model=gpt-5'
    )).toBe(false)
  })

  it('builds a compact Dive bootstrap that leaves runtime policy to Nirai', () => {
    const bootstrap = buildHoloBootstrapTemplate('2026-08-31', '11111111-1111-4111-8111-111111111111')
    expect(bootstrap).toContain('[2026-08-31 Nirai Dive]')
    expect(bootstrap).toContain('Local MCPを使用してHoloとしてNiraiへDiveしてください。')
    expect(bootstrap).toContain('attach → snapshot → skills')
    expect(bootstrap).toContain('skillsが0件なら追加指示はありません。')
    expect(bootstrap).toContain('同一コマンドを1回だけ再試行できます。')
    expect(bootstrap).toContain('状態変更や長時間処理は自動再試行しないでください。')
    expect(bootstrap).toContain('認証情報を直接読み取ったり')
    expect(bootstrap).toContain('このConversationの通常Assistant返答はMasterへのHolo Whisperです。')
    expect(bootstrap).toContain('Dive Session ID: 11111111-1111-4111-8111-111111111111')
    expect(bootstrap).toContain('task-startへDive Session ID')
    expect(bootstrap).toContain('nirai_holo_workflow_start')
    expect(bootstrap).toContain('返されたworkflow_id')
    expect(bootstrap).toContain('完了は同じIDでnirai_holo_workflow_complete')
    expect(bootstrap).toContain('nirai_holo_workflow_heartbeat')
    expect(bootstrap).toContain('schemaにworkflowIdが無い場合だけ互換Fallback')
    expect(bootstrap).toContain('監視だけではHeartbeatしない')
    expect(bootstrap).toContain('Workflow lifecycleは専用Toolだけ')
    expect(bootstrap).toContain('World変更時のbuildは全実装・検証後に1回だけ')
    expect(bootstrap).toContain('目的・範囲・重要Invariant・直近変更・主要Evidence・判断点')
    expect(bootstrap).toContain('executor（Cursor等）は並列作業にだけ使い')
    expect(bootstrap).toContain('結果が本筋のCritical PathならHolo自身で処理')
    expect(bootstrap).toContain('executor完了を待つだけの状態は作らない')
    expect(bootstrap).not.toContain('Auto Resume時は')
    expect(bootstrap).not.toContain('概ね5時間')
    expect(bootstrap).not.toContain('Fresh Hard Limit')
    expect(bootstrap).not.toContain('通常Tool・通常のstaging差分反映・Plan')
    expect(bootstrap).not.toContain('大規模な破壊的変更、commit、push')
    expect(bootstrap).not.toContain('30%等の固定閾値')
    expect(bootstrap).not.toContain('integrated_auditorへ書込可能Task')
    expect(bootstrap.split('\n').length).toBeLessThanOrEqual(13)
    expect(bootstrap.length).toBeLessThanOrEqual(1600)
  })

  it('injects the shared World Rules into a new Holo Dive', () => {
    const bootstrap = buildHoloBootstrapTemplate(
      '2026-09-17',
      'DIVE-WORLD-RULES',
      '# Nirai World Rules\n\nすべての実装はシンプル・合理的・効率的にする。\nMasterへの報告は平易な日本語で行う。'
    )
    expect(bootstrap).toContain('<nirai-world-rules>')
    expect(bootstrap).toContain('シンプル・合理的・効率的')
    expect(bootstrap).toContain('平易な日本語')
    expect(bootstrap).toContain('</nirai-world-rules>')
  })

  it('builds the minimal Auto Resume prompt from only Dive and Workflow identity', () => {
    const trigger = {
      task_id: 'T-123',
      agent_session_id: 'AS-456',
      reason: 'waiting_for_master' as const,
      request_id: 'REQ-7',
      request_kind: 'approval' as const,
      dive_session_id: '11111111-1111-4111-8111-111111111111',
      workflow_id: 'workflow-123',
      conversation_url: 'https://chatgpt.com/c/task-owner'
    }
    expect(buildHoloAutoResumePrompt(trigger)).toBe([
      '[Nirai Auto Resume]',
      '',
      '以下を続行してください。',
      '',
      'Dive Session ID: 11111111-1111-4111-8111-111111111111',
      'Workflow ID: workflow-123'
    ].join('\n'))
    expect(holoAutoResumeTriggerKey(trigger)).toBe('T-123:AS-456:waiting_for_master:REQ-7')
  })

  it('adds only a short Resume ID to the minimal Workflow notification', () => {
    const prompt = buildHoloAutoResumePrompt({
      kind: 'review',
      task_id: 'HR-123',
      agent_session_id: 'AS-HR-456',
      reason: 'failed',
      request_id: 'REQ-SECRET',
      dive_session_id: 'DIVE-1',
      workflow_id: 'WORKFLOW-1',
      delivery_id: 'DELIVERY-SECRET'
    })
    expect(prompt).toBe('[Nirai Auto Resume]\n\n以下を続行してください。\n\nDive Session ID: DIVE-1\nWorkflow ID: WORKFLOW-1\n再開ID: DELIVERY-SECRET')
    expect(prompt).not.toContain('HR-123')
    expect(prompt).not.toContain('AS-HR-456')
    expect(prompt).not.toContain('REQ-SECRET')
    expect(prompt).not.toContain('State:')
    expect(prompt).not.toContain('Trigger Key:')
  })

  it('derives the Workflow ID directly from a workflow-stalled lease trigger', () => {
    expect(buildHoloAutoResumePrompt({
      task_id: 'WF-lease-1',
      reason: 'workflow_stalled',
      request_id: '2026-09-11T12:00:00.000Z',
      dive_session_id: 'DIVE-1'
    })).toBe('[Nirai Auto Resume]\n\n以下を続行してください。\n\nDive Session ID: DIVE-1\nWorkflow ID: lease-1')
  })

  it('identifies a standalone Task when there is no Workflow to inspect', () => {
    const prompt = buildHoloAutoResumePrompt({ task_id: 'T-STANDALONE', reason: 'failed', dive_session_id: 'DIVE-1' })
    expect(prompt).toContain('Task ID: T-STANDALONE')
    expect(prompt).not.toContain('Workflow ID: -')
  })

  it('builds a generation-busy probe used by the workflow watchdog', () => {
    const script = buildHoloGenerationBusyProbeScript()
    expect(script).toContain('__niraiHoloGenerationProbe')
    expect(script).toContain('stop-button')
    expect(script).toContain('Stop generating')
    expect(script).toContain('生成を停止')
    expect(script).toContain('getClientRects')
    expect(script).toContain("aria-hidden")
  })

  it('ignores a hidden stale Stop button but detects a visible active one', () => {
    class Element {
      isConnected = true
      parentElement = null
      matches() { return false }
      hidden = false
      getAttribute() { return null }
      getClientRects() { return this.hidden ? [] : [{}] }
    }
    class Button extends Element {
      disabled = false
    }
    const stopButton = new Button()
    stopButton.hidden = true
    vi.stubGlobal('HTMLElement', Element)
    vi.stubGlobal('HTMLButtonElement', Button)
    vi.stubGlobal('getComputedStyle', () => ({ display: 'block', visibility: 'visible', opacity: '1' }))
    vi.stubGlobal('location', new URL('https://chatgpt.com/c/owner'))
    vi.stubGlobal('document', { querySelectorAll: () => [stopButton] })

    const script = buildHoloGenerationBusyProbeScript()
    expect(new Function(`return ${script}`)()).toBe(false)
    stopButton.hidden = false
    expect(new Function(`return ${script}`)()).toBe(true)
  })

  it('builds a scroll guard that follows output only at the true bottom and releases immediately on upward input', () => {
    const script = buildHoloScrollStabilityScript()
    expect(script).toContain('__niraiHoloScrollGuard')
    expect(script).toContain('followingLatest')
    expect(script).toContain('MutationObserver')
    expect(script).toContain('bottomDistance(element) <= 4')
    expect(script).toContain('currentScrollTop < lastScrollTop - 1')
    expect(script).toContain('event.deltaY < 0')
    expect(script).toContain("event.key === 'ArrowUp'")
    expect(script).toContain("event.key === 'PageUp'")
    expect(script).toContain("event.key === 'Home'")
    expect(script).toContain("event.key === 'End'")
    expect(script).toContain('scroller.scrollTop = scroller.scrollHeight')
    expect(script).not.toContain('scrollIntoView')
    expect(script).not.toContain('Math.max(240')
    expect(script).toContain('window[guardKey]?.dispose?.()')
  })

  it('confirms delivery by Resume ID without a second browser receipt store', () => {
    const script = buildHoloAutoResumeSubmissionScript(
      'continue', 'https://chatgpt.com/c/owner', undefined, 'T-123', 'delivery-123'
    )
    expect(script).toContain('__niraiHoloAutoResume')
    expect(script).toContain("status: 'draft_present'")
    expect(script).toContain("status: 'busy'")
    expect(script).toContain('delivery-123')
    expect(script).not.toContain('localStorage')
    expect(script).toContain('[data-message-author-role="user"]')
    expect(script).toContain('再開ID:')
    expect(script).toContain('duplicate: true')
    expect(script).toContain('const ownDraft')
    expect(script).not.toContain('Trigger Key:')
    expect(script).not.toContain('Delivery Key:')
    expect(script).toContain('wasDelivered()')
    expect(script).toContain('for (let attempt = 0; attempt < 50; attempt += 1)')
    expect(script).not.toContain('requestSubmit')
    expect(script).toContain("status: 'submitted'")
  })

  it('tracks observable Skin health without requiring ChatGPT sidebar navigation', () => {
    const skinProbe = buildHoloSkinProbeScript()
    expect(skinProbe).toContain("querySelector('main')")
    expect(skinProbe).not.toContain("querySelector('nav')")
    expect(isHealthyHoloSkinProbe({ host_ok: true, body_ok: true, chrome_ok: true, composer_ok: true })).toBe(true)
    expect(isHealthyHoloSkinProbe({ host_ok: true, body_ok: true, chrome_ok: false, composer_ok: true })).toBe(false)
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
