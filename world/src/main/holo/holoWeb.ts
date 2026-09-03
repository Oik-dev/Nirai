export const HOLO_CHATGPT_HOME_URL = 'https://chatgpt.com/'
export const HOLO_SESSION_PARTITION = 'persist:nirai-holo-chatgpt'
export const HOLO_CLIPBOARD_GESTURE_TTL_MS = 750

export type HoloSkinMode = 'checking' | 'applied' | 'fallback'
export type HoloWebState = 'idle' | 'loading' | 'ready' | 'unavailable' | 'error'
export type HoloAddonPhase = 'loading' | 'ready' | 'unavailable' | 'error'

export const HOLO_SKIN_CSS = `
html[data-nirai-holo-skin="product"] {
  --nirai-holo-skin-probe: 1;
  color-scheme: dark;
}

/* Structural containers become see-through so the Nirai Glass behind the
   native view carries the surface tone (12: 半透明Glass). */
html[data-nirai-holo-skin="product"],
html[data-nirai-holo-skin="product"] body,
html[data-nirai-holo-skin="product"] body > div,
html[data-nirai-holo-skin="product"] main,
html[data-nirai-holo-skin="product"] main > div {
  background-color: transparent !important;
}

/* ChatGPT paints its dark theme through surface tokens inside the thread.
   Scope to main so portal menus and dialogs keep their own readable fill. */
html[data-nirai-holo-skin="product"] main [class*="bg-token-main-surface-primary"],
html[data-nirai-holo-skin="product"] main [class*="bg-token-bg-primary"] {
  background-color: transparent !important;
}

/* The composer fade paints an opaque gradient band above the input. */
html[data-nirai-holo-skin="product"] .content-fade::after,
html[data-nirai-holo-skin="product"] [id="thread-bottom-container"]::after,
html[data-nirai-holo-skin="product"] main [class*="bg-gradient-to"],
html[data-nirai-holo-skin="product"] main [class*="from-token-main-surface"] {
  background: transparent !important;
  background-image: none !important;
}

/* Keep the ChatGPT history sidebar usable, blended into the glass. */
html[data-nirai-holo-skin="product"] nav,
html[data-nirai-holo-skin="product"] [class*="bg-token-sidebar-surface"] {
  background-color: rgb(2 22 39 / 40%) !important;
}

/* Gentle Nirai tint keeps ChatGPT text readable over the World. The overall
   darkness is owned by the renderer glass, matching the chat log tone. */
html[data-nirai-holo-skin="product"] body {
  background-image: linear-gradient(160deg, rgb(3 29 47 / 8%), rgb(1 17 31 / 10%)) !important;
}
`

const HOLO_ALLOWED_NAVIGATION_HOSTS = [
  'chatgpt.com',
  'auth.openai.com',
  'accounts.google.com',
  'login.microsoftonline.com',
  'login.live.com',
  'appleid.apple.com',
  'account.apple.com'
] as const

export interface HoloSurfaceBounds {
  readonly x: number
  readonly y: number
  readonly width: number
  readonly height: number
}

export function buildHoloBootstrapTemplate(localDate: string): string {
  return [
    `[${localDate} Nirai Dive]`,
    '',
    'Local MCPを使用してNiraiへ接続してください。',
    'あなたはHoloとしてNiraiへDiveします。',
    'Local MCPのrun_processでcwdをD:\\Products\\Niraiにし、node.exe tools\\holo-local-client.mjs attach を実行してください。',
    'attach後、同じLocal Clientのsnapshotで現在のNirai状態を取得してください。',
    '続けて同じLocal Clientのskillsを実行し、Nirai Skillsが返された場合だけ、必要な場面でその指示を使用してください。0件なら追加のSkill指示はありません。',
    'Local Clientが内部で使う認証情報を直接読み取ったり、会話へ出力したりしないでください。',
    '',
    'このConversationの通常Assistant返答はMasterへのHolo Whisperです。',
    'Nirai World上で公開発言・状態確認・Event待機が必要な場合は、同じLocal Clientのsay / snapshot / waitをLocal MCP経由で使用してください。'
  ].join('\n')
}

function matchesAllowedHost(hostname: string, allowedHost: string): boolean {
  return hostname === allowedHost || hostname.endsWith(`.${allowedHost}`)
}

export function isHoloAllowedNavigationUrl(value: string): boolean {
  try {
    const url = new URL(value)
    if (url.protocol !== 'https:') return false
    return HOLO_ALLOWED_NAVIGATION_HOSTS.some((host) => matchesAllowedHost(url.hostname, host))
  } catch {
    return false
  }
}

export function isSafeHoloExternalUrl(value: string): boolean {
  try {
    const url = new URL(value)
    return url.protocol === 'https:'
  } catch {
    return false
  }
}

export function buildHoloSkinProbeScript(): string {
  return `(() => ({
    host_ok: location.protocol === 'https:' && location.hostname === 'chatgpt.com',
    body_ok: document.body instanceof HTMLBodyElement,
    chrome_ok: Boolean(document.querySelector('main') && document.querySelector('nav')),
    composer_ok: Boolean(
      document.querySelector('#prompt-textarea')
      ?? document.querySelector('textarea[placeholder]')
      ?? document.querySelector('[contenteditable="true"]')
    )
  }))()`
}

export function buildHoloSkinMarkerScript(enabled: boolean): string {
  return enabled
    ? `(() => { document.documentElement?.setAttribute('data-nirai-holo-skin', 'product'); return true; })()`
    : `(() => { document.documentElement?.removeAttribute('data-nirai-holo-skin'); return true; })()`
}

export function buildHoloSkinAppliedProbeScript(): string {
  return `(() => {
    const root = document.documentElement;
    if (!(root instanceof HTMLElement)) return false;
    if (root.getAttribute('data-nirai-holo-skin') !== 'product') return false;
    return getComputedStyle(root).getPropertyValue('--nirai-holo-skin-probe').trim() === '1';
  })()`
}

export function buildHoloDisclaimerSuppressionScript(): string {
  return `(() => {
    const observerKey = '__niraiHoloDisclaimerObserver';
    const existing = window[observerKey];
    existing?.observer?.disconnect?.();
    if (existing?.timerId) clearInterval(existing.timerId);

    const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();
    const isDisclaimer = (value) => {
      const text = normalize(value);
      if (!text || text.length > 220) return false;
      const japanese = text.includes('ChatGPT')
        && text.includes('回答は必ずしも正しいとは限りません')
        && text.includes('重要な情報');
      const english = text.includes('ChatGPT can make mistakes')
        && text.includes('Check important info');
      return japanese || english;
    };
    const conversationSelector = 'article, [data-message-author-role], [data-testid^="conversation-turn"], [data-testid*="conversation-turn"]';
    const interactiveSelector = 'a, button, input, textarea, select, option, [contenteditable="true"], [role="button"], [role="link"], [role="textbox"]';
    // ChatGPT has changed the disclaimer wrapper tag/class more than once.
    // Match by the stable user-facing copy, then hide the deepest matching
    // non-interactive branches instead of depending on private DOM structure.
    const candidateSelector = '*';

    let observer = null;
    let root = null;
    let composer = null;
    let frameId = 0;
    const pendingScopes = new Set();

    const isConversationContent = (element) => Boolean(
      element.closest(conversationSelector) || element.querySelector(conversationSelector)
    );
    const isInteractiveContent = (element) => Boolean(
      element.closest(interactiveSelector) || element.querySelector(interactiveSelector)
    );
    const containsComposer = (element) => composer instanceof HTMLElement
      && (element === composer || element.contains(composer));
    const suppressWithin = (scope) => {
      if (!(scope instanceof HTMLElement)) return 0;
      const candidates = [];
      if (scope.matches(candidateSelector)) candidates.push(scope);
      candidates.push(...scope.querySelectorAll(candidateSelector));

      const matches = candidates
        .filter((candidate) => candidate instanceof HTMLElement)
        .filter((candidate) => !containsComposer(candidate) && !isConversationContent(candidate))
        .filter((candidate) => !isInteractiveContent(candidate))
        .filter((candidate) => isDisclaimer(candidate.textContent));

      // Start from every deepest text match, then climb through text-identical
      // safe ancestors. ChatGPT can paint padding/background on a dedicated
      // wrapper outside the text node; hiding only the text leaves that empty
      // decoration visible. Exact normalized-text equality prevents the climb
      // from swallowing a container that also owns unrelated visible content.
      const deepestMatches = matches.filter((candidate) => !matches.some((descendant) => (
        descendant !== candidate && candidate.contains(descendant)
      )));
      const targets = [...new Set(deepestMatches.map((candidate) => {
        const text = normalize(candidate.textContent);
        let target = candidate;
        while (target !== root) {
          const parent = target.parentElement;
          if (!(parent instanceof HTMLElement) || !root.contains(parent)) break;
          if (containsComposer(parent) || isConversationContent(parent) || isInteractiveContent(parent)) break;
          if (normalize(parent.textContent) !== text) break;
          target = parent;
        }
        return target;
      }))];
      for (const target of targets) {
        target.style.setProperty('display', 'none', 'important');
        target.setAttribute('data-nirai-holo-disclaimer-hidden', 'true');
      }
      return targets.length;
    };
    const queueScope = (node) => {
      const scope = node instanceof HTMLElement ? node : node?.parentElement;
      if (!(scope instanceof HTMLElement) || !(root instanceof HTMLElement) || !root.contains(scope)) return;
      pendingScopes.add(scope);
    };
    const bindCurrentComposer = () => {
      const nextComposer = document.querySelector('#prompt-textarea')
        ?? document.querySelector('textarea[placeholder]')
        ?? document.querySelector('[contenteditable="true"][data-virtualkeyboard="true"]')
        ?? document.querySelector('[contenteditable="true"]');
      if (!(nextComposer instanceof HTMLElement)) return false;

      // The disclaimer is inserted after load outside the composer's immediate
      // form branch. Observe the semantic ChatGPT main surface so sibling
      // insertions are visible without observing or rescanning document.body.
      const nextRoot = nextComposer.closest('main');
      if (!(nextRoot instanceof HTMLElement)) return false;

      if (nextComposer === composer && nextRoot === root && observer instanceof MutationObserver) {
        return true;
      }

      observer?.disconnect?.();
      pendingScopes.clear();
      composer = nextComposer;
      root = nextRoot;
      suppressWithin(root);
      observer = new MutationObserver((records) => {
        for (const record of records) {
          if (record.type === 'characterData') queueScope(record.target);
          else for (const node of record.addedNodes) queueScope(node);
        }
        if (frameId || pendingScopes.size === 0) return;
        frameId = requestAnimationFrame(() => {
          frameId = 0;
          const scopes = [...pendingScopes];
          pendingScopes.clear();
          for (const scope of scopes) suppressWithin(scope);
        });
      });
      observer.observe(root, { childList: true, subtree: true, characterData: true });
      return true;
    };

    const timerId = setInterval(() => {
      // ChatGPT is an SPA: the composer can appear after did-finish-load or be
      // replaced during an in-page navigation. Rebind only the local composer
      // root; never observe or scan the whole document body.
      bindCurrentComposer();
    }, 750);
    bindCurrentComposer();

    Object.defineProperty(window, observerKey, {
      value: {
        get observer() { return observer; },
        get root() { return root; },
        timerId
      },
      configurable: true,
      writable: true
    });
    return true;
  })()`
}

export function deriveHoloAddonPhase(webState: HoloWebState): HoloAddonPhase {
  if (webState === 'ready') return 'ready'
  if (webState === 'unavailable') return 'unavailable'
  if (webState === 'error') return 'error'
  return 'loading'
}

export function isHealthyHoloSkinProbe(value: unknown): boolean {
  if (!value || typeof value !== 'object') return false
  const probe = value as Partial<Record<'host_ok' | 'body_ok' | 'chrome_ok' | 'composer_ok', unknown>>
  return probe.host_ok === true
    && probe.body_ok === true
    && probe.chrome_ok === true
    && probe.composer_ok === true
}

export function shouldResetHoloSkinForNavigation(
  isMainFrame: boolean,
  isSameDocument: boolean
): boolean {
  return isMainFrame && !isSameDocument
}

export function shouldAllowHoloWebPermission(
  permission: string,
  requestingUrl?: string,
  masterGestureAuthorized = false
): boolean {
  // Remote content never earns permission from its origin alone. The sole
  // product exception is a one-shot sanitized Clipboard write armed by an
  // observed Master gesture in HoloAddonHost.
  if (!masterGestureAuthorized || permission !== 'clipboard-sanitized-write' || !requestingUrl) {
    return false
  }
  try {
    const url = new URL(requestingUrl)
    return url.protocol === 'https:' && url.hostname === 'chatgpt.com'
  } catch {
    return false
  }
}

export function isHoloConversationUrl(value: string): boolean {
  try {
    const url = new URL(value)
    if (url.protocol !== 'https:' || url.hostname !== 'chatgpt.com') return false
    return /(?:^|\/)c\/[^/]+/.test(url.pathname)
  } catch {
    return false
  }
}

export function clampHoloSurfaceBounds(
  bounds: HoloSurfaceBounds,
  contentWidth: number,
  contentHeight: number
): HoloSurfaceBounds {
  const x = Math.max(0, Math.min(Math.round(bounds.x), Math.max(0, contentWidth - 1)))
  const y = Math.max(0, Math.min(Math.round(bounds.y), Math.max(0, contentHeight - 1)))
  const width = Math.max(1, Math.min(Math.round(bounds.width), Math.max(1, contentWidth - x)))
  const height = Math.max(1, Math.min(Math.round(bounds.height), Math.max(1, contentHeight - y)))
  return { x, y, width, height }
}
