import type { HoloAutoResumeTrigger } from '../../shared/holoAutoResume'
import { holoDomGuards } from '../../shared/holoDom'
export { holoAutoResumeTriggerKey, isHoloAutoResumeTrigger, isHoloConversationUrl, isSameHoloConversationUrl } from '../../shared/holoAutoResume'
export type { HoloAutoResumeTrigger, HoloAutoResumeReason, HoloAutoResumeSubmitStatus } from '../../shared/holoAutoResume'

export const HOLO_CHATGPT_HOME_URL = 'https://chatgpt.com/'
export const HOLO_SESSION_PARTITION = 'persist:nirai-holo-chatgpt'
export const HOLO_CLIPBOARD_GESTURE_TTL_MS = 750

const HOLO_EXECUTOR_DELEGATION_POLICY = 'executor（Cursor等）は並列作業にだけ使い、結果が本筋のCritical PathならHolo自身で処理してください。executor完了を待つだけの状態は作らないでください。'
const HOLO_WORKFLOW_ACTIVITY_POLICY = '通常Local MCP ToolのschemaにworkflowIdがある場合は同じIDを添え、専用Heartbeatは使わないでください。schemaにworkflowIdが無い場合だけ互換Fallbackとして、同じIDのnirai_holo_workflow_heartbeatを長時間処理の直前と実作業の区切りごとに使ってLeaseを維持してください。監視だけではHeartbeatしないでください。'

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

export function buildHoloBootstrapTemplate(
  localDate: string,
  diveSessionId?: string,
  worldRules?: string
): string {
  const diveScope = diveSessionId?.trim()
    ? `Dive Session ID: ${diveSessionId.trim()}`
    : 'Dive Session IDが明示されている場合だけTask / Workflow操作に使用してください。'
  const rules = worldRules?.trim()
  return [
    `[${localDate} Nirai Dive]`,
    '',
    ...(rules ? ['<nirai-world-rules>', rules, '</nirai-world-rules>', ''] : []),
    'Local MCPを使用してHoloとしてNiraiへDiveしてください。',
    'D:\\Products\\Nirai で Holo Local Clientを使い、attach → snapshot → skills の順に実行してください。skillsが0件なら追加指示はありません。',
    'attach / snapshot / skills等の軽量・冪等な接続操作だけは、一過性失敗時に同一コマンドを1回だけ再試行できます。状態変更や長時間処理は自動再試行しないでください。',
    'Local Clientが内部で使う認証情報を直接読み取ったり、会話へ出力したりしないでください。',
    '',
    'このConversationの通常Assistant返答はMasterへのHolo Whisperです。',
    diveScope,
    'Nirai上のTask / World操作は同じLocal Clientを使用し、Task開始時はtask-startへDive Session IDを渡してください。',
    `複数Tool・長時間処理・ファイル編集ではnirai_holo_workflow_startを1回使い、返されたworkflow_idを通常作業へ添えてください。${HOLO_WORKFLOW_ACTIVITY_POLICY}完了は同じIDでnirai_holo_workflow_completeを使ってください。Workflow lifecycleは専用Toolだけを使い、World変更時のbuildは全実装・検証後に1回だけ行ってください。`,
    '難解Taskや統合監査をAgentへ渡す前に、Holoが目的・範囲・重要Invariant・直近変更・主要Evidence・判断点を短く要約してください。',
    HOLO_EXECUTOR_DELEGATION_POLICY
  ].join('\n')
}

export function buildHoloAutoResumePrompt(trigger: HoloAutoResumeTrigger): string {
  const diveSessionId = trigger.dive_session_id?.trim() || '-'
  const workflowId = trigger.workflow_id?.trim()
    || (trigger.task_id.trim().startsWith('WF-') ? trigger.task_id.trim().slice(3) : null)
  return [
    '[Nirai Auto Resume]',
    '',
    '以下を続行してください。',
    '',
    `Dive Session ID: ${diveSessionId}`,
    ...(workflowId ? [`Workflow ID: ${workflowId}`] : [
      `Task ID: ${trigger.task_id.trim()}`,
      ...(trigger.kind === 'review' && trigger.agent_session_id?.trim()
        ? [`Agent Session ID: ${trigger.agent_session_id.trim()}`] : [])
    ]),
    ...(trigger.delivery_id?.trim() ? [`再開ID: ${trigger.delivery_id.trim()}`] : [])
  ].join('\n')
}

export function buildHoloGenerationBusyProbeScript(): string {
  return `(() => {
    const __niraiHoloGenerationProbe = true;
    void __niraiHoloGenerationProbe;
    if (location.protocol !== 'https:' || location.hostname !== 'chatgpt.com') return false;
    const dom = (${holoDomGuards.toString()})();
    return dom.busy();
  })()`
}

export function buildHoloScrollStabilityScript(): string {
  return `(() => {
    const guardKey = '__niraiHoloScrollGuard';
    window[guardKey]?.dispose?.();
    if (location.protocol !== 'https:' || location.hostname !== 'chatgpt.com') return false;

    let scroller = null;
    let observer = null;
    let frameId = 0;
    let timerId = 0;
    let followingLatest = true;
    let lastScrollTop = 0;

    const bottomDistance = (element) => Math.max(
      0,
      element.scrollHeight - element.scrollTop - element.clientHeight
    );
    const isAtBottom = (element) => bottomDistance(element) <= 4;
    const composer = () => document.querySelector('#prompt-textarea')
      ?? document.querySelector('textarea[placeholder]')
      ?? document.querySelector('[contenteditable="true"][data-virtualkeyboard="true"]')
      ?? document.querySelector('[contenteditable="true"]');

    const findScroller = () => {
      const messages = document.querySelectorAll('[data-message-author-role]');
      const anchors = [];
      const lastMessage = messages.length > 0 ? messages[messages.length - 1] : null;
      if (lastMessage instanceof HTMLElement) anchors.push(lastMessage);
      const input = composer();
      if (input instanceof HTMLElement) anchors.push(input);

      for (const anchor of anchors) {
        for (let element = anchor.parentElement; element && element !== document.body; element = element.parentElement) {
          const overflowY = getComputedStyle(element).overflowY;
          if ((overflowY === 'auto' || overflowY === 'scroll')
            && element.scrollHeight > element.clientHeight + 8) {
            return element;
          }
        }
      }

      const root = document.scrollingElement;
      return root && root.scrollHeight > root.clientHeight + 8 ? root : null;
    };

    const queueFollowLatest = () => {
      if (!followingLatest || !scroller || frameId) return;
      frameId = requestAnimationFrame(() => {
        frameId = 0;
        if (!followingLatest || !scroller) return;
        scroller.scrollTop = scroller.scrollHeight;
        lastScrollTop = scroller.scrollTop;
      });
    };

    const stopFollowingLatest = () => {
      followingLatest = false;
      if (frameId) cancelAnimationFrame(frameId);
      frameId = 0;
    };

    const handleScroll = () => {
      if (!scroller) return;
      const currentScrollTop = scroller.scrollTop;
      if (currentScrollTop < lastScrollTop - 1) {
        stopFollowingLatest();
      } else if (isAtBottom(scroller)) {
        followingLatest = true;
      }
      lastScrollTop = currentScrollTop;
    };

    const handleWheel = (event) => {
      if (event.deltaY < 0) stopFollowingLatest();
    };

    const handleKeyDown = (event) => {
      if (!scroller) return;
      if (event.key === 'ArrowUp' || event.key === 'PageUp' || event.key === 'Home') {
        stopFollowingLatest();
        return;
      }
      if (event.key === 'End') {
        followingLatest = true;
        queueFollowLatest();
      }
    };

    const unbindScroller = () => {
      observer?.disconnect?.();
      observer = null;
      if (!scroller) return;
      scroller.removeEventListener('scroll', handleScroll);
      scroller.removeEventListener('wheel', handleWheel);
    };

    const bindScroller = () => {
      const next = findScroller();
      if (next === scroller) return;
      const hadScroller = Boolean(scroller);
      const wasFollowingLatest = followingLatest;
      unbindScroller();
      scroller = next;
      if (!scroller) return;
      followingLatest = hadScroller ? wasFollowingLatest : isAtBottom(scroller);
      lastScrollTop = scroller.scrollTop;
      scroller.addEventListener('scroll', handleScroll, { passive: true });
      scroller.addEventListener('wheel', handleWheel, { passive: true });
      observer = new MutationObserver(() => queueFollowLatest());
      observer.observe(scroller, { childList: true, subtree: true, characterData: true });
      if (followingLatest) queueFollowLatest();
    };

    document.addEventListener('keydown', handleKeyDown, true);
    timerId = setInterval(bindScroller, 1000);
    bindScroller();

    Object.defineProperty(window, guardKey, {
      value: {
        dispose() {
          if (timerId) clearInterval(timerId);
          timerId = 0;
          if (frameId) cancelAnimationFrame(frameId);
          frameId = 0;
          document.removeEventListener('keydown', handleKeyDown, true);
          unbindScroller();
          scroller = null;
        }
      },
      configurable: true,
      writable: true
    });
    return true;
  })()`
}

export function buildHoloAutoResumeSubmissionScript(text: string, conversationUrl?: string, deadlineMs?: number, taskId?: string, deliveryId?: string): string {
  return `(async () => {
    const __niraiHoloAutoResume = true;
    void __niraiHoloAutoResume;
    const expectedUrl = ${JSON.stringify(conversationUrl ?? null)};
    const conversationId = (value) => {
      try {
        const url = new URL(value);
        if (url.protocol !== 'https:' || url.hostname !== 'chatgpt.com') return null;
        const rawId = url.pathname.match(/(?:^|\\/)c\\/([^/]+)/)?.[1] ?? null;
        return rawId?.startsWith('WEB:') ? rawId.slice(4) : rawId;
      } catch {
        return null;
      }
    };
    const expectedConversationId = expectedUrl ? conversationId(expectedUrl) : null;
    const isOwnerConversation = () => !expectedUrl || (
      expectedConversationId !== null && conversationId(location.href) === expectedConversationId
    );
    const deadline = ${JSON.stringify(deadlineMs ?? null)};
    const taskId = ${JSON.stringify(taskId ?? null)};
    const maySubmit = () => (deadline === null || Date.now() < deadline) && isOwnerConversation()
      && !(taskId && window.__niraiHoloCancelledTasks?.includes(taskId))
      && conversationId(document.documentElement?.getAttribute('data-nirai-holo-master-stopped')) !== conversationId(location.href);
    if (!maySubmit()) return { status: 'not_ready' };
    if (location.protocol !== 'https:' || location.hostname !== 'chatgpt.com' || !/(?:^|\\/)c\\/[^/]+/.test(location.pathname)) {
      return { status: 'not_ready' };
    }

    const deliveryId = ${JSON.stringify(deliveryId?.trim() ?? '')};
    const promptText = ${JSON.stringify(text)};
    const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();
    const normalizedPrompt = normalize(promptText);
    const wasDelivered = () => Boolean(deliveryId) && Array.from(
      document.querySelectorAll('[data-message-author-role="user"]')
    ).some((element) => Array.from(
      normalize(element.textContent).matchAll(/(?:^|\\s)再開ID: (\\S+)/g)
    ).some((match) => match[1] === deliveryId));
    if (wasDelivered()) return { status: 'submitted', duplicate: true };

    const dom = (${holoDomGuards.toString()})();
    const target = dom.composer();
    if (!dom.isActionable(target)) return { status: 'not_ready' };

    const valueOf = () => target instanceof HTMLTextAreaElement
      ? target.value
      : (target.innerText || target.textContent || '');
    const replaceDraft = (value) => {
      target.focus();
      if (target instanceof HTMLTextAreaElement) {
        const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set;
        if (setter) setter.call(target, value);
        else target.value = value;
        target.dispatchEvent(new Event('input', { bubbles: true }));
        return;
      }
      const selection = window.getSelection();
      const range = document.createRange();
      range.selectNodeContents(target);
      selection?.removeAllRanges();
      selection?.addRange(range);
      document.execCommand('insertText', false, value);
      target.dispatchEvent(new InputEvent('input', {
        bubbles: true,
        inputType: 'insertText',
        data: value
      }));
    };
    const existingDraft = normalize(valueOf());
    const ownDraft = existingDraft && existingDraft === normalizedPrompt;
    // A Nirai-looking draft may contain Master's edits. Only reuse our exact
    // untouched prompt; never clear another draft based on its prefix.
    if (existingDraft && !ownDraft) return { status: 'draft_present' };
    if (dom.busy()) return { status: 'busy' };

    if (!ownDraft) {
      if (normalize(valueOf())) return { status: 'draft_present' };
      replaceDraft(promptText);
    }

    let submitted = false;
    for (let attempt = 0; attempt < 20 && !submitted; attempt += 1) {
      if (!maySubmit()) return { status: 'not_ready' };
      if (dom.composer() !== target || !dom.isActionable(target)) return { status: 'not_ready' };
      if (dom.busy()) return { status: 'busy' };
      if (normalize(valueOf()) !== normalizedPrompt) return { status: 'draft_present' };
      if (wasDelivered()) return { status: 'submitted', duplicate: true };
      const sendButton = dom.sendButton();
      if (sendButton instanceof HTMLButtonElement && !sendButton.disabled) {
        sendButton.click();
        submitted = true;
        break;
      }
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    if (!submitted) return { status: 'not_ready' };

    // Confirm the exact delivery in a user message. Counts of older visible
    // messages and browser storage are not evidence of this delivery.
    for (let attempt = 0; attempt < 50; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 100));
      if (!isOwnerConversation()) return { status: 'not_ready' };
      if (wasDelivered()) return { status: 'submitted' };
      if (!maySubmit()) return { status: 'not_ready' };
    }
    return { status: 'not_ready' };
  })()`
}

export function buildHoloAutoResumeCancellationScript(taskId: string, prompts: readonly string[]): string {
  return `(() => {
    window.__niraiHoloCancelledTasks = [...new Set([
      ...(window.__niraiHoloCancelledTasks || []), ${JSON.stringify(taskId)}
    ])];
    const target = document.querySelector('#prompt-textarea')
      ?? document.querySelector('textarea[placeholder]')
      ?? document.querySelector('[contenteditable="true"][data-virtualkeyboard="true"]')
      ?? document.querySelector('[contenteditable="true"]');
    if (!(target instanceof HTMLElement)) return;
    const value = target instanceof HTMLTextAreaElement ? target.value : (target.innerText || target.textContent || '');
    const normalize = (text) => text.replace(/\\s+/g, ' ').trim();
    if (!${JSON.stringify(prompts)}.some((prompt) => normalize(prompt) === normalize(value))) return;
    target.focus();
    if (target instanceof HTMLTextAreaElement) {
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set;
      if (setter) setter.call(target, ''); else target.value = '';
    } else {
      const range = document.createRange();
      range.selectNodeContents(target);
      const selection = window.getSelection();
      selection?.removeAllRanges();
      selection?.addRange(range);
      document.execCommand('insertText', false, '');
    }
    target.dispatchEvent(new Event('input', { bubbles: true }));
  })()`
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
    chrome_ok: Boolean(document.querySelector('main')),
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
