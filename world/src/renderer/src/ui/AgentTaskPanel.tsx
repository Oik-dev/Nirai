import { Fragment, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import type { AgentEventPayload, AgentPendingInputPayload } from '../protocol/types'
import { useAgentStore } from '../stores/agentStore'

interface AgentTaskPanelProps {
  readonly onApproval: (
    agentSessionId: string,
    requestId: string,
    decision: 'approve_once' | 'approve_session' | 'reject' | 'cancel'
  ) => boolean
  readonly onQuestion: (
    agentSessionId: string,
    requestId: string,
    answers: Record<string, readonly string[]>
  ) => boolean
  readonly onPlan: (
    agentSessionId: string,
    requestId: string,
    decision: 'approve' | 'revise' | 'cancel',
    reason?: string
  ) => boolean
  readonly onCancel: (agentSessionId: string) => boolean
}

const TERMINAL_STATES = new Set(['completed', 'failed', 'cancelled', 'interrupted'])

export function canCancelAgentSession(state: string): boolean {
  return state !== 'cancelling' && !TERMINAL_STATES.has(state)
}

function asString(value: unknown): string | null {
  return typeof value === 'string' && value ? value : null
}

function asNumber(value: unknown): number | null {
  return typeof value === 'number' ? value : null
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

function asRecordArray(value: unknown): readonly Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter((item) => asRecord(item) !== null) as Record<string, unknown>[] : []
}

export function findFileChangeApprovalContext(
  events: readonly AgentEventPayload[],
  pending: AgentPendingInputPayload | null | undefined
): AgentEventPayload | null {
  if (pending?.type !== 'approval_request' || pending.payload.kind !== 'file_change') return null
  const operationId = asString(pending.payload.operation_id)
  if (!operationId) return null
  return [...events].reverse().find((event) => (
    event.type === 'file_change' && asString(event.payload.operation_id) === operationId
  )) ?? null
}

function stateLabel(state: string): string {
  return {
    queued: '待機',
    starting: '起動中',
    running: '作業中',
    waiting_for_master: 'Master待ち',
    cancelling: '停止中',
    completed: '完了',
    failed: '失敗',
    cancelled: '停止',
    interrupted: '中断'
  }[state] ?? state
}

function eventTitle(event: AgentEventPayload): string {
  return {
    assistant_message: 'Agent Message',
    status_message: 'Status',
    tool_call: 'Tool Call',
    command_execution: 'Command',
    file_change: 'File Change',
    diff: 'Diff',
    approval_request: 'Approval',
    question_request: 'Question',
    plan: 'Plan',
    todo_update: 'Todo',
    subagent_update: 'Subagent',
    artifact: 'Artifact',
    run_state: 'Run State',
    error: 'Error'
  }[event.type]
}

export interface AgentFileReference {
  readonly path: string
  readonly line: number | null
}

export function safeHttpUrl(raw: string): string | null {
  try {
    const url = new URL(raw)
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.toString() : null
  } catch {
    return null
  }
}

export function parseAgentFileReference(raw: string): AgentFileReference | null {
  const cleaned = raw.trim()
  if (!cleaned || safeHttpUrl(cleaned)) return null
  const lineMatch = cleaned.match(/^(.*\.[A-Za-z0-9_-]+):(\d+)(?::\d+)?$/)
  const path = lineMatch ? lineMatch[1] : cleaned
  const line = lineMatch ? Number(lineMatch[2]) : null
  const windowsAbsolute = /^[A-Za-z]:[\\/]/.test(path)
  const relativeFile = /[\\/]/.test(path) && /\.[A-Za-z0-9_-]+$/.test(path)
  if (!windowsAbsolute && !relativeFile) return null
  return { path, line }
}

function inlineMarkdown(text: string, workingDir: string): ReactNode[] {
  const pieces = text.split(/(`[^`]+`|\[[^\]]+\]\([^)]+\)|https?:\/\/[^\s<>()]+|[A-Za-z]:[\\/][^\s<>]+|(?:\.{0,2}[\\/])?[A-Za-z0-9_.-]+[\\/][A-Za-z0-9_./\\-]+\.[A-Za-z0-9_-]+(?::\d+(?::\d+)?)?)/g)
  return pieces.map((piece, index) => {
    const markdownLink = piece.match(/^\[([^\]]+)\]\(([^)]+)\)$/)
    if (markdownLink) {
      const safeUrl = safeHttpUrl(markdownLink[2])
      return safeUrl ? (
        <button
          key={`${piece}-${index}`}
          type="button"
          className="agent-inline-link"
          onClick={() => { void window.nirai.external.open(safeUrl) }}
        >
          {markdownLink[1]}
        </button>
      ) : <Fragment key={`${piece}-${index}`}>{markdownLink[1]}</Fragment>
    }
    if (piece.startsWith('`') && piece.endsWith('`') && piece.length > 2) {
      const code = piece.slice(1, -1)
      const reference = parseAgentFileReference(code)
      return reference ? (
        <button
          key={`${piece}-${index}`}
          type="button"
          className="agent-file-link"
          onClick={() => { void window.nirai.agent.openFile(reference.path, workingDir) }}
        >
          <code>{code}</code>
        </button>
      ) : <code key={`${piece}-${index}`}>{code}</code>
    }
    const safeUrl = /^https?:\/\//.test(piece) ? safeHttpUrl(piece) : null
    if (safeUrl) {
      return (
        <button
          key={`${piece}-${index}`}
          type="button"
          className="agent-inline-link"
          onClick={() => { void window.nirai.external.open(safeUrl) }}
        >
          {piece}
        </button>
      )
    }
    const reference = parseAgentFileReference(piece)
    if (reference) {
      return (
        <button
          key={`${piece}-${index}`}
          type="button"
          className="agent-file-link"
          onClick={() => { void window.nirai.agent.openFile(reference.path, workingDir) }}
        >
          {piece}
        </button>
      )
    }
    return <Fragment key={`${piece}-${index}`}>{piece}</Fragment>
  })
}

function tableCells(line: string): string[] {
  return line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((cell) => cell.trim())
}

export function AgentMarkdown({ text, workingDir }: { readonly text: string; readonly workingDir: string }): JSX.Element {
  const lines = text.replace(/\r\n/g, '\n').split('\n')
  const nodes: ReactNode[] = []
  let index = 0
  while (index < lines.length) {
    const line = lines[index]
    const fence = line.match(/^```\s*([^`]*)$/)
    if (fence) {
      const codeLanguage = fence[1].trim()
      const codeLines: string[] = []
      index += 1
      while (index < lines.length && !/^```\s*$/.test(lines[index])) {
        codeLines.push(lines[index])
        index += 1
      }
      if (index < lines.length) index += 1
      nodes.push(
        <pre className="agent-code-block" key={`code-${nodes.length}`}>
          {codeLanguage && <small>{codeLanguage}</small>}
          <code>{codeLines.join('\n')}</code>
        </pre>
      )
      continue
    }

    if (
      /^\s*\|.*\|\s*$/.test(line)
      && index + 1 < lines.length
      && /^\s*\|?\s*:?-{3,}/.test(lines[index + 1])
    ) {
      const headers = tableCells(line)
      index += 2
      const rows: string[][] = []
      while (index < lines.length && /^\s*\|.*\|\s*$/.test(lines[index])) {
        rows.push(tableCells(lines[index]))
        index += 1
      }
      nodes.push(
        <table className="agent-markdown-table" key={`table-${nodes.length}`}>
          <thead><tr>{headers.map((cell, cellIndex) => <th key={cellIndex}>{inlineMarkdown(cell, workingDir)}</th>)}</tr></thead>
          <tbody>{rows.map((row, rowIndex) => (
            <tr key={rowIndex}>{row.map((cell, cellIndex) => <td key={cellIndex}>{inlineMarkdown(cell, workingDir)}</td>)}</tr>
          ))}</tbody>
        </table>
      )
      continue
    }

    const heading = line.match(/^(#{1,3})\s+(.+)$/)
    if (heading) {
      const Tag = heading[1].length === 1 ? 'h3' : heading[1].length === 2 ? 'h4' : 'h5'
      nodes.push(<Tag key={`heading-${nodes.length}`}>{inlineMarkdown(heading[2], workingDir)}</Tag>)
      index += 1
      continue
    }
    const bullet = line.match(/^\s*[-*]\s+(.+)$/)
    if (bullet) {
      nodes.push(<p className="agent-markdown-bullet" key={`bullet-${nodes.length}`}>• {inlineMarkdown(bullet[1], workingDir)}</p>)
      index += 1
      continue
    }
    const numbered = line.match(/^\s*(\d+)\.\s+(.+)$/)
    if (numbered) {
      nodes.push(<p className="agent-markdown-bullet" key={`number-${nodes.length}`}>{numbered[1]}. {inlineMarkdown(numbered[2], workingDir)}</p>)
      index += 1
      continue
    }
    const quote = line.match(/^>\s?(.*)$/)
    if (quote) {
      nodes.push(<blockquote key={`quote-${nodes.length}`}>{inlineMarkdown(quote[1], workingDir)}</blockquote>)
      index += 1
      continue
    }
    if (!line.trim()) {
      nodes.push(<span className="agent-markdown-spacer" key={`space-${nodes.length}`} />)
      index += 1
      continue
    }
    // Raw HTML is deliberately never parsed; React renders it as inert text.
    nodes.push(<p key={`line-${nodes.length}`}>{inlineMarkdown(line, workingDir)}</p>)
    index += 1
  }

  return <div className="agent-markdown">{nodes}</div>
}

function StepList({ steps }: { readonly steps: readonly Record<string, unknown>[] }): JSX.Element | null {
  if (steps.length === 0) return null
  return (
    <ol className="agent-step-list">
      {steps.map((step, index) => {
        const text = asString(step.step) ?? asString(step.text) ?? `Step ${index + 1}`
        const status = asString(step.status) ?? 'pending'
        return (
          <li key={`${text}-${index}`} data-status={status}>
            <span>{status === 'completed' ? '✓' : status === 'inProgress' || status === 'in_progress' ? '●' : '○'}</span>
            <span>{text}</span>
          </li>
        )
      })}
    </ol>
  )
}

export function CollapsedText({
  text,
  label,
  className
}: {
  readonly text: string
  readonly label: string
  readonly className?: string
}): JSX.Element {
  const lineCount = text.split('\n').length
  return (
    <details className={className}>
      <summary>{label} · {text.length.toLocaleString()} chars / {lineCount.toLocaleString()} lines</summary>
      <pre><code>{text}</code></pre>
    </details>
  )
}

function FileLink({ path, workingDir }: { readonly path: string; readonly workingDir: string }): JSX.Element {
  return (
    <button
      type="button"
      className="agent-file-link"
      onClick={() => { void window.nirai.agent.openFile(path, workingDir) }}
    >
      {path}
    </button>
  )
}

function EventBody({ event, workingDir }: { readonly event: AgentEventPayload; readonly workingDir: string }): JSX.Element | null {
  const payload = event.payload
  if (event.type === 'assistant_message') {
    const text = asString(payload.text)
    return text ? <AgentMarkdown text={text} workingDir={workingDir} /> : null
  }
  if (event.type === 'status_message') {
    const text = asString(payload.text) ?? asString(payload.message)
    return text ? <p>{text}</p> : null
  }
  if (event.type === 'command_execution') {
    const command = asString(payload.command)
    const cwd = asString(payload.cwd)
    const output = asString(payload.output) ?? asString(payload.delta)
    const exitCode = asNumber(payload.exit_code)
    return (
      <div className="agent-event-stack">
        {command && <pre><code>{command}</code></pre>}
        {cwd && <small>{cwd}</small>}
        {output && <CollapsedText text={output} label="Command output" className="agent-command-output" />}
        {exitCode !== null && <small>exit {exitCode}</small>}
      </div>
    )
  }
  if (event.type === 'file_change') {
    const changes = asRecordArray(payload.changes)
    const delta = asString(payload.delta) ?? asString(payload.diff)
    return (
      <div className="agent-event-stack">
        {changes.map((change, index) => {
          const absolutePath = asString(change.path)
          const displayPath = asString(change.relative_path) ?? absolutePath ?? 'file'
          const diff = asString(change.diff)
          return (
            <div className="agent-file-change" key={`${displayPath}-${index}`}>
              {absolutePath
                ? <FileLink path={absolutePath} workingDir={workingDir} />
                : <strong>{displayPath}</strong>}
              {diff && <CollapsedText text={diff} label="File diff" />}
            </div>
          )
        })}
        {delta && <CollapsedText text={delta} label="File change detail" />}
      </div>
    )
  }
  if (event.type === 'diff') {
    const diff = asString(payload.diff)
    return diff ? <CollapsedText text={diff} label="Diff" className="agent-diff" /> : null
  }
  if (event.type === 'plan' || event.type === 'todo_update') {
    const text = asString(payload.text) ?? asString(payload.explanation)
    const steps = asRecordArray(payload.steps)
    return (
      <div className="agent-event-stack">
        {text && <AgentMarkdown text={text} workingDir={workingDir} />}
        <StepList steps={steps} />
      </div>
    )
  }
  if (event.type === 'tool_call') {
    const tool = asString(payload.tool) ?? asString(payload.tool_type) ?? 'tool'
    const server = asString(payload.server)
    const query = asString(payload.query)
    const status = asString(payload.status)
    const result = payload.result
    return (
      <div className="agent-event-stack">
        <p><strong>{server ? `${server} / ` : ''}{tool}</strong>{status ? ` · ${status}` : ''}</p>
        {query && <pre><code>{query}</code></pre>}
        {result !== undefined && <pre><code>{typeof result === 'string' ? result : JSON.stringify(result, null, 2)}</code></pre>}
      </div>
    )
  }
  if (event.type === 'subagent_update') {
    const status = asString(payload.status)
    const tool = asString(payload.tool)
    const model = asString(payload.model)
    const prompt = asString(payload.prompt)
    return (
      <div className="agent-event-stack">
        <p>{tool ?? 'Subagent'}{model ? ` · ${model}` : ''}{status ? ` · ${status}` : ''}</p>
        {prompt && <AgentMarkdown text={prompt} workingDir={workingDir} />}
      </div>
    )
  }
  if (event.type === 'artifact') {
    const path = asString(payload.savedPath) ?? asString(payload.path)
    return path ? <FileLink path={path} workingDir={workingDir} /> : <p>Artifact created</p>
  }
  if (event.type === 'error') {
    const text = asString(payload.message) ?? 'Agent Runtime error'
    return <p>{text}</p>
  }
  if (event.type === 'approval_request' || event.type === 'question_request') {
    const title = asString(payload.title) ?? eventTitle(event)
    const description = asString(payload.description)
    return <p>{title}{description ? ` · ${description}` : ''}</p>
  }
  return null
}

export function canApprovePendingInput(
  pending: AgentPendingInputPayload,
  contextEvent: AgentEventPayload | null
): boolean {
  return pending.payload.kind !== 'file_change' || contextEvent !== null
}

function PendingApproval({
  agentSessionId,
  pending,
  contextEvent,
  workingDir,
  onApproval
}: {
  readonly agentSessionId: string
  readonly pending: AgentPendingInputPayload
  readonly contextEvent: AgentEventPayload | null
  readonly workingDir: string
  readonly onApproval: AgentTaskPanelProps['onApproval']
}): JSX.Element {
  const payload = pending.payload
  const command = asString(payload.command)
  const cwd = asString(payload.cwd)
  const reason = asString(payload.reason)
  const grantRoot = asString(payload.grant_root)
  const fileChangeNeedsContext = payload.kind === 'file_change'
  const canApprove = canApprovePendingInput(pending, contextEvent)
  return (
    <section className="agent-master-card agent-master-approval" aria-label="Agent承認待ち">
      <header><strong>{asString(payload.title) ?? '承認が必要です'}</strong><span>Master Decision</span></header>
      {command && <pre><code>{command}</code></pre>}
      {cwd && <small>{cwd}</small>}
      {reason && <p>{reason}</p>}
      {grantRoot && <p><strong>Requested write root:</strong> {grantRoot}</p>}
      {fileChangeNeedsContext && !contextEvent && (
        <p role="alert">承認対象のFile Changeを安全に関連付けできないため、許可できません。</p>
      )}
      {contextEvent && (
        <div className="agent-approval-context">
          <small>承認対象の直前変更</small>
          <EventBody event={contextEvent} workingDir={workingDir} />
        </div>
      )}
      <div className="agent-master-actions">
        <button type="button" disabled={!canApprove} onClick={() => onApproval(agentSessionId, pending.request_id, 'approve_once')}>今回だけ許可</button>
        <button type="button" disabled={!canApprove} onClick={() => onApproval(agentSessionId, pending.request_id, 'approve_session')}>このSessionで許可</button>
        <button type="button" onClick={() => onApproval(agentSessionId, pending.request_id, 'reject')}>拒否</button>
        <button type="button" className="is-danger" onClick={() => onApproval(agentSessionId, pending.request_id, 'cancel')}>作業停止</button>
      </div>
    </section>
  )
}

function PendingQuestion({
  agentSessionId,
  pending,
  onQuestion
}: {
  readonly agentSessionId: string
  readonly pending: AgentPendingInputPayload
  readonly onQuestion: AgentTaskPanelProps['onQuestion']
}): JSX.Element {
  const questions = asRecordArray(pending.payload.questions)
  const [answers, setAnswers] = useState<Record<string, string[]>>({})

  useEffect(() => setAnswers({}), [pending.request_id])

  const toggle = (questionId: string, value: string): void => {
    setAnswers((current) => {
      const existing = current[questionId] ?? []
      return {
        ...current,
        [questionId]: existing.includes(value)
          ? existing.filter((candidate) => candidate !== value)
          : [...existing, value]
      }
    })
  }

  const canSubmit = questions.length > 0 && questions.every((question) => {
    const questionId = asString(question.id)
    return questionId !== null && (answers[questionId]?.some((value) => value.trim()) ?? false)
  })

  return (
    <section className="agent-master-card" aria-label="Agent質問待ち">
      <header><strong>{asString(pending.payload.title) ?? 'Agentから質問があります'}</strong><span>Master Input</span></header>
      <div className="agent-question-list">
        {questions.map((question) => {
          const questionId = asString(question.id)
          if (!questionId) return null
          const options = asRecordArray(question.options)
          return (
            <fieldset key={questionId}>
              <legend>{asString(question.header) ?? asString(question.question) ?? questionId}</legend>
              {asString(question.header) && <p>{asString(question.question)}</p>}
              {options.map((option) => {
                const label = asString(option.label)
                if (!label) return null
                return (
                  <label key={label} className="agent-question-option">
                    <input
                      type="checkbox"
                      checked={answers[questionId]?.includes(label) ?? false}
                      onChange={() => toggle(questionId, label)}
                    />
                    <span><strong>{label}</strong>{asString(option.description) && <small>{asString(option.description)}</small>}</span>
                  </label>
                )
              })}
              <input
                type={question.is_secret === true ? 'password' : 'text'}
                aria-label={`${questionId} 自由入力`}
                placeholder={options.length > 0 ? 'その他・補足' : '回答を入力'}
                value={(answers[questionId] ?? []).find((value) => value.startsWith('__free__:'))?.slice(9) ?? ''}
                onChange={(event) => {
                  const value = event.currentTarget.value
                  setAnswers((current) => ({
                    ...current,
                    [questionId]: [
                      ...(current[questionId] ?? []).filter((candidate) => !candidate.startsWith('__free__:')),
                      ...(value ? [`__free__:${value}`] : [])
                    ]
                  }))
                }}
              />
            </fieldset>
          )
        })}
      </div>
      <div className="agent-master-actions">
        <button
          type="button"
          disabled={!canSubmit}
          onClick={() => {
            const normalized = Object.fromEntries(
              Object.entries(answers).map(([questionId, values]) => [
                questionId,
                values.map((value) => value.startsWith('__free__:') ? value.slice(9) : value).filter(Boolean)
              ])
            )
            onQuestion(agentSessionId, pending.request_id, normalized)
          }}
        >
          回答する
        </button>
      </div>
    </section>
  )
}

function PendingPlan({
  agentSessionId,
  pending,
  workingDir,
  onPlan
}: {
  readonly agentSessionId: string
  readonly pending: AgentPendingInputPayload
  readonly workingDir: string
  readonly onPlan: AgentTaskPanelProps['onPlan']
}): JSX.Element {
  const [reason, setReason] = useState('')
  useEffect(() => setReason(''), [pending.request_id])
  const text = asString(pending.payload.text) ?? asString(pending.payload.markdown) ?? asString(pending.payload.explanation)
  return (
    <section className="agent-master-card" aria-label="Agent計画承認待ち">
      <header><strong>Plan確認</strong><span>Master Decision</span></header>
      {text && <AgentMarkdown text={text} workingDir={workingDir} />}
      <StepList steps={asRecordArray(pending.payload.steps)} />
      <textarea value={reason} onChange={(event) => setReason(event.currentTarget.value)} placeholder="修正してほしい点（任意）" />
      <div className="agent-master-actions">
        <button type="button" onClick={() => onPlan(agentSessionId, pending.request_id, 'approve')}>承認</button>
        <button type="button" onClick={() => onPlan(agentSessionId, pending.request_id, 'revise', reason.trim() || undefined)}>修正依頼</button>
        <button type="button" className="is-danger" onClick={() => onPlan(agentSessionId, pending.request_id, 'cancel')}>停止</button>
      </div>
    </section>
  )
}

export function AgentTaskPanel({ onApproval, onQuestion, onPlan, onCancel }: AgentTaskPanelProps): JSX.Element | null {
  const sessions = useAgentStore((state) => state.sessions)
  const order = useAgentStore((state) => state.order)
  const activeSessionId = useAgentStore((state) => state.activeSessionId)
  const setActiveSession = useAgentStore((state) => state.setActiveSession)
  const session = activeSessionId ? sessions[activeSessionId] : null

  const visibleEvents = useMemo(() => {
    if (!session) return []
    const lastTodoSeq = [...session.events].reverse().find((event) => event.type === 'todo_update')?.seq ?? null
    return session.events.filter((event) => {
      if (event.type === 'run_state') return false
      if (event.type === 'todo_update' && event.seq !== lastTodoSeq) return false
      return true
    }).slice(-80)
  }, [session])

  const approvalContextEvent = useMemo(() => (
    session ? findFileChangeApprovalContext(session.events, session.pendingInput) : null
  ), [session])

  if (!session) return null

  return (
    <aside className="agent-task-panel" aria-label="Agent作業監督">
      <header className="agent-task-header">
        <div>
          <strong>{session.resident} · Agent Work</strong>
          <small>{session.taskText ?? session.taskId}</small>
        </div>
        <span className={`agent-run-state is-${session.state}`}>{stateLabel(session.state)}</span>
        {canCancelAgentSession(session.state) && (
          <button type="button" className="agent-cancel-button" onClick={() => onCancel(session.agentSessionId)}>停止</button>
        )}
      </header>

      {order.length > 1 && (
        <nav className="agent-session-tabs" aria-label="Agent Session">
          {order.slice(0, 6).map((agentSessionId) => {
            const candidate = sessions[agentSessionId]
            if (!candidate) return null
            return (
              <button
                type="button"
                key={agentSessionId}
                aria-pressed={agentSessionId === session.agentSessionId}
                onClick={() => setActiveSession(agentSessionId)}
              >
                {candidate.resident} · {stateLabel(candidate.state)}
              </button>
            )
          })}
        </nav>
      )}

      {session.state === 'waiting_for_master' && session.pendingInput?.type === 'approval_request' && (
        <PendingApproval
          agentSessionId={session.agentSessionId}
          pending={session.pendingInput}
          contextEvent={approvalContextEvent}
          workingDir={session.workingDir}
          onApproval={onApproval}
        />
      )}
      {session.state === 'waiting_for_master' && session.pendingInput?.type === 'question_request' && (
        <PendingQuestion agentSessionId={session.agentSessionId} pending={session.pendingInput} onQuestion={onQuestion} />
      )}
      {session.state === 'waiting_for_master' && session.pendingInput?.type === 'plan' && (
        <PendingPlan agentSessionId={session.agentSessionId} pending={session.pendingInput} workingDir={session.workingDir} onPlan={onPlan} />
      )}

      <div className="agent-event-feed">
        {visibleEvents.map((event) => (
          <article className={`agent-event-card is-${event.type}`} key={event.event_id}>
            <header>
              <strong>{eventTitle(event)}</strong>
              <small>{new Date(event.ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}</small>
            </header>
            <EventBody event={event} workingDir={session.workingDir} />
          </article>
        ))}
        {session.finalSummary && (
          <article className="agent-event-card is-summary">
            <header><strong>Completed</strong></header>
            <AgentMarkdown text={session.finalSummary} workingDir={session.workingDir} />
          </article>
        )}
      </div>
    </aside>
  )
}
