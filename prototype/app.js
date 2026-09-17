const statusLabel = {
  Running: 'Running',
  NeedsInput: 'Needs Input',
  Completed: 'Completed',
  Waiting: 'Waiting',
}

const taskStatusOrder = {
  Running: 0,
  NeedsInput: 1,
  Completed: 2,
}

const COMPLETED_VISIBLE_MS = 72 * 60 * 60 * 1000

const stepStatusOrder = {
  Running: 0,
  NeedsInput: 1,
  Waiting: 2,
  Completed: 3,
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

const tasks = [
  {
    id: 'dna-homebase',
    title: 'DNA Chapter01 HomeBase再現',
    type: 'build',
    status: 'Running',
    agents: ['holo', 'cursor'],
    updated: '2分前',
    draft: false,
    resumeEnabled: false,
    steps: [
      { id: 'dna-1', title: '室内Material検証', status: 'Running', agent: 'holo', note: '正本Sceneを基準に機械チェック' },
      { id: 'dna-2', title: '参照Asset探索', status: 'Completed', agent: 'cursor', note: '探索完了' },
      { id: 'dna-3', title: '最終監査', status: 'Waiting', agent: 'astra', note: '前Step完了待ち' },
    ],
    messages: [
      { role: 'agent', who: 'Holo', text: '室内側の正本Sceneを基準に進めておる。', time: '01:48' },
      { role: 'master', who: 'Master', text: '今はどこまで進んでる？', time: '01:51' },
      { role: 'agent', who: 'Holo', text: 'Material検証中。Cursor側の探索は完了済みじゃ。', time: '01:51' },
    ],
  },
  {
    id: 'nirai-control',
    title: 'Nirai Control Plane',
    type: 'control',
    status: 'Running',
    agents: ['holo'],
    updated: 'たった今',
    draft: false,
    resumeEnabled: false,
    steps: [
      { id: 'nirai-1', title: 'Dashboard UIモック調整', status: 'Running', agent: 'holo', note: '現在のPrototype' },
      { id: 'nirai-2', title: '旧Control Plane棚卸し', status: 'Waiting', agent: 'cursor', note: 'UI確定後に開始' },
      { id: 'nirai-3', title: '状態遷移レビュー', status: 'Waiting', agent: 'astra', note: '実装前監査' },
    ],
    messages: [
      { role: 'agent', who: 'Holo', text: 'Taskを仕事単位、Stepを内部工程として整理した。', time: '03:08' },
      { role: 'master', who: 'Master', text: 'その方が分かりやすい。', time: '03:09' },
    ],
  },
  {
    id: 'old-control-review',
    title: '旧Nirai制御層レビュー',
    type: 'review',
    status: 'NeedsInput',
    agents: ['astra'],
    updated: '11分前',
    draft: false,
    resumeEnabled: false,
    steps: [
      { id: 'old-1', title: '廃棄候補の確認', status: 'NeedsInput', agent: 'astra', note: 'Master回答待ち' },
      { id: 'old-2', title: '再利用候補の一覧化', status: 'Completed', agent: 'astra', note: '完了' },
    ],
    messages: [
      { role: 'agent', who: 'Holo', text: '旧制御層のうち、完全廃棄する範囲についてMasterの回答待ちじゃ。', time: '02:31' },
    ],
  },
  {
    id: 'serina-memory',
    title: 'Serina Memory再開準備',
    type: 'memory',
    status: 'Running',
    agents: ['holo'],
    updated: '4時間前',
    draft: false,
    resumeEnabled: false,
    steps: [
      { id: 'serina-1', title: '旧設計の差分整理', status: 'Running', agent: 'holo', note: '差分を整理中' },
      { id: 'serina-2', title: '移行方針決定', status: 'Waiting', agent: 'holo', note: '前Step完了待ち' },
    ],
    messages: [
      { role: 'agent', who: 'Holo', text: '旧設計との差分整理を進めておる。', time: '22:16' },
    ],
  },
  {
    id: 'auto-resume-cleanup',
    title: 'Auto Resume簡素化',
    type: 'automation',
    status: 'Completed',
    agents: ['holo'],
    updated: '昨日 23:57',
    completedAt: Date.now() - 14 * 60 * 60 * 1000,
    draft: false,
    resumeEnabled: false,
    steps: [
      { id: 'auto-1', title: '再開条件を整理', status: 'Completed', agent: 'holo', note: '完了' },
      { id: 'auto-2', title: '不要分岐を削除', status: 'Completed', agent: 'holo', note: '完了' },
    ],
    messages: [
      { role: 'agent', who: 'Holo', text: 'Auto Resumeの簡素化はここで一区切りじゃ。', time: '23:57' },
    ],
  },
  {
    id: 'skylight-review',
    title: 'SkyLight原因調査',
    type: 'research',
    status: 'Completed',
    agents: ['holo', 'cursor'],
    updated: '昨日 21:40',
    completedAt: Date.now() - 28 * 60 * 60 * 1000,
    draft: false,
    resumeEnabled: false,
    steps: [
      { id: 'sky-1', title: '原因候補を切り分け', status: 'Completed', agent: 'holo', note: '完了' },
      { id: 'sky-2', title: 'Scene差分を確認', status: 'Completed', agent: 'cursor', note: '完了' },
      { id: 'sky-3', title: '残りのライト調整', status: 'Waiting', agent: 'holo', note: '次Taskへ持ち越し可能' },
    ],
    messages: [
      { role: 'agent', who: 'Holo', text: '原因調査は一区切り。残りはライト調整のみじゃ。', time: '21:40' },
    ],
  },
  {
    id: 'old-incident-cleanup',
    title: '旧Incident整理',
    type: 'maintenance',
    status: 'Completed',
    agents: ['holo'],
    updated: '9/14 23:20',
    completedAt: Date.now() - 80 * 60 * 60 * 1000,
    draft: false,
    resumeEnabled: false,
    steps: [],
    messages: [],
  },
]

const ROLE_OPTIONS = ['未設定', '指揮者', '実装者', '監査者']
const AI_OPTIONS = ['未設定', 'Holo Addon', 'Cursor', 'Astra']
const MODEL_OPTIONS = ['未設定', 'Grok4.6 xhigh']
const AVATAR_OPTIONS = ['未設定', 'Lapan', 'Mirdo']

const residents = [
  {
    id: 'holo',
    name: 'Holo',
    role: '指揮者',
    ai: 'Holo Addon',
    model: '-',
    avatar: 'Lapan',
    promptPath: 'file:///D:/Products/dev/Nirai/resident-prompts/holo.txt',
    online: true,
    shortLimit: { label: '5時間', remaining: 58, reset: '03:00' },
    longLimit: { label: '1週間', remaining: 74, reset: '月 09:00' },
  },
  {
    id: 'cursor',
    name: 'Cursor',
    role: '実装者',
    ai: 'Cursor',
    model: 'Grok4.6 xhigh',
    avatar: 'Mirdo',
    promptPath: 'file:///D:/Products/dev/Nirai/resident-prompts/cursor.txt',
    online: true,
    shortLimit: { label: '5時間', remaining: 82, reset: '07:00' },
    longLimit: { label: '1か月', remaining: 61, reset: '10/01' },
  },
  {
    id: 'astra',
    name: 'Astra',
    role: '未設定',
    ai: 'Astra',
    model: '未設定',
    avatar: '未設定',
    promptPath: 'file:///D:/Products/dev/Nirai/resident-prompts/astra.txt',
    online: true,
    shortLimit: { label: '5時間', remaining: 71, reset: '05:00' },
    longLimit: { label: '1週間', remaining: 88, reset: '月 09:00' },
  },
]

let expandedTaskId = 'nirai-control'
let selectedResidentId = 'holo'
let editingResidentId = null
let pendingResidentDeleteId = null
let edgeCompletedCount = 0
let completedExpiryTimer = null
const residentStripDrag = { active: false, moved: false, startX: 0, startScrollLeft: 0 }
const $ = (id) => document.getElementById(id)

function currentTime() {
  return new Date().toLocaleTimeString('ja-JP', { hour: '2-digit', minute: '2-digit' })
}

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;')
}

function getTask(id) {
  return tasks.find((task) => task.id === id) ?? null
}

function getResident(id) {
  return residents.find((resident) => resident.id === id) ?? null
}

function residentName(id) {
  return getResident(id)?.name ?? '削除済みResident'
}

function taskAgentNames(task) {
  return task.agents.map(residentName)
}

function getSelectedTask() {
  return expandedTaskId ? getTask(expandedTaskId) : null
}

function isCompletedVisible(task, now = Date.now()) {
  if (task.status !== 'Completed') return true
  if (task.closedAt || !task.completedAt) return false
  return now - task.completedAt < COMPLETED_VISIBLE_MS
}

function visibleTasks() {
  const now = Date.now()
  return tasks.filter((task) => isCompletedVisible(task, now))
}

function completedAgeLabel(completedAt) {
  if (!Number.isFinite(completedAt)) return '完了'
  const elapsed = Math.max(0, Date.now() - completedAt)
  const hours = Math.floor(elapsed / (60 * 60 * 1000))
  if (hours < 1) return `${Math.max(1, Math.floor(elapsed / (60 * 1000)))}分前に完了`
  if (hours < 24) return `${hours}時間前に完了`
  return `${Math.floor(hours / 24)}日前に完了`
}

function sortedTasks() {
  return visibleTasks().sort((a, b) => {
    const statusDiff = (taskStatusOrder[a.status] ?? 99) - (taskStatusOrder[b.status] ?? 99)
    if (statusDiff !== 0) return statusDiff
    if (a.status === 'Completed') return (b.completedAt ?? 0) - (a.completedAt ?? 0)
    return a.title.localeCompare(b.title, 'ja')
  })
}

function sortedSteps(task) {
  return [...task.steps].sort((a, b) => {
    const statusDiff = (stepStatusOrder[a.status] ?? 99) - (stepStatusOrder[b.status] ?? 99)
    if (statusDiff !== 0) return statusDiff
    return a.title.localeCompare(b.title, 'ja')
  })
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

function residentSettingSelectMarkup(resident, field, label, options, disabled = false) {
  const optionMarkup = options.map((option) => `
    <option value="${escapeHtml(option)}"${resident[field] === option ? ' selected' : ''}>${escapeHtml(option)}</option>
  `).join('')

  return `
    <label class="resident-setting-row">
      <span>${escapeHtml(label)}</span>
      <select data-resident-select="${field}" data-resident-id="${escapeHtml(resident.id)}"${disabled ? ' disabled' : ''}>
        ${optionMarkup}
      </select>
    </label>
  `
}

function renderResidentSettings() {
  if ($('residentSettingsPanel').hidden) return

  const list = $('residentSettingsList')
  list.innerHTML = residents.map((resident) => {
    const nameEditing = editingResidentId === resident.id
    const nameMarkup = nameEditing
      ? `<span class="resident-setting-edit-row"><input class="resident-setting-input" data-resident-name-input data-resident-id="${escapeHtml(resident.id)}" value="${escapeHtml(resident.name)}" aria-label="名前" /><button class="resident-setting-save" type="button" data-resident-name-save data-resident-id="${escapeHtml(resident.id)}" aria-label="名前を保存">✓</button></span>`
      : `<button class="resident-name-button" type="button" data-resident-name-edit="${escapeHtml(resident.id)}" aria-label="名前を編集"><span class="resident-state-dot state-${resident.online ? 'online' : 'offline'}"></span><strong>${escapeHtml(resident.name)}</strong></button>`

    return `
      <article class="resident-settings-column" data-resident-card="${escapeHtml(resident.id)}">
        ${nameMarkup}
        <div class="resident-settings-fields">
          ${residentSettingSelectMarkup(resident, 'role', 'Role', ROLE_OPTIONS)}
          ${residentSettingSelectMarkup(resident, 'ai', 'AI', AI_OPTIONS)}
          ${residentSettingSelectMarkup(resident, 'model', 'Model', resident.ai === 'Holo Addon' ? ['-'] : MODEL_OPTIONS, resident.ai === 'Holo Addon')}
          ${residentSettingSelectMarkup(resident, 'avatar', 'Avatar', AVATAR_OPTIONS)}
        </div>
        <button class="resident-prompt-trigger" type="button" data-resident-prompt-open="${escapeHtml(resident.id)}">Prompt設定</button>
        <button class="resident-delete-button" type="button" data-resident-delete="${escapeHtml(resident.id)}">キャラクター削除</button>
      </article>
    `
  }).join('')
}

function setResidentDeleteConfirmOpen(residentId = null) {
  const overlay = $('residentDeleteConfirm')
  pendingResidentDeleteId = residentId
  overlay.hidden = !residentId
  if (!residentId) return
  const resident = getResident(residentId)
  if (!resident) {
    pendingResidentDeleteId = null
    overlay.hidden = true
    return
  }
  $('residentDeleteMessage').textContent = `${resident.name} を削除しますか？`
}

function deletePendingResident() {
  const resident = getResident(pendingResidentDeleteId)
  if (!resident) {
    setResidentDeleteConfirmOpen(null)
    return
  }
  const index = residents.findIndex((item) => item.id === resident.id)
  residents.splice(index, 1)
  if (editingResidentId === resident.id) editingResidentId = null

  const fallbackResidentId = residents[0]?.id ?? null
  if (selectedResidentId === resident.id) selectedResidentId = fallbackResidentId
  for (const task of tasks) {
    if (task.draft && task.agents.includes(resident.id)) {
      task.agents = fallbackResidentId ? [fallbackResidentId] : []
    }
  }

  setResidentDeleteConfirmOpen(null)
  renderAll()
}

function setResidentSettingsOpen(open) {
  const panel = $('residentSettingsPanel')
  const button = $('settingsButton')
  if (!open) setResidentDeleteConfirmOpen(null)
  panel.hidden = !open
  button.setAttribute('aria-expanded', String(open))
  if (open) renderResidentSettings()
}

function renameResident(residentId, value) {
  const resident = getResident(residentId)
  const nextName = value.trim()
  if (!resident || !nextName) return

  resident.name = nextName
  editingResidentId = null
  renderAll()
}

function updateResidentSetting(residentId, field, value) {
  const resident = getResident(residentId)
  if (!resident) return

  resident[field] = value
  if (field === 'ai') resident.model = value === 'Holo Addon' ? '-' : '未設定'
  renderResidentSettings()
}

function addResident() {
  const id = `resident-${Date.now()}`
  residents.push({
    id,
    name: '新しいResident',
    role: '未設定',
    ai: '未設定',
    model: '未設定',
    avatar: '未設定',
    promptPath: `file:///D:/Products/dev/Nirai/resident-prompts/${id}.txt`,
    online: false,
    shortLimit: { label: '5時間', remaining: 0, reset: '--' },
    longLimit: { label: '長期', remaining: 0, reset: '--' },
  })
  editingResidentId = id
  renderAll()
  requestAnimationFrame(() => {
    const input = document.querySelector(`[data-resident-name-input][data-resident-id="${id}"]`)
    input?.focus()
    input?.select()
  })
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
        aria-label="${escapeHtml(resident.name)} を新規CHATの対象にする"
      >
        <span class="resident-headline">
          <span class="resident-state-dot state-${resident.online ? 'online' : 'offline'}"></span>
          <strong>${escapeHtml(resident.name)}</strong>
          <span class="resident-state-text">${resident.online ? 'Online' : 'Offline'}</span>
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

function renderEdgeStats() {
  $('edgeRunning').textContent = tasks.filter((task) => task.status === 'Running').length
  $('edgeNeedsInput').textContent = tasks.filter((task) => task.status === 'NeedsInput').length
  $('edgeCompleted').textContent = edgeCompletedCount
}

function statusDotClass(status) {
  return `status-${status.toLowerCase()}`
}

function taskTypeIcon(type) {
  return TASK_TYPE_ICONS[type] ?? TASK_TYPE_ICONS.general
}

function taskListRowMarkup({ type, status, title, secondary, timestamp, baseStateText, resumeEnabled }) {
  const stateMarkup = status === 'Running'
    ? `<span class="task-list-status-running">${escapeHtml(baseStateText)}</span>`
    : escapeHtml(baseStateText)
  const resumeMarkup = resumeEnabled ? '<span class="task-list-resume"> · Resume</span>' : ''

  return `
    <span class="task-type-icon" aria-hidden="true">${taskTypeIcon(type)}</span>
    <span class="task-list-main">
      <strong>${escapeHtml(title)}</strong>
      <small>${escapeHtml(secondary)}</small>
    </span>
    <span class="task-list-state">
      <span class="task-list-status">
        <span class="task-status-dot ${statusDotClass(status)}"></span>
        <span>${stateMarkup}${resumeMarkup}</span>
      </span>
      <small class="task-list-timestamp">${escapeHtml(timestamp)}</small>
    </span>
  `
}

function taskPreviewText(task) {
  const lastMessage = [...task.messages].reverse().find((message) => message.role !== 'system')
  if (lastMessage?.text) return lastMessage.text
  return task.draft ? 'Chatから指示してください' : 'Chat履歴なし'
}

function renderTaskAccordion() {
  $('taskAccordion').innerHTML = sortedTasks().map((task) => {
    const isExpanded = task.id === expandedTaskId
    const baseStateText = task.draft ? 'Needs Input' : (statusLabel[task.status] ?? task.status)
    const secondary = taskPreviewText(task)
    const timestamp = task.status === 'Completed'
      ? completedAgeLabel(task.completedAt)
      : task.updated
    const steps = task.steps.length > 0
      ? sortedSteps(task).map(stepMarkup).join('')
      : '<div class="step-empty">Chatから最初の指示を送るとStepが作成されます</div>'

    return `
      <article class="work-item selectable-surface${isExpanded ? ' is-selected' : ''}" data-task-id="${escapeHtml(task.id)}">
        <button type="button" class="task-list-row work-summary" data-task-toggle aria-expanded="${isExpanded}">
          ${taskListRowMarkup({
            type: task.type,
            status: task.status,
            title: task.title,
            secondary,
            timestamp,
            baseStateText,
            resumeEnabled: task.resumeEnabled,
          })}
        </button>
        <div class="work-detail" ${isExpanded ? '' : 'hidden'}>
          <div class="work-detail-toolbar">
            <small>${task.steps.length} Steps</small>
          </div>
          <div class="nested-task-list">${steps}</div>
          ${taskActionsMarkup(task)}
        </div>
      </article>
    `
  }).join('')

  requestAnimationFrame(updateTaskScrollFade)
}

function stepMarkup(step) {
  return `
    <div class="nested-task-row">
      <span class="task-status-dot ${statusDotClass(step.status)}"></span>
      <span class="nested-task-main">
        <strong>${escapeHtml(step.title)}</strong>
        <small>${escapeHtml(step.note)}</small>
      </span>
      <span class="nested-task-agent">${escapeHtml(residentName(step.agent))}</span>
      <span class="nested-task-status">${statusLabel[step.status] ?? step.status}</span>
    </div>
  `
}

function taskActionsMarkup(task) {
  if (task.status === 'Completed') {
    return `
      <div class="completed-task-actions">
        <button type="button" class="restart-task-button" data-task-action="restart">新しいTaskで再開</button>
        <button type="button" class="close-task-button" data-task-action="close">閉じる</button>
      </div>
    `
  }

  return `
    <button type="button" class="complete-task-button" data-task-action="complete">
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="m9.2 16.2-4.1-4.1L3.7 13.5l5.5 5.5L20.7 7.5l-1.4-1.4-10.1 10.1Z" />
      </svg>
      <span>このタスクを完了扱いにする</span>
    </button>
  `
}

function renderChat(task) {
  const form = $('chatForm')
  const input = $('chatInput')
  const send = form.querySelector('.send-button')
  const resumeButton = $('resumeButton')
  const messages = $('chatMessages')

  if (!task) {
    $('chatTaskTitle').textContent = 'Taskを選択'
    $('chatTaskMeta').textContent = 'Taskを開くと、そのTaskのChatへ切り替わります'
    messages.innerHTML = '<div class="chat-empty">Taskを展開するとChatが開きます</div>'
    input.disabled = true
    input.placeholder = ''
    send.disabled = true
    resumeButton.hidden = true
    resumeButton.classList.remove('is-active')
    resumeButton.setAttribute('aria-pressed', 'false')
    form.classList.add('is-disabled')
    return
  }

  $('chatTaskTitle').textContent = task.title
  const agentNames = taskAgentNames(task).join(' / ')
  $('chatTaskMeta').textContent = task.draft
    ? agentNames
    : task.status === 'Completed'
      ? `${agentNames} · ${completedAgeLabel(task.completedAt)}`
      : `${agentNames} · ${task.updated}更新`
  const isCompleted = task.status === 'Completed'
  input.disabled = isCompleted
  input.placeholder = ''
  send.disabled = isCompleted
  const supportsResume = !isCompleted && task.agents.includes('holo')
  resumeButton.hidden = !supportsResume
  resumeButton.classList.toggle('is-active', supportsResume && task.resumeEnabled)
  resumeButton.setAttribute('aria-pressed', String(supportsResume && task.resumeEnabled))
  form.classList.toggle('is-disabled', isCompleted)

  messages.innerHTML = ''
  if (task.messages.length === 0) {
    messages.innerHTML = isCompleted
      ? '<div class="chat-empty">このTaskにはChat履歴がありません</div>'
      : '<div class="chat-empty">このTaskはまだ空です。下から最初の指示を送ってください。</div>'
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

function updateTask(taskId, action) {
  const task = getTask(taskId)
  if (!task) return

  switch (action) {
    case 'toggle-resume':
      if (task.status === 'Completed' || !task.agents.includes('holo')) return
      task.resumeEnabled = !task.resumeEnabled
      break

    case 'complete':
      task.status = 'Completed'
      task.completedAt = Date.now()
      task.resumeEnabled = false
      task.draft = false
      addSystemMessage(task, 'Taskを完了扱いにした。')
      if (!$('dashboard').classList.contains('is-open')) edgeCompletedCount += 1
      break

    case 'restart':
      restartCompletedTask(task)
      return

    case 'close':
      task.closedAt = Date.now()
      if (expandedTaskId === task.id) expandedTaskId = null
      break

    default:
      return
  }

  renderAll()
}

function addSystemMessage(task, text) {
  task.messages.push({ role: 'system', who: 'Nirai', text, time: currentTime() })
}

function restartCompletedTask(sourceTask) {
  if (sourceTask.status !== 'Completed') return

  const id = `task-${Date.now()}`
  const unfinishedSteps = sourceTask.steps.filter((step) => step.status !== 'Completed')
  const steps = unfinishedSteps.length > 0
    ? unfinishedSteps.map((step, index) => ({
        ...step,
        id: `${id}-step-${index + 1}`,
        status: index === 0 ? 'Running' : 'Waiting',
        note: `前Taskから引き継ぎ · ${step.note}`,
      }))
    : [{
        id: `${id}-step-1`,
        title: '続きの作業を整理',
        status: 'Running',
        agent: sourceTask.agents[0] ?? 'holo',
        note: `前Task「${sourceTask.title}」から再開`,
      }]

  tasks.unshift({
    id,
    title: `${sourceTask.title} 続き`,
    type: sourceTask.type ?? 'general',
    status: 'Running',
    agents: [...sourceTask.agents],
    updated: 'たった今',
    draft: false,
    resumeEnabled: false,
    continuedFrom: sourceTask.id,
    steps,
    messages: [
      { role: 'system', who: 'Nirai', text: `前Task「${sourceTask.title}」から新しいTaskとして再開。`, time: currentTime() },
    ],
  })

  expandedTaskId = id
  renderAll()
}

function createTask() {
  const residentId = selectedResidentId ?? residents[0]?.id
  if (!residentId) return

  const id = `task-${Date.now()}`
  const task = {
    id,
    title: '新しいTask',
    type: 'general',
    status: 'NeedsInput',
    agents: [residentId],
    updated: 'たった今',
    draft: true,
    resumeEnabled: false,
    steps: [],
    messages: [],
  }
  tasks.unshift(task)
  expandedTaskId = id
  renderAll()
  requestAnimationFrame(() => {
    $('chatInput').focus()
  })
}

function updateResidentScrollFade() {
  const strip = $('residentStrip')
  if (!strip) return
  const hasMoreRight = strip.scrollWidth - strip.scrollLeft - strip.clientWidth > 2
  strip.classList.toggle('has-more-right', hasMoreRight)
}

function updateTaskScrollFade() {
  const container = $('taskAccordion')
  if (!container) return
  const hasMoreBelow = container.scrollHeight - container.scrollTop - container.clientHeight > 2
  container.classList.toggle('has-more-below', hasMoreBelow)
}

function scheduleCompletedExpiry() {
  window.clearTimeout(completedExpiryTimer)
  const now = Date.now()
  const nextExpiry = tasks
    .filter((task) => task.status === 'Completed' && !task.closedAt && task.completedAt)
    .map((task) => task.completedAt + COMPLETED_VISIBLE_MS)
    .filter((expiresAt) => expiresAt > now)
    .sort((a, b) => a - b)[0]

  if (!nextExpiry) return
  completedExpiryTimer = window.setTimeout(() => {
    const selectedTask = getSelectedTask()
    if (selectedTask?.status === 'Completed' && !isCompletedVisible(selectedTask)) expandedTaskId = null
    renderAll()
  }, Math.max(0, nextExpiry - now) + 50)
}

function setDashboardOpen(open) {
  const edgeDock = $('edgeDock')
  if (!open) setResidentSettingsOpen(false)
  $('dashboard').classList.toggle('is-open', open)
  edgeDock.classList.toggle('is-dashboard-open', open)
  edgeDock.setAttribute('aria-expanded', String(open))
  edgeDock.setAttribute('aria-label', open ? 'Dashboardを格納' : 'Dashboardを開く')
  if (open && edgeCompletedCount > 0) {
    edgeCompletedCount = 0
    renderEdgeStats()
  }
}

function renderAll() {
  const selectedTask = getSelectedTask()
  if (selectedTask?.status === 'Completed' && !isCompletedVisible(selectedTask)) expandedTaskId = null

  renderResidents()
  renderResidentSettings()
  renderEdgeStats()
  renderTaskAccordion()
  renderChat(getSelectedTask())
  scheduleCompletedExpiry()
}

function finishResidentStripDrag(strip, pointerId, preserveMoved = true) {
  residentStripDrag.active = false
  if (!preserveMoved) residentStripDrag.moved = false
  if (strip.hasPointerCapture(pointerId)) strip.releasePointerCapture(pointerId)
  strip.classList.remove('is-dragging')
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
  if ((event.buttons & 1) === 0) {
    finishResidentStripDrag(strip, event.pointerId, false)
    return
  }

  const deltaX = event.clientX - residentStripDrag.startX
  if (!residentStripDrag.moved && Math.abs(deltaX) > 4) {
    residentStripDrag.moved = true
    strip.setPointerCapture(event.pointerId)
  }
  if (!residentStripDrag.moved) return

  strip.classList.add('is-dragging')
  strip.scrollLeft = residentStripDrag.startScrollLeft - deltaX
})
$('residentStrip').addEventListener('pointerup', (event) => {
  finishResidentStripDrag(event.currentTarget, event.pointerId)
})
$('residentStrip').addEventListener('pointercancel', (event) => {
  finishResidentStripDrag(event.currentTarget, event.pointerId, false)
})
$('residentStrip').addEventListener('click', (event) => {
  if (residentStripDrag.moved) {
    residentStripDrag.moved = false
    return
  }

  const chip = event.target.closest('[data-resident-id]')
  if (!chip) return

  selectedResidentId = chip.dataset.residentId
  const task = getSelectedTask()
  if (task?.draft) task.agents = [selectedResidentId]
  renderResidents()
  if (task?.draft) renderChat(task)
})

$('taskAccordion').addEventListener('click', (event) => {
  const item = event.target.closest('[data-task-id]')
  if (!item) return

  const taskId = item.dataset.taskId
  const actionButton = event.target.closest('[data-task-action]')
  if (actionButton) {
    const action = actionButton.dataset.taskAction
    updateTask(taskId, action)
    return
  }

  if (event.target.closest('[data-task-toggle]')) {
    expandedTaskId = expandedTaskId === taskId ? null : taskId
    renderAll()
  }
})

$('edgeDock').addEventListener('click', () => {
  setDashboardOpen(!$('dashboard').classList.contains('is-open'))
})
$('settingsButton').addEventListener('click', () => {
  setResidentSettingsOpen($('residentSettingsPanel').hidden)
})
$('residentSettingsClose').addEventListener('click', () => {
  setResidentSettingsOpen(false)
})
$('residentSettingsPanel').addEventListener('click', (event) => {
  if (event.target === $('residentSettingsPanel')) setResidentSettingsOpen(false)
})
$('residentSettingsList').addEventListener('click', (event) => {
  const editButton = event.target.closest('[data-resident-name-edit]')
  if (editButton) {
    editingResidentId = editButton.dataset.residentNameEdit
    renderResidentSettings()
    requestAnimationFrame(() => {
      const input = document.querySelector(`[data-resident-name-input][data-resident-id="${editingResidentId}"]`)
      input?.focus()
      input?.select()
    })
    return
  }

  const saveButton = event.target.closest('[data-resident-name-save]')
  if (saveButton) {
    const input = saveButton.closest('.resident-setting-edit-row')?.querySelector('[data-resident-name-input]')
    if (input) renameResident(saveButton.dataset.residentId, input.value)
    return
  }

  const promptOpenButton = event.target.closest('[data-resident-prompt-open]')
  if (promptOpenButton) {
    const resident = getResident(promptOpenButton.dataset.residentPromptOpen)
    if (resident?.promptPath) window.open(resident.promptPath, '_blank', 'noopener')
    return
  }

  const deleteButton = event.target.closest('[data-resident-delete]')
  if (deleteButton) setResidentDeleteConfirmOpen(deleteButton.dataset.residentDelete)
})
$('residentSettingsList').addEventListener('change', (event) => {
  if (!event.target.matches('[data-resident-select]')) return
  updateResidentSetting(event.target.dataset.residentId, event.target.dataset.residentSelect, event.target.value)
})
$('residentSettingsList').addEventListener('keydown', (event) => {
  if (event.key !== 'Enter' || !event.target.matches('[data-resident-name-input]')) return
  event.preventDefault()
  renameResident(event.target.dataset.residentId, event.target.value)
})
$('addResidentButton').addEventListener('click', addResident)
$('residentDeleteCancel').addEventListener('click', () => setResidentDeleteConfirmOpen(null))
$('residentDeleteConfirmButton').addEventListener('click', deletePendingResident)
$('residentDeleteConfirm').addEventListener('click', (event) => {
  if (event.target === $('residentDeleteConfirm')) setResidentDeleteConfirmOpen(null)
})
$('resumeButton').addEventListener('click', () => {
  const task = getSelectedTask()
  if (task) updateTask(task.id, 'toggle-resume')
})

$('chatForm').addEventListener('submit', (event) => {
  event.preventDefault()
  const task = getSelectedTask()
  const input = $('chatInput')
  const text = input.value.trim()
  if (!task || !text) return

  task.messages.push({ role: 'master', who: 'Master', text, time: currentTime() })

  if (!task.draft && task.status === 'NeedsInput') {
    task.status = 'Running'
    const waitingStep = task.steps.find((step) => step.status === 'NeedsInput')
    if (waitingStep) {
      waitingStep.status = 'Running'
      waitingStep.note = 'Master回答を受けて再開'
    }
  }

  if (task.draft) {
    const residentId = task.agents[0] ?? 'holo'
    const agentName = residentName(residentId)
    task.draft = false
    task.status = 'Running'
    task.title = text.length > 30 ? `${text.slice(0, 30)}…` : text
    task.steps.push({
      id: `${task.id}-step-1`,
      title: '依頼内容を整理',
      status: 'Running',
      agent: residentId,
      note: '最初の指示から実行計画を整理中',
    })
    task.messages.push({ role: 'agent', who: agentName, text: '了解。新しいTaskとして開始した。まず依頼内容を整理して進める。', time: currentTime() })
  }

  input.value = ''
  resizeComposer()
  renderAll()
})

$('chatInput').addEventListener('input', resizeComposer)
$('residentStrip').addEventListener('wheel', (event) => {
  const strip = event.currentTarget
  if (strip.scrollWidth <= strip.clientWidth) return

  const delta = Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY
  if (!delta) return

  const previousScrollLeft = strip.scrollLeft
  strip.scrollLeft += delta
  if (strip.scrollLeft !== previousScrollLeft) event.preventDefault()
}, { passive: false })
$('residentStrip').addEventListener('scroll', updateResidentScrollFade, { passive: true })
$('taskAccordion').addEventListener('scroll', updateTaskScrollFade, { passive: true })
window.addEventListener('resize', () => requestAnimationFrame(() => {
  updateResidentScrollFade()
  updateTaskScrollFade()
}))

function resizeComposer() {
  const input = $('chatInput')
  input.style.height = 'auto'
  input.style.height = `${Math.min(input.scrollHeight, 128)}px`
}

$('addTaskButton').addEventListener('click', createTask)

window.addEventListener('keydown', (event) => {
  if (event.key !== 'Escape') return
  if (!$('residentDeleteConfirm').hidden) {
    setResidentDeleteConfirmOpen(null)
    return
  }
  if (!$('residentSettingsPanel').hidden) {
    setResidentSettingsOpen(false)
    return
  }
  if ($('dashboard').classList.contains('is-open')) setDashboardOpen(false)
})

renderAll()
setDashboardOpen(true)
