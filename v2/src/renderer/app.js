const statusLabel = {
  Running: '実行中',
  Paused: '一時停止',
  Completed: '完了',
  Failed: '失敗',
  Cancelled: '取消済み',
  Waiting: '待機中',
  Interrupted: '中断',
}

const taskStatusOrder = {
  Running: 0,
  Paused: 1,
  Completed: 2,
  Failed: 3,
  Cancelled: 4,
}

const activityStatusOrder = {
  Running: 0,
  Waiting: 1,
  Failed: 2,
  Interrupted: 3,
  Completed: 4,
  Cancelled: 5,
}

const TERMINAL_STATES = new Set(['Completed', 'Failed', 'Cancelled'])
const TERMINAL_VISIBLE_MS = 72 * 60 * 60 * 1000

const TASK_TYPE_ICONS = {
  build: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 19h16M6 16V8l6-4 6 4v8M9 16v-4h6v4"/></svg>',
  control: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h10M18 7h2M4 17h2M10 17h10M14 4v6M8 14v6"/></svg>',
  review: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3h9l3 3v15H6zM14 3v4h4M9 12l2 2 4-4"/></svg>',
  memory: '<svg viewBox="0 0 24 24" aria-hidden="true"><ellipse cx="12" cy="5" rx="7" ry="3"/><path d="M5 5v7c0 1.7 3.1 3 7 3s7-1.3 7-3V5M5 12v7c0 1.7 3.1 3 7 3s7-1.3 7-3v-7"/></svg>',
  automation: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 7h8l-2-2M17 17H9l2 2M17 7a7 7 0 0 1 1.4 8M7 17a7 7 0 0 1-1.4-8"/></svg>',
  research: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.5" cy="10.5" r="5.5"/><path d="m15 15 5 5M8 10.5h5M10.5 8v5"/></svg>',
  maintenance: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 6a5 5 0 0 0-6 6L3 17l4 4 5-5a5 5 0 0 0 6-6l-3 3-4-4z"/></svg>',
  general: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3h9l3 3v15H6zM14 3v4h4M9 11h6M9 15h6"/></svg>',
}

const bridge = window.niraiDashboard
let snapshot = { tasks: [], pending_requests: [], runs: [], messages: [], residents: [] }
let tasks = []
let residents = []
let expandedTaskId = sessionStorage.getItem('nirai:v2:selected-task')
let selectedResidentId = 'holo'
let closedTerminalTaskIds = loadClosedTerminalTaskIds()
let terminalExpiryTimer = null
let dashboardConnected = false
let commandBusy = false
let restoreChatFocus = false
let uncertainCommandId = localStorage.getItem('nirai:v2:uncertain-command-id')
let reconcilingCommand = false
let holoAppDraft = null
let workspaceDraft = null
let holoSettingsRevision = null
let holoSurfaceSignature = null
let holoSurfaceTaskId = null
let holoSurfaceMode = null
let holoSurfaceSerial = Promise.resolve()
let holoSurfaceVisible = false
let holoPresentation = null
let holoPresentationRevision = 0
let holoChatPresentation = null
let holoChatReturnFocus = null
let taskListOpen = false
let chatRenderSignature = null
let displayedInputTaskId = null
const chatDrafts = new Map()
const renderedMarkup = new WeakMap()
const residentStripDrag = { active: false, moved: false, startX: 0, startScrollLeft: 0 }
const $ = (id) => document.getElementById(id)
const residentDrafts = new Map()
let settingsTrigger = 'settingsButton'
const usualConversation = window.createUsualConversationUI({
  state: () => ({ snapshot, connected: dashboardConnected, locked: commandBusy || Boolean(uncertainCommandId) }),
  send: sendCommand, escapeHtml, timeLabel, residentName,
})

function loadClosedTerminalTaskIds() {
  try {
    const value = JSON.parse(localStorage.getItem('nirai:v2:closed-terminal-tasks') ?? '[]')
    return new Set(Array.isArray(value) ? value.filter(item => typeof item === 'string') : [])
  } catch {
    return new Set()
  }
}

function persistClosedTerminalTaskIds() {
  localStorage.setItem('nirai:v2:closed-terminal-tasks', JSON.stringify([...closedTerminalTaskIds]))
}

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;')
}

function commandEnvelope(type, payload, expectedRevision) {
  return {
    protocol_version: 1,
    command_id: crypto.randomUUID(),
    issued_at: new Date().toISOString(),
    type,
    target: payload?.task_id ?? payload?.request_id ?? null,
    ...(Number.isInteger(expectedRevision) ? { expected_revision: expectedRevision } : {}),
    payload,
  }
}

function timeLabel(value) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleTimeString('ja-JP', { hour: '2-digit', minute: '2-digit' })
}

function relativeLabel(value) {
  if (!value) return ''
  const timestamp = new Date(value).getTime()
  if (!Number.isFinite(timestamp)) return ''
  const elapsed = Math.max(0, Date.now() - timestamp)
  if (elapsed < 60_000) return 'たった今'
  const minutes = Math.floor(elapsed / 60_000)
  if (minutes < 60) return `${minutes}分前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}時間前`
  return new Date(timestamp).toLocaleDateString('ja-JP', { month: 'numeric', day: 'numeric' })
}

function completedAgeLabel(value) {
  const label = relativeLabel(value)
  return label ? `${label}に完了` : '完了'
}

function showNotice(message, isError = false) {
  const notice = !$('residentSettingsPanel').hidden ? $('settingsNotice')
    : $('dashboard').classList.contains('is-open') ? $('hubNotice') : $('chatModeNotice')
  $(notice.id + 'Text').textContent = message
  notice.classList.toggle('is-error', isError)
  notice.setAttribute('role', isError ? 'alert' : 'status')
  notice.hidden = false
  clearTimeout(showNotice.timer)
  if (!isError) showNotice.timer = setTimeout(() => { notice.hidden = true }, 6000)
}

// Keep focus, expanded details and scroll when snapshots only change a label.
function renderMarkup(container, markup) {
  if (renderedMarkup.get(container) === markup) return
  const focus = container.contains(document.activeElement) ? document.activeElement : null
  const selection = typeof focus?.selectionStart === 'number' ? [focus.selectionStart, focus.selectionEnd, focus.selectionDirection] : null
  const owner = focus?.closest('[data-task-id]')
  const attributes = ['data-task-action', 'data-task-toggle', 'data-request-id', 'data-request-action', 'data-resident-id', 'data-holo-save', 'data-holo-open', 'data-avatar-select', 'data-avatar-clear', 'data-persona-select', 'data-persona-clear', 'data-resident-save', 'data-resident-setting', 'data-field']
  const control = focus && attributes.filter(name => focus.hasAttribute(name)).map(name => `[${name}="${CSS.escape(focus.getAttribute(name))}"]`).join('')
  const selector = focus?.id ? `#${CSS.escape(focus.id)}` : focus
    ? control || (owner && focus.matches('summary'))
      ? `${owner ? `[data-task-id="${CSS.escape(owner.dataset.taskId)}"] ` : ''}${control || 'summary'}` : null
    : null
  const opened = [...container.querySelectorAll('[data-task-id] details[open]')].map(node => node.closest('[data-task-id]').dataset.taskId)
  const scrollTop = container.scrollTop
  container.innerHTML = markup
  renderedMarkup.set(container, markup)
  for (const id of opened) container.querySelector(`[data-task-id="${CSS.escape(id)}"] details`)?.setAttribute('open', '')
  container.scrollTop = scrollTop
  if (selector?.trim()) {
    const nextFocus = container.querySelector(selector)
    nextFocus?.focus({ preventScroll: true })
    if (selection && nextFocus?.setSelectionRange) nextFocus.setSelectionRange(...selection)
  }
}

function rejectionMessage(message) {
  const reasons = [
    ['unfinished runs', '実行中または開始待ちの作業が残っています。Activityを確認してください。'],
    ['active Holo Turn', 'Holoが応答中です。応答が終わってから操作してください。'],
    ['unhandled Master instructions', 'まだ処理されていない指示があります。'],
    ['pending Approvals', '承認待ちの操作を解決してから完了を確定してください。'],
    ['unresolved side effects', '実行結果や停止処理の確認が残っています。'],
    ['invalid workspace_scope', '作業フォルダーは絶対パスで指定してください。'],
    ['completion evidence', '完了を確認する成果物・検証結果が不足しています。'],
    ['required verification', '必須の検証結果を確認できません。'],
    ['current content', '成果物の版が検証した版と一致しません。'],
    ['stale revision', '状態が更新されました。最新の表示を確認して操作してください。'],
    ['stale request revision', 'このCHECKは更新されています。最新の内容を確認してください。'],
    ['no initial instruction', '最初の指示を入力してください。'],
  ]
  return reasons.find(([text]) => message.includes(text))?.[1] ?? message
}

async function reconcileUncertainCommand() {
  if (!uncertainCommandId || !bridge || commandBusy || reconcilingCommand) return
  reconcilingCommand = true
  try {
    const receipt = await bridge.commandReceipt(uncertainCommandId)
    if (receipt) {
      showNotice('直前の操作はHubに受理済みでした。最新状態へ同期しました。')
    } else {
      showNotice('直前の操作はHubに記録されていません。必要なら再実行できます。', true)
    }
    uncertainCommandId = null
    localStorage.removeItem('nirai:v2:uncertain-command-id')
  } catch {
    // 接続が戻るまでは受付結果を確定しない。
  } finally {
    reconcilingCommand = false
    renderAll()
  }
}

async function sendCommand(type, payload, expectedRevision) {
  if (!bridge || commandBusy || uncertainCommandId || !dashboardConnected) return null
  const hadChatFocus = ['chatInput', 'usualChatInput'].includes(document.activeElement?.id) ? document.activeElement.id : null
  const envelope = commandEnvelope(type, payload, expectedRevision)
  commandBusy = true
  $('dashboard').setAttribute('aria-busy', 'true')
  renderAll()
  let acceptedResult = null
  try {
    // Reload may happen before the IPC Promise settles. This is only a receipt
    // lookup key; Hub remains the authority for whether the command was accepted.
    uncertainCommandId = envelope.command_id
    localStorage.setItem('nirai:v2:uncertain-command-id', uncertainCommandId)
    const result = await bridge.command(envelope)
    acceptedResult = result
    uncertainCommandId = null
    localStorage.removeItem('nirai:v2:uncertain-command-id')
    const latest = await bridge.snapshot()
    applySnapshot(latest)
    return result
  } catch (error) {
    const message = error?.message ?? String(error)
    if (acceptedResult) {
      restoreChatFocus ||= hadChatFocus
      dashboardConnected = false
      showNotice('操作は受理済みですが、表示の更新に失敗しました。再読込して最新状態を取得してください。', true)
      renderAll()
      return acceptedResult
    }
    if (message.includes('transport:')) {
      uncertainCommandId = envelope.command_id
      localStorage.setItem('nirai:v2:uncertain-command-id', uncertainCommandId)
      restoreChatFocus ||= hadChatFocus
      dashboardConnected = false
      showNotice('Hubとの通信が切れ、直前操作の受付結果が不明です。再送せず、再接続後に照合します。', true)
      renderAll()
    } else {
      uncertainCommandId = null
      localStorage.removeItem('nirai:v2:uncertain-command-id')
      showNotice(rejectionMessage(message), true)
      try {
        applySnapshot(await bridge.snapshot())
      } catch (refreshError) {
        dashboardConnected = false
        showNotice(
          `${message} / 最新状態の再取得にも失敗しました: ${refreshError?.message ?? refreshError}`,
          true,
        )
        renderAll()
      }
    }
    return null
  } finally {
    commandBusy = false
    $('dashboard').setAttribute('aria-busy', 'false')
    renderAll()
    const input = hadChatFocus && $(hadChatFocus)
    if (input && dashboardConnected && !input.disabled && input.getClientRects().length && [input, document.body].includes(document.activeElement)) input.focus({ preventScroll: true })
  }
}

function getResident(id) {
  return residents.find((resident) => resident.id === id) ?? null
}

function residentName(id) {
  return getResident(id)?.name ?? id ?? 'Resident'
}

function getTask(id) {
  return tasks.find((task) => task.id === id) ?? null
}

function getSelectedTask() {
  return expandedTaskId ? getTask(expandedTaskId) : null
}

function mapRunState(state) {
  if (state === 'Pending') return 'Waiting'
  return state
}

function taskType(task) {
  const title = `${task.title} ${task.objective ?? ''}`.toLowerCase()
  if (title.includes('review') || title.includes('監査')) return 'review'
  if (title.includes('memory')) return 'memory'
  if (title.includes('resume')) return 'automation'
  if (title.includes('調査') || title.includes('research')) return 'research'
  if (title.includes('nirai')) return 'control'
  return 'general'
}

function applySnapshot(next) {
  if (next && Number(next.revision) < Number(snapshot.revision ?? 0)) return
  const wasConnected = dashboardConnected
  snapshot = next ?? snapshot
  const rawResidents = Array.isArray(snapshot.residents) ? snapshot.residents : []
  residents = rawResidents.map((resident) => ({
    id: resident.id,
    name: resident.display_name ?? resident.id,
    role: resident.role ?? (resident.id === 'holo' ? '指揮者' : ''),
    ai: resident.id === 'holo' ? 'Holo Addon' : resident.capability_id ?? '未接続',
    model: resident.model ?? '',
    persona: resident.persona_path ?? null,
    capabilityId: resident.capability_id ?? null,
    avatar: snapshot.settings?.value?.resident_avatars?.[resident.id] ?? null,
    online: resident.id === 'holo' ? ['ready', 'busy'].includes(snapshot.conversation_providers?.find(provider => provider.id === 'holo')?.availability?.state ?? snapshot.holo?.state)
      : snapshot.conversation_providers?.find(provider => provider.id === resident.capability_id)?.availability?.state === 'ready',
    connectionLabel: snapshot.verification_mode ? '検証用Capability' : resident.id === 'holo'
      ? snapshot.conversation_providers?.find(provider => provider.id === 'holo')?.availability?.reason ?? snapshot.holo?.reason ?? '未接続' : snapshot.conversation_providers?.find(provider => provider.id === resident.capability_id)?.availability?.reason ?? '未接続',
    shortLimit: { label: '現在', remaining: null, reset: '--' },
    longLimit: { label: '長期', remaining: null, reset: '--' },
  }))
  if (!residents.some((resident) => resident.id === selectedResidentId)) {
    selectedResidentId = residents[0]?.id ?? null
  }

  const requests = Array.isArray(snapshot.pending_requests) ? snapshot.pending_requests : []
  const turns = Array.isArray(snapshot.holo_turns) ? snapshot.holo_turns : []
  const runs = Array.isArray(snapshot.runs) ? snapshot.runs : []
  const messages = Array.isArray(snapshot.messages) ? snapshot.messages : []

  tasks = (snapshot.tasks ?? []).map((task) => {
    const taskMessages = messages
      .filter((message) => message.conversation_id === task.conversation_id && message.sender !== 'control')
      .map((message) => ({
        id: message.id,
        role: message.sender === 'master' ? 'master' : message.sender === 'system' ? 'system' : 'agent',
        who: message.sender === 'master' ? 'Master' : message.sender === 'system' ? 'Nirai' : residentName(message.sender),
        text: message.content,
        time: timeLabel(message.created_at),
      }))

    const taskRequests = requests.filter((request) => request.task_id === task.id)
    const taskTurns = turns.filter((turn) => turn.task_id === task.id)
    const taskRuns = runs.filter((run) => run.task_id === task.id)
    const activities = [
      ...taskTurns.map((turn) => ({
        id: `turn-${turn.id}`,
        title: `${residentName(task.resident_id)} Turn`,
        status: turn.ended_at ? (turn.end_reason === 'assistant' ? 'Completed' : 'Interrupted') : turn.completion_summary ? 'Waiting' : 'Running',
        agent: task.resident_id,
        note: turn.ended_at ? (turn.end_reason ?? '終了') : turn.completion_summary ? '最終回答をCHATへ反映中' : '処理中',
      })),
      ...taskRuns.map((run) => ({
        id: run.id,
        title: run.operation,
        status: mapRunState(run.state),
        agent: task.resident_id,
        note: run.stop_requested_at && run.state === 'Running' ? '停止処理中'
          : run.effects === 'unknown' || run.cleanup_state !== 'clear' ? '実結果・後始末を確認中'
          : run.error_json
          ? (() => { try { return JSON.parse(run.error_json).message ?? 'エラー' } catch { return 'エラー' } })()
          : run.state === 'Interrupted'
            ? '中断・確認待ち'
            : run.state === 'Pending'
              ? '開始待ち'
              : (() => { try { return JSON.parse(run.result_json ?? '{}').value?.summary ?? run.state } catch { return run.state } })(),
      })),
    ]

    for (const request of taskRequests) {
      activities.unshift({
        id: `request-${request.id}`,
        title: '承認待ち',
        status: 'Waiting',
        attention: true,
        agent: task.resident_id,
        note: request.prompt,
      })
    }

    return {
      ...task,
      type: taskType(task),
      status: task.state,
      agents: [task.resident_id],
      updated: relativeLabel(task.updated_at),
      completedAt: task.ended_at ? new Date(task.ended_at).getTime() : null,
      draft: !task.initial_message_id,
      resumeEnabled: Boolean(task.resume_enabled),
      attention: taskRequests.length > 0,
      requests: taskRequests,
      artifacts: (snapshot.artifacts ?? []).filter(item => item.task_id === task.id),
      activities,
      messages: taskMessages,
    }
  })

  if (expandedTaskId && !sortedTasks().some(task => task.id === expandedTaskId)) expandedTaskId = null
  if (!expandedTaskId) expandedTaskId = sortedTasks()[0]?.id ?? null
  dashboardConnected = true
  renderAll()
  if (!wasConnected && restoreChatFocus) {
    const focusId = restoreChatFocus
    restoreChatFocus = false
    requestAnimationFrame(() => {
      const input = $(focusId)
      if (input && !input.disabled && input.getClientRects().length) input.focus()
    })
  }
}

function isTaskVisible(task, now = Date.now()) {
  if (!TERMINAL_STATES.has(task.status)) return true
  if (closedTerminalTaskIds.has(task.id)) return false
  if (task.status !== 'Completed' || !Number.isFinite(task.completedAt)) return true
  return now - task.completedAt < TERMINAL_VISIBLE_MS
}

function visibleTasks(now = Date.now()) {
  return tasks.filter(task => isTaskVisible(task, now))
}

function scheduleTerminalExpiry() {
  clearTimeout(terminalExpiryTimer)
  const now = Date.now()
  const nextExpiry = tasks
    .filter(task => task.status === 'Completed' && !closedTerminalTaskIds.has(task.id) && Number.isFinite(task.completedAt))
    .map(task => task.completedAt + TERMINAL_VISIBLE_MS)
    .filter(expiresAt => expiresAt > now)
    .sort((a, b) => a - b)[0]
  if (!nextExpiry) return
  terminalExpiryTimer = setTimeout(() => renderAll(), Math.min(nextExpiry - now + 50, 2_147_000_000))
}

function sortedTasks() {
  return visibleTasks().sort((a, b) => {
    const diff = (taskStatusOrder[a.status] ?? 99) - (taskStatusOrder[b.status] ?? 99)
    if (diff !== 0) return diff
    return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
  })
}

function sortedActivities(task) {
  return [...task.activities].sort((a, b) => {
    const diff = (activityStatusOrder[a.status] ?? 99) - (activityStatusOrder[b.status] ?? 99)
    if (diff !== 0) return diff
    return a.title.localeCompare(b.title, 'ja')
  })
}

function statusDotClass(status) {
  if (status === 'Failed' || status === 'Interrupted' || status === 'Cancelled') return 'status-paused'
  return `status-${String(status).toLowerCase()}`
}

function taskTypeIcon(type) {
  return TASK_TYPE_ICONS[type] ?? TASK_TYPE_ICONS.general
}

function limitRow(limit) {
  const known = Number.isFinite(limit.remaining)
  return `
    <span class="limit-row">
      <span>${escapeHtml(limit.label)}</span>
      <span class="limit-track"><i style="width:${known ? limit.remaining : 0}%"></i></span>
      <strong>${known ? `${limit.remaining}%` : '不明'}</strong>
      <small>${escapeHtml(limit.reset)}</small>
    </span>
  `
}

function renderResidents() {
  const strip = $('residentStrip')
  const previousScrollLeft = strip.scrollLeft
  renderMarkup(strip, residents.map((resident) => {
    const isSelected = resident.id === selectedResidentId
    return `
      <button
        type="button"
        class="resident-chip glass-soft selectable-surface${isSelected ? ' is-selected' : ''}"
        data-resident-id="${escapeHtml(resident.id)}"
        aria-pressed="${isSelected}"
      >
        <span class="resident-headline">
          <span class="resident-state-dot state-${resident.online ? 'online' : 'offline'}"></span>
          <strong>${escapeHtml(resident.name)}</strong>
          <span class="resident-state-text" title="${escapeHtml(resident.connectionLabel)}">${escapeHtml(resident.connectionLabel)}</span>
        </span>
        <span class="resident-limits">
          ${resident.shortLimit.remaining === null && resident.longLimit.remaining === null
            ? '<span class="usage-unknown">使用量 未取得</span>'
            : limitRow(resident.shortLimit) + limitRow(resident.longLimit)}
        </span>
      </button>
    `
  }).join(''))

  requestAnimationFrame(() => {
    const maxScrollLeft = Math.max(0, strip.scrollWidth - strip.clientWidth)
    strip.scrollLeft = Math.min(previousScrollLeft, maxScrollLeft)
    updateResidentScrollFade()
  })
}

function avatarAvailability(residentId) {
  const avatar = snapshot.avatar_states?.find(item => item.resident_id === residentId)
  if (!avatar || avatar.status !== 'ready') return '表示を確認できると、本人が表情や衣装を選べます。'
  const expressions = avatar.capabilities?.expressions.length ?? 0
  const wardrobe = avatar.capabilities?.wardrobe.length ?? 0
  const controls = avatar.capabilities?.controls?.length ?? 0
  return `本人が選べる表情 ${expressions} 種・衣装部品 ${wardrobe} 点・外見 ${controls} 項目`
}

function renderResidentSettings() {
  if ($('residentSettingsPanel').hidden) return
  if (['holoAppName', 'workspaceScope'].includes(document.activeElement?.id)) return
  holoSettingsRevision = snapshot.settings?.revision
  renderMarkup($('residentSettingsList'), residents.map((resident) => `
    <article class="resident-settings-column">
      <button class="resident-name-button" type="button" disabled>
        <span class="resident-state-dot state-${resident.online ? 'online' : 'offline'}"></span>
        <strong>${escapeHtml(resident.name)}</strong>
      </button>
      <div class="resident-settings-fields">
        ${resident.id === 'holo' ? `
          <label class="resident-setting-row"><span>Role</span><select disabled><option>${escapeHtml(resident.role)}</option></select></label>
          <label class="resident-setting-row"><span>AI</span><select disabled><option>${escapeHtml(resident.ai)}</option></select></label>
          <label class="resident-setting-row"><span>Model</span><select disabled><option>${escapeHtml(resident.model || '-')}</option></select></label>
          <button type="button" data-holo-open ${!dashboardConnected ? 'disabled' : ''}>ChatGPTを開く</button>` : `
          <label class="resident-setting-row"><span>名前</span><input data-resident-setting="${escapeHtml(resident.id)}" data-field="display_name" maxlength="80" value="${escapeHtml(residentSettingValue(resident, 'display_name'))}"></label>
          <label class="resident-setting-row"><span>役割</span><input data-resident-setting="${escapeHtml(resident.id)}" data-field="role" maxlength="200" value="${escapeHtml(residentSettingValue(resident, 'role'))}"></label>
          <label class="resident-setting-row"><span>会話の接続</span><select data-resident-setting="${escapeHtml(resident.id)}" data-field="capability_id">${conversationProviderOptions(residentSettingValue(resident, 'capability_id'))}</select></label>
          <label class="resident-setting-row"><span>Model</span><input data-resident-setting="${escapeHtml(resident.id)}" data-field="model" maxlength="120" value="${escapeHtml(residentSettingValue(resident, 'model'))}"></label>
          <div class="resident-setting-row"><span>Persona</span><div class="avatar-setting"><small>${escapeHtml(resident.persona?.split(/[\\/]/).at(-1) ?? '未設定')}</small><div class="avatar-setting-buttons"><button type="button" data-persona-select="${escapeHtml(resident.id)}">ファイルを選択</button>${resident.persona ? `<button type="button" data-persona-clear="${escapeHtml(resident.id)}">参照を外す</button>` : ''}</div></div></div>
          <button type="button" data-resident-save="${escapeHtml(resident.id)}" ${!dashboardConnected || commandBusy ? 'disabled' : ''}>保存</button>`}
        <div class="resident-setting-row"><span>Avatar</span><div class="avatar-setting"><small>${escapeHtml(resident.avatar?.split(/[\\/]/).at(-1) ?? '未設定')}</small><div class="avatar-setting-buttons"><button type="button" data-avatar-select="${escapeHtml(resident.id)}">VRMを選択</button>${resident.avatar ? `<button type="button" data-avatar-clear="${escapeHtml(resident.id)}">表示を外す</button>` : ''}</div></div></div>
        ${resident.avatar ? `<p class="setting-hint">${escapeHtml(avatarAvailability(resident.id))}</p>` : ''}
        ${resident.id === 'holo' ? `<label class="resident-setting-row"><span>Task用接続名</span><input id="holoAppName" maxlength="64" autocomplete="off" placeholder="nirai-v2" value="${escapeHtml(holoAppDraft ?? snapshot.settings?.value?.holo_app_name ?? '')}"></label>
          <p class="setting-hint">Taskで使うNirai用アプリの名前です。通常会話では不要です。</p>
          <label class="resident-setting-row"><span>作業フォルダー</span><input id="workspaceScope" autocomplete="off" placeholder="D:\\Products\\Nirai\\v2" value="${escapeHtml(workspaceDraft ?? snapshot.settings?.value?.workspace_scope ?? '')}"></label>
          <p class="setting-hint">新しく作るTaskでHoloが読み書き・検証できる範囲です。既存Taskには反映しません。</p>
          <button type="button" data-holo-save ${!dashboardConnected || commandBusy ? 'disabled' : ''}>設定を保存</button>` : ''}
      </div>
    </article>
  `).join(''))
  $('addResidentButton').disabled = !dashboardConnected || commandBusy
}

function residentSettingValue(resident, field) {
  const draft = residentDrafts.get(resident.id)
  if (draft && Object.hasOwn(draft, field)) return draft[field]
  return field === 'display_name' ? resident.name : field === 'capability_id' ? resident.capabilityId ?? '' : resident[field] ?? ''
}

function conversationProviderOptions(selected) {
  const providers = (snapshot.conversation_providers ?? []).filter(provider => provider.id !== 'holo')
  if (selected === 'holo') selected = ''
  return `<option value=""${!selected ? ' selected' : ''}>未接続</option>`
    + (selected && !providers.some(provider => provider.id === selected) ? `<option value="${escapeHtml(selected)}" selected>${escapeHtml(selected)}（未接続）</option>` : '')
    + providers.map(provider => `<option value="${escapeHtml(provider.id)}"${provider.id === selected ? ' selected' : ''}>${escapeHtml(provider.display_name ?? provider.id)}${provider.availability?.state === 'ready' ? '' : '（未接続）'}</option>`).join('')
}

function renderEdgeStats() {
  $('edgeRunning').textContent = tasks.filter((task) => task.status === 'Running').length
  $('edgeCheck').textContent = tasks.filter((task) => task.attention).length
  $('edgePaused').textContent = tasks.filter((task) => task.status === 'Paused').length
  $('edgeCompleted').textContent = visibleTasks().filter((task) => task.status === 'Completed').length
}

function taskPreviewText(task) {
  const last = [...task.messages].reverse().find((message) => message.role !== 'system')
  if (last?.text) return last.text
  if (TERMINAL_STATES.has(task.status)) return 'Chat履歴なし'
  return task.draft
    ? task.resident_id === 'holo' ? 'ChatGPTから指示してください' : 'Chatから指示してください'
    : 'Chat履歴なし'
}

function taskListRowMarkup(task) {
  const baseStateText = TERMINAL_STATES.has(task.status) ? statusLabel[task.status]
    : task.draft
    ? '入力待ち'
    : task.attention
      ? `${statusLabel[task.status] ?? task.status} · 承認待ち`
      : statusLabel[task.status] ?? task.status
  const stateMarkup = task.status === 'Running'
    ? `<span class="task-list-status-running">${escapeHtml(baseStateText)}</span>`
    : escapeHtml(baseStateText)
  const resumeMarkup = task.resumeEnabled ? '<span class="task-list-resume"> · Resume</span>' : ''
  const dotClass = task.attention ? 'state-waiting' : statusDotClass(task.status)

  return `
    <span class="task-type-icon" aria-hidden="true">${taskTypeIcon(task.type)}</span>
    <span class="task-list-main">
      <strong>${escapeHtml(task.title)}</strong>
      <small>${escapeHtml(taskPreviewText(task))}</small>
    </span>
    <span class="task-list-state">
      <span class="task-list-status">
        <span class="task-status-dot ${dotClass}"></span>
        <span>${stateMarkup}${resumeMarkup}</span>
      </span>
      <small class="task-list-timestamp">${escapeHtml(task.status === 'Completed' ? completedAgeLabel(task.ended_at) : task.updated)}</small>
    </span>
  `
}

function activityMarkup(activity) {
  const dotClass = activity.attention ? 'state-waiting' : statusDotClass(activity.status)
  const label = activity.attention ? 'Check' : statusLabel[activity.status] ?? activity.status
  return `
    <div class="nested-task-row">
      <span class="task-status-dot ${dotClass}"></span>
      <span class="nested-task-main">
        <strong>${escapeHtml(activity.title)}</strong>
        <small>${escapeHtml(activity.note)}</small>
      </span>
      <span class="nested-task-agent">${escapeHtml(residentName(activity.agent))}</span>
      <span class="nested-task-status">${escapeHtml(label)}</span>
    </div>
  `
}

function requestMarkup(request) {
  const proposalData = request.proposal_json ? (() => {
    try { return JSON.parse(request.proposal_json) } catch { return null }
  })() : null
  const proposal = request.proposal_json ? (() => {
    try { return JSON.stringify(proposalData, null, 2) } catch { return request.proposal_json }
  })() : ''
  return `
    <div class="master-request-card">
      <strong>CHECK · 承認</strong>
      <small class="request-id">Request: ${escapeHtml(request.id)}</small>
      <p>${escapeHtml(request.prompt)}</p>
      ${proposal ? `<pre>${escapeHtml(proposal)}</pre>` : ''}
      <div class="completed-task-actions">
        <button type="button" class="restart-task-button" data-request-action="approve" data-request-id="${escapeHtml(request.id)}">承認</button>
        <button type="button" class="close-task-button" data-request-action="reject" data-request-id="${escapeHtml(request.id)}">却下</button>
      </div>
    </div>
  `
}

function taskActionsMarkup(task) {
  if (TERMINAL_STATES.has(task.status)) {
    return `
      <div class="completed-task-actions">
        ${task.status === 'Completed' ? '<button type="button" class="restart-task-button" data-task-action="restart">再開</button>' : ''}
        <button type="button" class="close-task-button" data-task-action="close">閉じる</button>
      </div>
    `
  }
  if (task.draft) {
    return `
      <div class="completed-task-actions">
        <button type="button" class="close-task-button" data-task-action="cancel">Taskを取り消す</button>
      </div>
    `
  }
  return `
    <div class="completed-task-actions">
      <button type="button" class="complete-task-button" data-task-action="complete">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9.2 16.2-4.1-4.1L3.7 13.5l5.5 5.5L20.7 7.5l-1.4-1.4-10.1 10.1Z" /></svg>
        <span>完了を確定</span>
      </button>
      <button type="button" class="close-task-button" data-task-action="cancel">Taskを取り消す</button>
    </div>
  `
}

function renderTaskAccordion() {
  renderMarkup($('taskAccordion'), sortedTasks().map((task) => {
    const isExpanded = task.id === expandedTaskId
    const activities = task.activities.length
      ? sortedActivities(task).map(activityMarkup).join('')
      : '<div class="activity-empty">Activityはまだありません</div>'
    const requests = task.requests.map(requestMarkup).join('')
    return `
      <article class="work-item selectable-surface${isExpanded ? ' is-selected' : ''}" data-task-id="${escapeHtml(task.id)}">
        <button type="button" class="task-list-row work-summary" data-task-toggle aria-expanded="${isExpanded}">
          ${taskListRowMarkup(task)}
        </button>
        <div class="work-detail" ${isExpanded ? '' : 'hidden'}>
          <div class="work-detail-toolbar"><small>作業の記録 · ${task.activities.length}</small></div>
          ${requests}
          <div class="nested-task-list">${activities}</div>
          ${task.artifacts.length ? `<details class="task-result"><summary>成果物の記録</summary>${task.artifacts.map(item => `<p>${escapeHtml(item.ref)}<br>内容指紋: ${escapeHtml(item.fingerprint)}</p>`).join('')}</details>` : ''}
          ${taskActionsMarkup(task)}
        </div>
      </article>
    `
  }).join('') || '<div class="activity-empty">まだTaskがありません。<br>右上の ＋ から始めましょう。</div>')
  requestAnimationFrame(updateTaskScrollFade)
}

function canPresentHolo(task = getSelectedTask()) {
  return Boolean(task?.resident_id === 'holo' && dashboardConnected
    && $('holoChatPanel').hidden
    && $('dashboard').classList.contains('is-open')
    && $('residentSettingsPanel').hidden && $('residentDeleteConfirm').hidden
    && !$('holoSurface').hidden && $('holoSurface').getClientRects().length > 0)
}

function clearHoloPresentation() {
  holoPresentation = null
  holoSurfaceVisible = false
  holoPresentationRevision += 1
  $('holoLoadingMessage').textContent = ''
  $('holoPresentation').hidden = true
  delete $('holoPresentation').dataset.taskId
  delete $('holoPresentation').dataset.phase
}

function renderHoloPresentation(task = getSelectedTask()) {
  if (!canPresentHolo(task)) {
    if (holoPresentation || holoSurfaceVisible) clearHoloPresentation()
    return
  }
  const presentation = holoPresentation?.task_id === task.id ? holoPresentation : null
  const show = Boolean(presentation && !presentation.visible && presentation.phase === 'loading'
    && snapshot.holo?.state !== 'unavailable')
  const placeholder = $('holoPresentation')
  placeholder.hidden = !show
  if (show) {
    placeholder.dataset.taskId = task.id
    placeholder.dataset.phase = presentation.phase
    $('holoLoadingMessage').textContent = '会話を読み込んでいます…'
  } else {
    delete placeholder.dataset.taskId
    delete placeholder.dataset.phase
    $('holoLoadingMessage').textContent = ''
  }
}

function applyHoloPresentation(presentation) {
  if (!$('holoChatPanel').hidden && presentation?.task_id === null && typeof presentation.visible === 'boolean') {
    holoChatPresentation = { visible: presentation.visible, phase: presentation.phase,
      error: typeof presentation.error === 'string' ? presentation.error : null }
    holoPresentationRevision += 1
    renderHoloChatStatus()
    return
  }
  const task = getSelectedTask()
  if (!canPresentHolo(task) || presentation?.task_id !== task.id || typeof presentation.visible !== 'boolean') return
  const phase = presentation.phase === 'preparing' ? 'preparing'
    : !presentation.visible && presentation.phase === 'loading' ? 'loading' : null
  holoPresentation = { task_id: task.id, visible: presentation.visible, phase }
  holoSurfaceVisible = presentation.visible
  holoPresentationRevision += 1
  renderHoloPresentation(task)
  renderHoloSurfaceStatus(task)
}

function renderHoloChatStatus() {
  if ($('holoChatPanel').hidden) return
  const status = $('holoChatStatus')
  status.hidden = dashboardConnected && !snapshot.verification_mode && holoChatPresentation?.visible === true
  status.textContent = !dashboardConnected ? 'Hubへ接続中…'
    : snapshot.verification_mode ? '検証構成 · 実ChatGPTへの接続なし'
    : holoChatPresentation?.error ?? 'ChatGPTを読み込んでいます…'
  status.setAttribute('role', holoChatPresentation?.error ? 'alert' : 'status')
}

function renderHoloSurfaceStatus(task = getSelectedTask()) {
  const status = $('holoSurfaceStatus')
  const mismatch = snapshot.holo?.reason === '選択中TaskのConversationではありません'
  const presentation = holoPresentation?.task_id === task?.id ? holoPresentation : null
  const unavailable = snapshot.holo?.state === 'unavailable'
  const preparing = presentation?.phase === 'preparing'
  const loading = !holoSurfaceVisible && presentation?.phase === 'loading'
  status.hidden = task?.resident_id !== 'holo'
    || (!mismatch && !unavailable && !preparing && (holoSurfaceVisible || loading))
  status.textContent = mismatch ? '選択中のTaskとは別のChatGPT会話を表示しています。'
    : snapshot.verification_mode ? '検証構成 · 実ChatGPTへの接続なし'
    : unavailable ? snapshot.holo?.reason ?? 'Holoへ接続できません'
    : preparing ? '送信中…'
    : !holoSurfaceVisible && ['ready', 'busy'].includes(snapshot.holo?.state) ? 'Holo画面を準備しています…'
    : snapshot.holo?.reason ?? 'Holoへ接続しています…'
}

function renderChat(task) {
  const pane = $('chatPane')
  const surface = $('holoSurface')
  const form = $('chatForm')
  const input = $('chatInput')
  const send = form.querySelector('.send-button')
  const pauseButton = $('pauseButton')
  const resumeButton = $('resumeButton')
  const messages = $('chatMessages')
  const holo = task?.resident_id === 'holo'

  if (displayedInputTaskId !== (task?.id ?? null)) {
    if (displayedInputTaskId) chatDrafts.set(displayedInputTaskId, input.value)
    displayedInputTaskId = task?.id ?? null
    input.value = chatDrafts.get(displayedInputTaskId) ?? ''
    resizeComposer()
    chatRenderSignature = null
    clearHoloPresentation()
  }

  pane.classList.toggle('is-holo', Boolean(holo))
  surface.hidden = !holo
  renderHoloPresentation(task)
  renderHoloSurfaceStatus(task)

  if (!task) {
    $('chatTaskTitle').textContent = 'Taskを選択'
    $('chatTaskMeta').textContent = dashboardConnected ? '新しいTaskを作成できます' : 'Hubへ接続中'
    messages.innerHTML = '<div class="chat-empty"><strong>海を眺めながら、次の仕事を。</strong><small>一覧からTaskを選ぶか、右上の ＋ で作成できます。</small></div>'
    input.disabled = true
    send.disabled = true
    pauseButton.hidden = true
    resumeButton.hidden = true
    form.classList.add('is-disabled')
    return
  }

  $('chatTaskTitle').textContent = task.title
  $('chatTaskTitle').title = task.title
  const agentNames = task.agents.map(residentName).join(' / ')
  $('chatTaskMeta').textContent = holo
    ? `${agentNames} · ${statusLabel[task.status] ?? task.status}`
    : task.draft
      ? `${agentNames} · 入力待ち`
      : `${agentNames} · ${task.updated}更新`

  const terminal = ['Completed', 'Failed', 'Cancelled'].includes(task.status)
  input.placeholder = terminal ? '終了したTask' : task.draft ? '最初の指示を入力' : '追加の指示を入力'
  input.disabled = holo || terminal || !dashboardConnected
  send.disabled = holo || terminal || !dashboardConnected
  form.classList.toggle('is-disabled', holo || terminal || !dashboardConnected)

  const supportsControls = !terminal && !task.draft
  pauseButton.hidden = !supportsControls
  pauseButton.textContent = task.status === 'Running' ? 'Pause' : '再開'
  pauseButton.title = task.status === 'Running' ? 'このTaskの実行を一時停止' : 'このTaskの実行を再開'
  resumeButton.hidden = !supportsControls || task.resident_id !== 'holo'
  resumeButton.textContent = task.resumeEnabled ? 'Resume ON' : 'Resume OFF'
  resumeButton.classList.toggle('is-active', !resumeButton.hidden && task.resumeEnabled)
  resumeButton.setAttribute('aria-pressed', String(!resumeButton.hidden && task.resumeEnabled))
  resumeButton.title = 'ONにすると、応答が終わった後も未完了のTaskを自動で続けます'

  if (holo) return

  const signature = JSON.stringify([task.id, task.messages])
  if (signature === chatRenderSignature) return
  const followLatest = chatRenderSignature === null || messages.scrollHeight - messages.clientHeight - messages.scrollTop < 48
  const previousScroll = messages.scrollTop
  chatRenderSignature = signature

  messages.innerHTML = ''
  if (!task.messages.length) {
    messages.innerHTML = terminal
      ? '<div class="chat-empty">このTaskにはChat履歴がありません</div>'
      : '<div class="chat-empty">下から最初の指示を送ってください。</div>'
  } else {
    for (const message of task.messages) {
      const article = document.createElement('article')
      article.className = `message ${message.role}`
      article.innerHTML = `
        <div class="message-meta">${escapeHtml(message.who)} · ${escapeHtml(message.time)}</div>
        <div class="message-bubble">${escapeHtml(message.text)}</div>
      `
      messages.appendChild(article)
    }
  }
  requestAnimationFrame(() => { messages.scrollTop = followLatest ? messages.scrollHeight : previousScroll })
}

function holoSurfaceSpec() {
  if (!$('holoChatPanel').hidden) {
    const surface = $('holoChatSurface')
    const visible = dashboardConnected && surface.getClientRects().length > 0
    const rect = visible ? surface.getBoundingClientRect() : null
    const payload = {
      mode: 'chat', visible, task_id: null,
      bounds: rect ? { x: Math.round(rect.x), y: Math.round(rect.y), width: Math.max(1, Math.round(rect.width)), height: Math.max(1, Math.round(rect.height)) } : null,
    }
    return { payload, signature: JSON.stringify(payload) }
  }
  const task = getSelectedTask()
  const surface = $('holoSurface')
  const isHolo = task?.resident_id === 'holo'
  const visible = Boolean(
    isHolo
    && dashboardConnected
    && !surface.hidden
    && $('dashboard').classList.contains('is-open')
    && $('residentSettingsPanel').hidden
    && $('residentDeleteConfirm').hidden
    && surface.getClientRects().length > 0
  )
  const rect = visible ? surface.getBoundingClientRect() : null
  const payload = {
    mode: visible ? 'task' : 'chat',
    visible,
    task_id: visible ? task.id : null,
    bounds: rect ? {
      x: Math.round(rect.x),
      y: Math.round(rect.y),
      width: Math.max(1, Math.round(rect.width)),
      height: Math.max(1, Math.round(rect.height)),
    } : null,
  }
  return {
    payload,
    signature: JSON.stringify({ ...payload, task_state: visible ? task.status : null }),
  }
}

function scheduleHoloSurfaceSync() {
  if (!bridge?.holoSurface) return
  requestAnimationFrame(() => {
    const { payload, signature } = holoSurfaceSpec()
    if (!payload.visible || (payload.mode === 'chat' && (holoPresentation || holoSurfaceVisible))) clearHoloPresentation()
    if (signature === holoSurfaceSignature) return
    const mode = payload.mode ?? 'task'
    const taskChanged = payload.task_id !== holoSurfaceTaskId || mode !== holoSurfaceMode
    holoSurfaceTaskId = payload.task_id
    holoSurfaceMode = mode
    holoSurfaceSignature = signature
    if (taskChanged || !payload.visible) {
      // Hide immediately; an older conversation load can still be waiting in
      // the serial queue when the Master selects another Task or closes it.
      void Promise.resolve(bridge.holoHide?.()).catch(error => {
        showNotice(`Holo画面を隠せません: ${error?.message ?? error}`, true)
      })
    }
    holoSurfaceSerial = holoSurfaceSerial
      .then(async () => {
        if (signature !== holoSurfaceSignature) return
        const presentationRevision = holoPresentationRevision
        const result = await bridge.holoSurface(payload)
        if (payload.mode === 'chat') {
          if (signature !== holoSurfaceSignature || $('holoChatPanel').hidden) return
          if (!result?.blocked && presentationRevision !== holoPresentationRevision) return
          applyHoloPresentation(result?.blocked
            ? { task_id: null, visible: false, phase: null, error: result.reason ?? 'ChatGPTの入力状態を確認してください' }
            : result?.presentation ?? { task_id: null, visible: result?.visible === true, phase: null })
          return
        }
        // A native transition can arrive while IPC is pending. Its newer
        // presentation must not be replaced by the earlier response.
        if (presentationRevision !== holoPresentationRevision
          || payload.task_id !== getSelectedTask()?.id || !payload.visible || !canPresentHolo()) return
        if (result?.presentation) applyHoloPresentation(result.presentation)
        else applyHoloPresentation({ task_id: payload.task_id, visible: result?.visible === true, phase: null })
      })
      .catch((error) => {
        if (signature !== holoSurfaceSignature) return
        holoSurfaceSignature = null
        if (payload.mode === 'chat') {
          if ($('holoChatPanel').hidden) return
          holoChatPresentation = { visible: false, error: `ChatGPTを開けません: ${error?.message ?? error}` }
          renderHoloChatStatus()
          return
        }
        if (payload.task_id !== getSelectedTask()?.id) return
        if (payload.visible) showNotice(`Holo画面を更新できません: ${error?.message ?? error}`, true)
      })
  })
}

function renderAll() {
  if (expandedTaskId && !sortedTasks().some(task => task.id === expandedTaskId)) expandedTaskId = sortedTasks()[0]?.id ?? null
  renderResidents()
  renderResidentSettings()
  renderEdgeStats()
  renderTaskAccordion()
  renderChat(getSelectedTask())
  renderHoloChatStatus()
  usualConversation.render()
  $('dashboard').classList.toggle('is-task-list', taskListOpen || !getSelectedTask())
  $('showChatButton').disabled = !getSelectedTask()
  $('connectionStatus').textContent = !dashboardConnected ? '接続切れ・操作停止'
    : uncertainCommandId ? '受付未確定・照合中' : snapshot.verification_mode ? '検証構成' : 'Hub接続中'
  const disabled = !dashboardConnected || commandBusy || Boolean(uncertainCommandId)
  for (const control of document.querySelectorAll('#addTaskButton, #pauseButton, #resumeButton, [data-task-action], [data-request-action]')) control.disabled = disabled
  if (disabled) $('chatForm').querySelector('.send-button').disabled = true
  if (expandedTaskId) sessionStorage.setItem('nirai:v2:selected-task', expandedTaskId)
  else sessionStorage.removeItem('nirai:v2:selected-task')
  scheduleTerminalExpiry()
  scheduleHoloSurfaceSync()
}

async function createTask() {
  const residentId = selectedResidentId ?? residents[0]?.id
  if (!residentId) return
  const result = await sendCommand('CreateTask', { resident_id: residentId })
  if (!result?.task_id) return
  expandedTaskId = result.task_id
  taskListOpen = false
  const next = await bridge.snapshot()
  applySnapshot(next)
  if (getTask(result.task_id)?.resident_id !== 'holo') {
    requestAnimationFrame(() => $('chatInput').focus())
  }
}

async function updateTask(task, action) {
  if (!task) return

  if (action === 'toggle-state') {
    const type = task.status === 'Running' ? 'PauseTask' : 'ResumeTask'
    await sendCommand(type, { task_id: task.id }, task.revision)
    return
  }
  if (action === 'toggle-resume') {
    await sendCommand('SetTaskResume', { task_id: task.id, enabled: !task.resumeEnabled }, task.revision)
    return
  }
  if (action === 'complete') {
    await sendCommand('CompleteTask', {
      task_id: task.id,
      result_summary: 'MasterがDashboardから完了を確定',
    }, task.revision)
    return
  }
  if (action === 'restart') {
    const created = await sendCommand('CreateTask', { resident_id: task.resident_id })
    if (!created?.task_id) return
    expandedTaskId = created.task_id
    const next = await bridge.snapshot()
    applySnapshot(next)
    if (task.resident_id === 'holo') return

    const restarted = getTask(created.task_id)
    if (!restarted) return
    const priorAnswer = [...task.messages].reverse().find(message => message.role === 'agent')?.text
    const priorContext = priorAnswer ? `\n前回の最終回答:\n${priorAnswer}` : ''
    await sendCommand('SendConversationMessage', {
      task_id: restarted.id,
      sender: 'master',
      content: `前Task「${task.title}」の続きとして再開してください。\n前Task ID: ${task.id}${priorContext}`,
    }, restarted.revision)
    return
  }
  if (action === 'close') {
    closedTerminalTaskIds.add(task.id)
    persistClosedTerminalTaskIds()
    if (expandedTaskId === task.id) expandedTaskId = null
    renderAll()
    return
  }
  if (action === 'cancel') {
    if (!confirm('このTaskを取り消しますか？ 完了扱いにはなりません。')) return
    await sendCommand('CancelTask', { task_id: task.id }, task.revision)
    return
  }
}

async function resolveRequest(requestId, action) {
  const request = snapshot.pending_requests?.find((item) => item.id === requestId)
  if (!request) return

  const answer = action === 'approve' ? { approved: true }
    : action === 'reject' ? { approved: false }
      : null
  if (!answer) return

  await sendCommand('ResolveMasterRequest', { request_id: requestId, answer }, Number(request.revision))
}

function setDashboardOpen(open) {
  const edgeDock = $('edgeDock')
  if (open) usualConversation.saveView()
  if (!open) clearHoloPresentation()
  if (!open) setResidentSettingsOpen(false)
  $('dashboard').classList.toggle('is-open', open)
  $('dashboard').inert = !open || !$('holoChatPanel').hidden
  $('usualChat').hidden = open
  $('usualChat').inert = open || !$('holoChatPanel').hidden
  document.querySelector('.world').dataset.mode = open ? 'task' : 'chat'
  edgeDock.classList.toggle('is-dashboard-open', open)
  edgeDock.setAttribute('aria-expanded', String(open))
  edgeDock.setAttribute('aria-label', open ? 'ChatModeへ' : 'TaskModeへ')
  if (!open) usualConversation.activate()
  if (!open) edgeDock.focus({ preventScroll: true })
  scheduleHoloSurfaceSync()
}

function setResidentSettingsOpen(open) {
  if (open) clearHoloPresentation()
  $('residentSettingsPanel').hidden = !open
  $('settingsButton').setAttribute('aria-expanded', String(open))
  $('chatSettingsButton').setAttribute('aria-expanded', String(open))
  if (open) renderResidentSettings()
  $('dashboard').inert = open || !$('holoChatPanel').hidden || !$('dashboard').classList.contains('is-open')
  $('usualChat').inert = open || !$('holoChatPanel').hidden || $('usualChat').hidden
  if (open) $('residentSettingsClose').focus()
  else if ($(settingsTrigger).getClientRects().length) $(settingsTrigger).focus({ preventScroll: true })
  scheduleHoloSurfaceSync()
}

function setHoloChatOpen(open) {
  if (open === !$('holoChatPanel').hidden) return
  if (open) holoChatReturnFocus = document.activeElement
  clearHoloPresentation()
  holoChatPresentation = null
  $('holoChatPanel').hidden = !open
  $('residentSettingsPanel').inert = open
  $('worldCanvas').inert = open
  $('edgeDock').inert = open
  document.querySelector('.world-controls').inert = open
  const settingsOpen = !$('residentSettingsPanel').hidden
  $('dashboard').inert = open || settingsOpen || !$('dashboard').classList.contains('is-open')
  $('usualChat').inert = open || settingsOpen || $('usualChat').hidden
  if (open) {
    renderHoloChatStatus()
    $('holoChatClose').focus({ preventScroll: true })
  } else {
    if (holoChatReturnFocus?.isConnected && holoChatReturnFocus.getClientRects().length) holoChatReturnFocus.focus({ preventScroll: true })
    else if (settingsOpen) ($('residentSettingsList').querySelector('[data-holo-open]') ?? $('residentSettingsClose')).focus({ preventScroll: true })
    holoChatReturnFocus = null
  }
  scheduleHoloSurfaceSync()
}

function finishResidentStripDrag(strip, pointerId, preserveMoved = true) {
  residentStripDrag.active = false
  if (!preserveMoved) residentStripDrag.moved = false
  if (strip.hasPointerCapture(pointerId)) strip.releasePointerCapture(pointerId)
  strip.classList.remove('is-dragging')
}

function updateResidentScrollFade() {
  const strip = $('residentStrip')
  const hasOverflow = strip.scrollWidth > strip.clientWidth + 1
  const atLeft = strip.scrollLeft <= 1
  strip.classList.toggle('has-more-right', hasOverflow && !atLeft)
}

function updateTaskScrollFade() {
  const pane = $('taskAccordion')
  const hasOverflow = pane.scrollHeight > pane.clientHeight + 1
  pane.classList.toggle('has-more-below', hasOverflow)
}

function resizeComposer() {
  const input = $('chatInput')
  input.style.height = 'auto'
  input.style.height = `${Math.min(input.scrollHeight, 128)}px`
}

$('residentStrip').addEventListener('pointerdown', (event) => {
  if (event.button !== 0) return
  const strip = event.currentTarget
  if (strip.scrollWidth <= strip.clientWidth) return
  residentStripDrag.active = true
  residentStripDrag.moved = false
  residentStripDrag.startX = event.clientX
  residentStripDrag.startScrollLeft = strip.scrollLeft
})
$('residentStrip').addEventListener('pointermove', (event) => {
  if (!residentStripDrag.active) return
  const strip = event.currentTarget
  const deltaX = event.clientX - residentStripDrag.startX
  if (!residentStripDrag.moved && Math.abs(deltaX) > 4) {
    residentStripDrag.moved = true
    strip.setPointerCapture(event.pointerId)
  }
  if (!residentStripDrag.moved) return
  strip.classList.add('is-dragging')
  strip.scrollLeft = residentStripDrag.startScrollLeft - deltaX
})
$('residentStrip').addEventListener('pointerup', (event) => finishResidentStripDrag(event.currentTarget, event.pointerId))
$('residentStrip').addEventListener('pointercancel', (event) => finishResidentStripDrag(event.currentTarget, event.pointerId, false))
$('residentStrip').addEventListener('wheel', (event) => {
  const strip = event.currentTarget
  if (strip.scrollWidth <= strip.clientWidth) return
  const delta = Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY
  if (!delta) return
  const previous = strip.scrollLeft
  strip.scrollLeft += delta
  if (strip.scrollLeft !== previous) event.preventDefault()
}, { passive: false })
$('residentStrip').addEventListener('scroll', updateResidentScrollFade, { passive: true })

$('residentStrip').addEventListener('click', async (event) => {
  if (residentStripDrag.moved) {
    residentStripDrag.moved = false
    return
  }
  const chip = event.target.closest('[data-resident-id]')
  if (!chip) return
  selectedResidentId = chip.dataset.residentId
  const task = getSelectedTask()
  if (task?.draft && task.resident_id !== selectedResidentId) {
    await sendCommand('UpdateTaskDefinition', {
      task_id: task.id,
      resident_id: selectedResidentId,
    }, task.revision)
  } else {
    renderResidents()
  }
})

$('taskAccordion').addEventListener('click', async (event) => {
  const requestButton = event.target.closest('[data-request-action]')
  if (requestButton) {
    await resolveRequest(requestButton.dataset.requestId, requestButton.dataset.requestAction)
    return
  }

  const item = event.target.closest('[data-task-id]')
  if (!item) return
  const task = getTask(item.dataset.taskId)
  const actionButton = event.target.closest('[data-task-action]')
  if (actionButton) {
    await updateTask(task, actionButton.dataset.taskAction)
    return
  }

  if (event.target.closest('[data-task-toggle]')) {
    expandedTaskId = matchMedia('(max-width: 760px)').matches ? item.dataset.taskId : expandedTaskId === item.dataset.taskId ? null : item.dataset.taskId
    taskListOpen = false
    renderAll()
  }
})

$('pauseButton').addEventListener('click', async () => {
  const task = getSelectedTask()
  if (task) await updateTask(task, 'toggle-state')
})
$('resumeButton').addEventListener('click', async () => {
  const task = getSelectedTask()
  if (task) await updateTask(task, 'toggle-resume')
})

$('chatForm').addEventListener('submit', async (event) => {
  event.preventDefault()
  const task = getSelectedTask()
  const input = $('chatInput')
  const text = input.value.trim()
  if (!task || task.resident_id === 'holo' || !text) return

  const result = await sendCommand('SendConversationMessage', {
    task_id: task.id,
    sender: 'master',
    content: text,
  }, task.revision)
  if (!result) return

  if (chatDrafts.get(task.id)?.trim() === text) chatDrafts.delete(task.id)
  if (displayedInputTaskId === task.id && input.value.trim() === text) input.value = ''
  resizeComposer()
})

$('chatInput').addEventListener('keydown', (event) => {
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing || event.keyCode === 229) return
  event.preventDefault()
  $('chatForm').requestSubmit()
})
$('chatInput').addEventListener('input', resizeComposer)
$('chatInput').addEventListener('pointerdown', () => {
  const input = $('chatInput')
  if (!input.disabled && document.activeElement !== input) requestAnimationFrame(() => input.focus())
})
$('addTaskButton').addEventListener('click', createTask)
$('collapseButton').addEventListener('click', () => setDashboardOpen(false))
$('noticeDismiss').addEventListener('click', () => { $('hubNotice').hidden = true })
$('settingsNoticeDismiss').addEventListener('click', () => { $('settingsNotice').hidden = true })
$('chatModeNoticeDismiss').addEventListener('click', () => { $('chatModeNotice').hidden = true })
$('showTasksButton').addEventListener('click', () => { taskListOpen = true; renderAll(); $('showChatButton').focus() })
$('showChatButton').addEventListener('click', () => { taskListOpen = false; renderAll(); $('showTasksButton').focus() })
$('edgeDock').addEventListener('click', () => setDashboardOpen(!$('dashboard').classList.contains('is-open')))
$('settingsButton').addEventListener('click', () => { settingsTrigger = 'settingsButton'; setResidentSettingsOpen($('residentSettingsPanel').hidden) })
$('chatSettingsButton').addEventListener('click', () => { settingsTrigger = 'chatSettingsButton'; setResidentSettingsOpen($('residentSettingsPanel').hidden) })
$('residentSettingsClose').addEventListener('click', () => setResidentSettingsOpen(false))
$('holoChatClose').addEventListener('click', () => setHoloChatOpen(false))
$('holoChatPanel').addEventListener('click', event => {
  if (event.target === $('holoChatPanel')) setHoloChatOpen(false)
})
$('residentSettingsPanel').addEventListener('click', (event) => {
  if (event.target === $('residentSettingsPanel')) setResidentSettingsOpen(false)
  if (event.target.closest('[data-holo-open]')) { setHoloChatOpen(true); return }
  const avatarButton = event.target.closest('[data-avatar-select], [data-avatar-clear]')
  if (avatarButton) {
    avatarButton.disabled = true
    const clear = avatarButton.hasAttribute('data-avatar-clear')
    void bridge.selectAvatar(avatarButton.dataset.avatarSelect ?? avatarButton.dataset.avatarClear, clear).then(async result => {
      if (!result.cancelled) {
        window.dispatchEvent(new Event('nirai:avatar-changed'))
        showNotice(clear ? 'キャラクターの表示を外しました。' : 'VRMを保存しました。海中Worldへ読み込みます。')
      }
    }).catch(error => showNotice(error.message, true)).finally(() => { avatarButton.disabled = false })
  }
  const personaButton = event.target.closest('[data-persona-select], [data-persona-clear]')
  if (personaButton) {
    personaButton.disabled = true
    const clear = personaButton.hasAttribute('data-persona-clear')
    void bridge.selectPersona(personaButton.dataset.personaSelect ?? personaButton.dataset.personaClear, clear)
      .then(async result => {
        if (!result.cancelled) { applySnapshot(await bridge.snapshot()); showNotice(clear ? 'Personaの参照を外しました。' : 'Personaの参照を保存しました。') }
      }).catch(error => showNotice(error.message, true)).finally(() => { personaButton.disabled = false })
  }
  const residentSave = event.target.closest('[data-resident-save]')
  if (residentSave) {
    const id = residentSave.dataset.residentSave
    const column = residentSave.closest('.resident-settings-column')
    const payload = { resident_id: id }
    for (const control of column.querySelectorAll('[data-resident-setting]')) payload[control.dataset.field] = control.value.trim() || null
    if (!payload.display_name) { showNotice('名前を入力してください。', true); return }
    void sendCommand('UpdateResident', payload).then(result => {
      if (result) { residentDrafts.delete(id); renderResidentSettings(); showNotice('Resident設定を保存しました。') }
    })
  }
  if (event.target.closest('[data-holo-save]')) {
    const name = $('holoAppName').value.trim()
    const workspace = $('workspaceScope').value.trim()
    void sendCommand('UpdateSettings', { settings: { holo_app_name: name || null, workspace_scope: workspace || null } }, holoSettingsRevision).then(result => {
      if (result) { holoAppDraft = null; workspaceDraft = null; renderResidentSettings(); showNotice('設定を保存しました。') }
    })
  }
})
$('residentSettingsPanel').addEventListener('input', event => {
  if (event.target.id === 'holoAppName') holoAppDraft = event.target.value
  if (event.target.id === 'workspaceScope') workspaceDraft = event.target.value
  const id = event.target.dataset.residentSetting
  if (id) residentDrafts.set(id, { ...(residentDrafts.get(id) ?? {}), [event.target.dataset.field]: event.target.value })
})
$('addResidentButton').addEventListener('click', () => {
  $('newResidentForm').hidden = false
  $('addResidentButton').setAttribute('aria-expanded', 'true')
  $('newResidentName').focus()
})
$('newResidentCancel').addEventListener('click', () => {
  $('newResidentForm').hidden = true
  $('addResidentButton').setAttribute('aria-expanded', 'false')
  $('addResidentButton').focus()
})
$('newResidentForm').addEventListener('submit', async event => {
  event.preventDefault()
  const displayName = $('newResidentName').value.trim()
  if (!displayName) return
  const result = await sendCommand('CreateResident', { id: `resident-${crypto.randomUUID()}`, display_name: displayName })
  if (result) {
    $('newResidentName').value = ''
    $('newResidentForm').hidden = true
    $('addResidentButton').setAttribute('aria-expanded', 'false')
    showNotice('Residentを追加しました。Personaと会話の接続を設定できます。')
  }
})
$('residentDeleteCancel').addEventListener('click', () => { $('residentDeleteConfirm').hidden = true })
$('residentDeleteConfirmButton').addEventListener('click', () => { $('residentDeleteConfirm').hidden = true })

window.addEventListener('keydown', (event) => {
  if (!$('holoChatPanel').hidden) {
    if (event.key === 'Escape') { event.preventDefault(); setHoloChatOpen(false) }
    else if (event.key === 'Tab' && document.activeElement === $('holoChatClose') && event.shiftKey) event.preventDefault()
    return
  }
  if (event.key === 'Tab' && !$('residentSettingsPanel').hidden) {
    const controls = [...$('residentSettingsPanel').querySelectorAll('button:not(:disabled), input:not(:disabled), select:not(:disabled)')].filter(control => control.getClientRects().length > 0)
    const first = controls[0], last = controls.at(-1)
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
    return
  }
  if (event.key !== 'Escape') return
  if (!$('residentSettingsPanel').hidden) {
    setResidentSettingsOpen(false)
    return
  }
  if (document.activeElement === $('worldCanvas')) return
  if ($('dashboard').classList.contains('is-open')) setDashboardOpen(false)
})

window.addEventListener('resize', () => requestAnimationFrame(() => {
  updateResidentScrollFade()
  updateTaskScrollFade()
  holoSurfaceSignature = null
  scheduleHoloSurfaceSync()
}))

// A notification or wrapped heading can resize the native slot without a
// window resize. Native bounds always follow the actual slot, never a guess.
new ResizeObserver(scheduleHoloSurfaceSync).observe($('holoSurface'))
new ResizeObserver(scheduleHoloSurfaceSync).observe($('holoChatSurface'))

if (bridge) {
  bridge.onHoloChatClose?.(() => setHoloChatOpen(false))
  bridge.onHoloPresentationChanged?.(applyHoloPresentation)
  bridge.onSnapshotChanged((next) => {
    applySnapshot(next)
    void reconcileUncertainCommand()
  })
  bridge.onHubDisconnected(() => {
    restoreChatFocus ||= ['chatInput', 'usualChatInput'].includes(document.activeElement?.id) ? document.activeElement.id : null
    dashboardConnected = false
    clearHoloPresentation()
    showNotice('Hubとの接続が切れました。操作は停止しています。', true)
    renderAll()
  })
  bridge.snapshot()
    .then((next) => {
      applySnapshot(next)
      void reconcileUncertainCommand()
    })
    .catch((error) => {
      dashboardConnected = false
      showNotice(`Hubへ接続できません: ${error?.message ?? error}`, true)
      renderAll()
    })
} else {
  showNotice('Nirai Hub bridgeがありません', true)
}

renderAll()
setDashboardOpen(true)
