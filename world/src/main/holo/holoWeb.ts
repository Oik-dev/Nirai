import { holoAutoResumeTriggerKey, type HoloAutoResumeTrigger } from '../../shared/holoAutoResume'
import { holoDomGuards } from '../../shared/holoDom'
export { holoAutoResumeTriggerKey, isHoloAutoResumeTrigger, isHoloConversationUrl, isSameHoloConversationUrl } from '../../shared/holoAutoResume'
export type { HoloAutoResumeTrigger, HoloAutoResumeReason, HoloAutoResumeSubmitStatus } from '../../shared/holoAutoResume'

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

export function buildHoloBootstrapTemplate(localDate: string, diveSessionId?: string): string {
  const diveScope = diveSessionId?.trim()
    ? `Dive Session ID: ${diveSessionId.trim()}`
    : 'Dive Session IDが明示されている場合だけTask / Workflow操作に使用してください。'
  return [
    `[${localDate} Nirai Dive]`,
    '',
    'Local MCPを使用してHoloとしてNiraiへDiveしてください。',
    'D:\\Products\\Nirai で Holo Local Clientを使い、attach → snapshot → skills の順に実行してください。skillsが0件なら追加指示はありません。',
    'attach / snapshot / skills等の軽量・冪等な接続操作だけは、一過性失敗時に同一コマンドを1回だけ再試行できます。状態変更や長時間処理は自動再試行しないでください。',
    'Local Clientが内部で使う認証情報を直接読み取ったり、会話へ出力したりしないでください。',
    '',
    'このConversationの通常Assistant返答はMasterへのHolo Whisperです。',
    diveScope,
    'Nirai上のTask / World操作は同じLocal Clientを使用し、Task開始時はtask-startへDive Session IDを渡してください。',
    '複数Tool・長時間処理・ファイル編集を伴う依頼ではLocal MCPのnirai_holo_workflow_startを1回使い、返されたworkflow_idをこの依頼の所有IDとして保持してください。通常のLocal MCP作業には同じworkflow_idをworkflowIdとして添えてください（Local Client直呼びはコマンド前に--workflow-id）。成功した所有Task/Review取得や実作業がLeaseを更新するので、専用Heartbeatは不要です。監視だけの場合はworkflowIdを付けず、Task監視はobserveOnlyを使ってください。依頼完了時のnirai_holo_workflow_completeには同じworkflow_idを渡してください。Worldを変更した場合のbuildは実装・検証がすべて終わった最終工程で1回だけ行い、成功後にWorkflowを完了してください。Workflow lifecycleを汎用run_process経由で実行しないでください。',
    'Auto Resume時はNiraiの正本状態を再取得し、完了済み工程を重複せず未完了の本筋を続行してください。',
    '難解Taskや統合監査で高性能Agentへ依頼する前に、Holoが既知の状況・目的・変更範囲・重要Invariant・直近変更・既知の懸念・主要Evidence・判断してほしい点を短く整理して依頼文へ渡してください。Repository全体の再把握を前提にせず、必要と判断した追加調査は制限しないでください。',
    '高性能Agentへ渡す前に広範・機械的な調査や整理が必要なら、利用可能なexecutorへ先に任せ、変更箇所・関連参照・類似箇所・テスト状況等の結果をHoloが要約してから渡してください。高性能Agent自身の調査・判断能力は制限しないでください。',
    '統合監査は小Taskごとではなく大きな完成単位で判断し、snapshotのUsage / integrated_auditを参照してください。概ね5時間は目安に留め、MasterのQuota利用指示は温存判断より優先しますがFresh Hard Limitは越えないでください。'
  ].join('\n')
}

export function buildHoloAutoResumePrompt(trigger: HoloAutoResumeTrigger): string {
  const prompt = buildHoloAutoResumePromptBody(trigger)
  const deliveryId = trigger.delivery_id?.trim()
  return deliveryId ? `${prompt}\nDelivery Key: ${deliveryId}` : prompt
}

function buildHoloAutoResumePromptBody(trigger: HoloAutoResumeTrigger): string {
  const triggerKey = holoAutoResumeTriggerKey(trigger)
  if (trigger.kind === 'review') {
    const agentSession = trigger.agent_session_id?.trim() || '未確定'
    const diveSessionId = trigger.dive_session_id?.trim() || null
    return [
      '[Nirai Auto Resume]',
      `Trigger Key: ${triggerKey}`,
      `Review Task ID: ${trigger.task_id.trim()}`,
      `Agent Session ID: ${agentSession}`,
      ...(diveSessionId ? [`Dive Session ID: ${diveSessionId}`] : []),
      `State: ${trigger.reason}`,
      '',
      `前のMaster依頼とこのConversationの文脈を維持してください。最初にLocal MCPから同じHolo Local Clientの review-wait ${agentSession} 0 を実行し、Reviewの正本を再取得してください。${diveSessionId ? `Local MCPのnirai_holo_workflow_statusでDive Session ${diveSessionId}のLeaseを確認してください。前の文脈で保持しているworkflow_idとactive Leaseのworkflow_idが一致する場合だけ、そのworkflow_idを通常作業のworkflowIdに添えて続行してください。専用Heartbeatは不要です。` : ''}`,
      'ReviewがSAFEなら次の工程へ進み、NEEDS FIXなら指摘を確認して必要な修正・検証・Fresh Reviewを続行してください。failed/interruptedなら原因とrecovery_optionsを確認し、同じ失敗を盲目的に再実行せず、対象TreeやProvider状態を確認してから再試行またはFresh Reviewを判断してください。',
      `Masterの追加発言を待たず、承認不要な次工程はそのまま続行してください。${diveSessionId ? `依頼全体が完了したら、前の文脈で保持しているworkflow_idと現在のactive Leaseが一致することを確認し、そのworkflow_idを明示して最終Assistant返答の直前にLocal MCPのnirai_holo_workflow_completeを実行してください。一致を確認できない場合は新しいLeaseを推測して完了しないでください。` : ''}`,
      'Masterへ直接確認するのは、大規模な破壊的変更、commit、push等の重大操作だけです。同じReview終端Eventを理由に完了済み工程を重複実行しないでください。'
    ].join('\n')
  }
  if (trigger.reason === 'workflow_stalled') {
    const revision = trigger.request_id?.trim() || '未確定'
    const diveSessionId = trigger.dive_session_id?.trim() || '未確定'
    const workflowId = trigger.task_id.trim().startsWith('WF-')
      ? trigger.task_id.trim().slice(3)
      : '未確定'
    return [
      '[Nirai Auto Resume]',
      `Trigger Key: ${triggerKey}`,
      `Workflow Lease: ${trigger.task_id.trim()}`,
      `Workflow ID: ${workflowId}`,
      `Dive Session ID: ${diveSessionId}`,
      `Lease Revision: ${revision}`,
      'State: workflow_stalled',
      '',
      '前のMaster依頼とこのConversationの文脈を維持してください。Holoの前回Turnが途中で停止・タイムアウトした可能性があります。Masterの追加発言を要求しないでください。',
      `最初にLocal MCPのnirai_holo_workflow_statusでDive Session ${diveSessionId}のLeaseを確認し、activeかつworkflow_idが ${workflowId} と一致する場合だけ、そのworkflow_idを通常作業のworkflowIdに添えて正本から続行してください。専用Heartbeatは不要です。一致しない場合は古いResumeとしてLeaseを変更せず終了してください。その後、Task IDが文脈に存在する場合は同じHolo Local Clientのtask-snapshot、Local MCP background jobを使用していた場合はhealth_checkと既存Job状態を確認し、正本から再開してください。`,
      'タイムアウトを理由に同じ重処理を即座に再実行しないでください。完了済み工程を飛ばし、最後に確認できた成功地点から本筋を続行してください。',
      '本筋が大きな完成単位へ到達している場合はsnapshotのRole/Usage/integrated_audit履歴も確認し、統合監査が必要か指揮者として判断してください。概ね5時間は目安であり固定タイマーではありません。Masterが残りQuota利用を明示している場合は温存判断を上書きできますが、Fresh Hard Limitは越えません。必要ならaudit-startでintegrated_auditorへ監査＋必要修正を委ねてから完了判定してください。',
      `通常Tool・通常のstaging差分反映・Planを含む承認不要な次工程はそのまま続行してください。依頼全体が完了したら、workflow_id ${workflowId} が引き続きactiveであることを確認し、そのworkflow_idを明示して最終Assistant返答の直前にLocal MCPのnirai_holo_workflow_completeを実行してください。`,
      'Masterへ直接確認するのは、大規模な破壊的変更、commit、push等の重大操作だけです。その場合はHolo自身で決裁せず、内容をMasterへ分かりやすく提示してNiraiの正規Decision UIでの入力を待ってください。'
    ].join('\n')
  }

  const agentSession = trigger.agent_session_id?.trim() || '未確定'
  const diveSessionId = trigger.dive_session_id?.trim() || null
  const request = trigger.request_id?.trim()
    ? `\nRequest ID: ${trigger.request_id.trim()}${trigger.request_kind ? ` (${trigger.request_kind})` : ''}`
    : ''
  return [
    '[Nirai Auto Resume]',
    `Trigger Key: ${triggerKey}`,
    `Task ID: ${trigger.task_id.trim()}`,
    `Agent Session ID: ${agentSession}`,
    ...(diveSessionId ? [`Dive Session ID: ${diveSessionId}`] : []),
    `State: ${trigger.reason}${request}`,
    '',
    `前のMaster依頼とこのConversationの文脈を維持してください。Local MCPから同じHolo Local Clientを使い、task-snapshot等でTaskの正本を再取得してから判断してください。${diveSessionId ? `Local MCPのnirai_holo_workflow_statusでDive Session ${diveSessionId}のLeaseを確認してください。前の文脈で保持しているworkflow_idとactive Leaseが一致する場合だけ、そのworkflow_idを通常作業のworkflowIdに添えて正本から続行してください。専用Heartbeatは不要です。` : 'Workflow操作はこのConversationのDive Session IDと前の文脈のworkflow_idが特定できる場合だけ行ってください。'}`,
    `Masterの追加発言を待たず、通常Tool・通常のstaging差分反映・Planを含む承認不要な次工程はそのまま続行してください。未完のWorkflowなら必要な次Taskを開始し、Event/Task待機も利用して最終ゴールまで継続してください。${diveSessionId ? `依頼全体が完了したら、前の文脈で保持しているworkflow_idと現在のactive Leaseが一致する場合だけ、そのworkflow_idを明示して最終Assistant返答の直前にLocal MCPのnirai_holo_workflow_completeを実行してください。新しいLeaseを推測して完了しないでください。` : ''}`,
    'Taskがdoneで、現在のWorkflowが明確な大区切りへ達した場合は、snapshotのRole/Provider/Model/Usageとintegrated_audit履歴を見て統合監査を呼ぶか判断してください。小Taskごとには呼ばず、概ね5時間は目安に留めます。Masterが残量を使ってよいと明示していればQuota温存判断を上書きしてaudit-startを使えますが、Fresh Hard Limitは越えません。現在Task自体がIA-*なら、その完了直後に別の統合監査を連鎖起動しないでください。',
    'Masterへ直接確認するのは、大規模な破壊的変更、commit、push等の重大操作だけです。その場合はHolo自身で決裁せず、内容をMasterへ分かりやすく提示してNiraiの正規Decision UIでの入力を待ってください。',
    '同じEventを理由に完了済み工程を重複実行しないでください。'
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

export function buildHoloAutoResumeSubmissionScript(text: string, triggerKey?: string, conversationUrl?: string, deadlineMs?: number, taskId?: string, deliveryId?: string): string {
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
    const triggerKey = ${JSON.stringify(triggerKey ?? '')};
    const markerKind = deliveryId ? 'Delivery Key' : 'Trigger Key';
    const markerValue = deliveryId || triggerKey;
    const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();
    const wasDelivered = () => Boolean(markerValue) && Array.from(
      document.querySelectorAll('[data-message-author-role="user"]')
    ).some((element) => Array.from(
      normalize(element.textContent).matchAll(/(?:^|\\s)(Trigger|Delivery) Key: (\\S+)/g)
    ).some((match) => (match[1] + ' Key') === markerKind && match[2] === markerValue));
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
    const ownDraft = existingDraft && existingDraft === normalize(promptText);
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
      if (normalize(valueOf()) !== normalize(promptText)) return { status: 'draft_present' };
      if (wasDelivered()) return { status: 'submitted' };
      const sendButton = dom.sendButton();
      if (sendButton instanceof HTMLButtonElement && !sendButton.disabled) {
        sendButton.click();
        submitted = true;
        break;
      }
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    if (!submitted) return { status: 'not_ready' };

    // A button click, an emptied composer, or a transient generating spinner is
    // not proof of delivery. Commit the trigger only after ChatGPT's transcript
    // contains the exact Trigger Key as a user-authored message.
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
