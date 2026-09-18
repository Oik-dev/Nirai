const statusLabel = {
  Running: 'Running',
  Paused: 'Paused',
  Completed: 'Completed',
  Failed: 'Failed',
  Cancelled: 'Cancelled',
  Waiting: 'Waiting',
  Interrupted: 'Interrupted',
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
let expandedTaskId = null
let selectedResidentId = 'holo'
let edgeCompletedCount = 0
let dashboardConnected = false
let commandBusy = false
const residentStripDrag = { active: false, moved: false, startX: 0, startScrollLeft: 0 }
const $ = (id) => document.getElementById(id)

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
    target: payload?.task_id ?? null,
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
  let notice = $('hubNotice')
  if (!notice) {
    notice = document.createElement('div')
    notice.id = 'hubNotice'
    notice.className = 'hub-notice'
    $('dashboard').prepend(notice)
  }
  notice.textContent = message
  notice.classList.toggle('is-error', isError)
  notice.hidden = false
  clearTimeout(showNotice.timer)
  showNotice.timer = setTimeout(() => {
    notice.hidden = true
  }, 5000)
}

async function sendCommand(type, payload, expectedRevision) {
  if (!bridge || commandBusy) return null
  commandBusy = true
  $('dashboard').setAttribute('aria-busy', 'true')
  try {
    const result = await bridge.command(commandEnvelope(type, payload, expectedRevision))
    const latest = await bridge.snapshot()
    applySnapshot(latest)
    return result
  } catch (error) {
    showNotice(error?.message ?? String(error), true)
    return null
  } finally {
    commandBusy = false
    $('dashboard').setAttribute('aria-busy', 'false')
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
  snapshot = next ?? snapshot
  const rawResidents = Array.isArray(snapshot.residents) ? snapshot.residents : []
  residents = rawResidents.map((resident) => ({
    id: resident.id,
    name: resident.display_name ?? resident.id,
    role: resident.id === 'holo' ? '指揮者' : '未設定',
    ai: resident.id === 'holo' ? 'Holo Addon' : '未設定',
    model: '-',
    avatar: '未設定',
    online: false,
    shortLimit: { label: '現在', remaining: 0, reset: '--' },
    longLimit: { label: '長期', remaining: 0, reset: '--' },
  }))
  if (!residents.some((resident) => resident.id === selectedResidentId)) {
    selectedResidentId = residents[0]?.id ?? null
  }

  const requests = Array.isArray(snapshot.pending_requests) ? snapshot.pending_requests : []
  const runs = Array.isArray(snapshot.runs) ? snapshot.runs : []
  const messages = Array.isArray(snapshot.messages) ? snapshot.messages : []

  tasks = (snapshot.tasks ?? []).map((task) => {
    const taskMessages = messages
      .filter((message) => message.conversation_id === task.conversation_id)
      .map((message) => ({
        id: message.id,
        role: message.sender === 'master' ? 'master' : message.sender === 'system' ? 'system' : 'agent',
        who: message.sender === 'master' ? 'Master' : message.sender === 'system' ? 'Nirai' : residentName(message.sender),
        text: message.content,
        time: timeLabel(message.created_at),
      }))

    const taskRequests = requests.filter((request) => request.task_id === task.id)
    const taskRuns = runs.filter((run) => run.task_id === task.id)
    const activities = taskRuns.map((run) => ({
      id: run.id,
      title: run.kind === 'response'
        ? `${residentName(task.resident_id)} 応答`
        : run.operation,
      status: mapRunState(run.state),
      agent: task.resident_id,
      note: run.error_json
        ? 'エラー'
        : run.state === 'Interrupted'
          ? '中断・確認待ち'
          : run.state === 'Pending'
            ? '開始待ち'
            : run.state,
    }))

    for (const request of taskRequests) {
      activities.unshift({
        id: `request-${request.id}`,
        title: request.kind === 'approval' ? '承認待ち' : 'Master回答待ち',
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
      activities,
      messages: taskMessages,
    }
  })

  if (expandedTaskId && !getTask(expandedTaskId)) expandedTaskId = null
  if (!expandedTaskId && tasks.length > 0) expandedTaskId = tasks[0].id
  dashboardConnected = true
  renderAll()
}

function sortedTasks() {
  return [...tasks].sort((a, b) => {
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
  return `
    <span class="limit-row">
      <span>${escapeHtml(limit.label)}</span>
      <span class="limit-track"><i style="width:${limit.remaining}%"></i></span>
      <strong>${limit.remaining}%</strong>
      <small>${escapeHtml(limit.reset)}</small>
    </span>
  `
}

function renderResidents() {
  const strip = $('residentStrip')
  const previousScrollLeft = strip.scrollLeft
  strip.innerHTML = residents.map((resident) => {
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
          <span class="resident-state-text">${resident.online ? 'Online' : 'Not connected'}</span>
        </span>
        <span class="resident-limits">
          ${limitRow(resident.shortLimit)}
          ${limitRow(resident.longLimit)}
        </span>
      </button>
    `
  }).join('')

  requestAnimationFrame(() => {
    const maxScrollLeft = Math.max(0, strip.scrollWidth - strip.clientWidth)
    strip.scrollLeft = Math.min(previousScrollLeft, maxScrollLeft)
    updateResidentScrollFade()
  })
}

function renderResidentSettings() {
  if ($('residentSettingsPanel').hidden) return
  $('residentSettingsList').innerHTML = residents.map((resident) => `
    <article class="resident-settings-column">
      <button class="resident-name-button" type="button" disabled>
        <span class="resident-state-dot state-${resident.online ? 'online' : 'offline'}"></span>
        <strong>${escapeHtml(resident.name)}</strong>
      </button>
      <div class="resident-settings-fields">
        <label class="resident-setting-row"><span>Role</span><select disabled><option>${escapeHtml(resident.role)}</option></select></label>
        <label class="resident-setting-row"><span>AI</span><select disabled><option>${escapeHtml(resident.ai)}</option></select></label>
        <label class="resident-setting-row"><span>Model</span><select disabled><option>${escapeHtml(resident.model)}</option></select></label>
        <label class="resident-setting-row"><span>Avatar</span><select disabled><option>${escapeHtml(resident.avatar)}</option></select></label>
      </div>
    </article>
  `).join('')
  $('addResidentButton').disabled = true
}

function renderEdgeStats() {
  $('edgeRunning').textContent = tasks.filter((task) => task.status === 'Running').length
  $('edgeCheck').textContent = tasks.filter((task) => task.attention).length
  $('edgePaused').textContent = tasks.filter((task) => task.status === 'Paused' && !task.draft).length
  $('edgeCompleted').textContent = tasks.filter((task) => ['Completed', 'Failed', 'Cancelled'].includes(task.status)).length + edgeCompletedCount
}

function taskPreviewText(task) {
  const last = [...task.messages].reverse().find((message) => message.role !== 'system')
  if (last?.text) return last.text
  if (task.result_summary) return task.result_summary
  return task.draft ? 'Chatから指示してください' : 'Chat履歴なし'
}

function taskListRowMarkup(task) {
  const baseStateText = task.draft
    ? 'Waiting for input'
    : task.attention
      ? `${statusLabel[task.status] ?? task.status} · Check`
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
  const proposal = request.proposal_json ? (() => {
    try { return JSON.stringify(JSON.parse(request.proposal_json), null, 2) } catch { return request.proposal_json }
  })() : ''
  if (request.kind === 'approval') {
    return `
      <div class="master-request-card">
        <strong>CHECK · 承認</strong>
        <p>${escapeHtml(request.prompt)}</p>
        ${proposal ? `<pre>${escapeHtml(proposal)}</pre>` : ''}
        <div class="completed-task-actions">
          <button type="button" class="restart-task-button" data-request-action="approve" data-request-id="${escapeHtml(request.id)}">承認</button>
          <button type="button" class="close-task-button" data-request-action="reject" data-request-id="${escapeHtml(request.id)}">却下</button>
        </div>
      </div>
    `
  }
  return `
    <div class="master-request-card">
      <strong>CHECK · 回答待ち</strong>
      <p>${escapeHtml(request.prompt)}</p>
      <button type="button" class="restart-task-button" data-request-action="answer" data-request-id="${escapeHtml(request.id)}">回答する</button>
    </div>
  `
}

function taskActionsMarkup(task) {
  if (['Completed', 'Failed', 'Cancelled'].includes(task.status)) {
    return `
      <div class="completed-task-actions">
        <button type="button" class="restart-task-button" data-task-action="restart">新しいTaskで再開</button>
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
  $('taskAccordion').innerHTML = sortedTasks().map((task) => {
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
          <div class="work-detail-toolbar"><small>${task.activities.length} Activities</small></div>
          <div class="nested-task-list">${activities}</div>
          ${requests}
          ${taskActionsMarkup(task)}
        </div>
      </article>
    `
  }).join('')
  requestAnimationFrame(updateTaskScrollFade)
}

function renderChat(task) {
  const form = $('chatForm')
  const input = $('chatInput')
  const send = form.querySelector('.send-button')
  const pauseButton = $('pauseButton')
  const resumeButton = $('resumeButton')
  const messages = $('chatMessages')

  if (!task) {
    $('chatTaskTitle').textContent = 'Taskを選択'
    $('chatTaskMeta').textContent = dashboardConnected ? '新しいTaskを作成できます' : 'Hubへ接続中'
    messages.innerHTML = '<div class="chat-empty">Taskを選択してください</div>'
    input.disabled = true
    send.disabled = true
    pauseButton.hidden = true
    resumeButton.hidden = true
    form.classList.add('is-disabled')
    return
  }

  $('chatTaskTitle').textContent = task.title
  const agentNames = task.agents.map(residentName).join(' / ')
  $('chatTaskMeta').textContent = task.draft
    ? `${agentNames} · 入力待ち`
    : `${agentNames} · ${task.updated}更新`

  const terminal = ['Completed', 'Failed', 'Cancelled'].includes(task.status)
  input.disabled = terminal || !dashboardConnected
  send.disabled = terminal || !dashboardConnected
  form.classList.toggle('is-disabled', terminal || !dashboardConnected)

  const supportsControls = !terminal && !task.draft
  pauseButton.hidden = !supportsControls
  pauseButton.textContent = task.status === 'Running' ? 'Pause' : '再開'
  resumeButton.hidden = !supportsControls
  resumeButton.textContent = task.resumeEnabled ? 'Resume ON' : 'Resume OFF'
  resumeButton.classList.toggle('is-active', supportsControls && task.resumeEnabled)
  resumeButton.setAttribute('aria-pressed', String(supportsControls && task.resumeEnabled))

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
  requestAnimationFrame(() => { messages.scrollTop = messages.scrollHeight })
}

function renderAll() {
  renderResidents()
  renderResidentSettings()
  renderEdgeStats()
  renderTaskAccordion()
  renderChat(getSelectedTask())
}

async function createTask() {
  const residentId = selectedResidentId ?? residents[0]?.id
  if (!residentId) return
  const result = await sendCommand('CreateTask', { resident_id: residentId })
  if (!result?.task_id) return
  expandedTaskId = result.task_id
  const next = await bridge.snapshot()
  applySnapshot(next)
  requestAnimationFrame(() => $('chatInput').focus())
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
  if (action === 'cancel') {
    if (!confirm('このTaskを取り消しますか？ 完了扱いにはなりません。')) return
    await sendCommand('CancelTask', { task_id: task.id }, task.revision)
    return
  }
  if (action === 'restart') {
    const result = await sendCommand('CreateTask', { resident_id: task.resident_id })
    if (result?.task_id) expandedTaskId = result.task_id
  }
}

async function resolveRequest(requestId, action) {
  const request = snapshot.pending_requests?.find((item) => item.id === requestId)
  if (!request) return

  let answer
  if (action === 'approve') answer = { approved: true }
  else if (action === 'reject') answer = { approved: false }
  else {
    const value = prompt(request.prompt)
    if (value === null || !value.trim()) return
    answer = { text: value.trim() }
  }

  await sendCommand('ResolveMasterRequest', { request_id: requestId, answer })
}

function setDashboardOpen(open) {
  const edgeDock = $('edgeDock')
  if (!open) setResidentSettingsOpen(false)
  $('dashboard').classList.toggle('is-open', open)
  edgeDock.classList.toggle('is-dashboard-open', open)
  edgeDock.setAttribute('aria-expanded', String(open))
  edgeDock.setAttribute('aria-label', open ? 'Dashboardを格納' : 'Dashboardを開く')
  if (open) edgeCompletedCount = 0
}

function setResidentSettingsOpen(open) {
  $('residentSettingsPanel').hidden = !open
  $('settingsButton').setAttribute('aria-expanded', String(open))
  if (open) renderResidentSettings()
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
    expandedTaskId = expandedTaskId === item.dataset.taskId ? null : item.dataset.taskId
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
  if (!task || !text) return

  const result = await sendCommand('SendConversationMessage', {
    task_id: task.id,
    sender: 'master',
    content: text,
  }, task.revision)
  if (!result) return

  input.value = ''
  resizeComposer()
})

$('chatInput').addEventListener('input', resizeComposer)
$('addTaskButton').addEventListener('click', createTask)
$('edgeDock').addEventListener('click', () => setDashboardOpen(!$('dashboard').classList.contains('is-open')))
$('settingsButton').addEventListener('click', () => setResidentSettingsOpen($('residentSettingsPanel').hidden))
$('residentSettingsClose').addEventListener('click', () => setResidentSettingsOpen(false))
$('residentSettingsPanel').addEventListener('click', (event) => {
  if (event.target === $('residentSettingsPanel')) setResidentSettingsOpen(false)
})
$('residentDeleteCancel').addEventListener('click', () => { $('residentDeleteConfirm').hidden = true })
$('residentDeleteConfirmButton').addEventListener('click', () => { $('residentDeleteConfirm').hidden = true })

window.addEventListener('keydown', (event) => {
  if (event.key !== 'Escape') return
  if (!$('residentSettingsPanel').hidden) {
    setResidentSettingsOpen(false)
    return
  }
  if ($('dashboard').classList.contains('is-open')) setDashboardOpen(false)
})

window.addEventListener('resize', () => requestAnimationFrame(() => {
  updateResidentScrollFade()
  updateTaskScrollFade()
}))

if (bridge) {
  bridge.onSnapshotChanged((next) => applySnapshot(next))
  bridge.onHubDisconnected(() => {
    dashboardConnected = false
    showNotice('Hubとの接続が切れました。操作は停止しています。', true)
    renderAll()
  })
  bridge.snapshot()
    .then((next) => applySnapshot(next))
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
