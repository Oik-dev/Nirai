import { useEffect, useState } from 'react'
import type { AgentEventPayload, AgentPendingInputPayload } from '../protocol/types'
import type { AgentTaskPanelProps } from './agentPresentation'
import {
  asString, asRecordArray, approvalOptionIsSupported, canApprovePendingInput,
  questionAllowsMultiple, questionAllowsFreeText
} from './agentPresentation'
import { AgentMarkdown } from './AgentMarkdown'
import { EventBody, StepList } from './AgentEventBody'

export function PendingApproval({
  agentSessionId,
  pending,
  contextEvent,
  onApproval
}: {
  readonly agentSessionId: string
  readonly pending: AgentPendingInputPayload
  readonly contextEvent: AgentEventPayload | null
  readonly onApproval: AgentTaskPanelProps['onApproval']
}): JSX.Element {
  const payload = pending.payload
  const command = asString(payload.command)
  const cwd = asString(payload.cwd)
  const reason = asString(payload.reason)
  const grantRoot = asString(payload.grant_root)
  const fileChangeNeedsContext = payload.kind === 'file_change'
  const canApprove = canApprovePendingInput(pending, contextEvent)
  const supports = (option: string): boolean => approvalOptionIsSupported(payload.options, option)
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
          <EventBody event={contextEvent} agentSessionId={agentSessionId} />
        </div>
      )}
      <div className="agent-master-actions">
        {supports('approve_once') && <button type="button" disabled={!canApprove} onClick={() => onApproval(agentSessionId, pending.request_id, 'approve_once')}>今回だけ許可</button>}
        {supports('approve_session') && <button type="button" disabled={!canApprove} onClick={() => onApproval(agentSessionId, pending.request_id, 'approve_session')}>このSessionで許可</button>}
        {supports('reject') && <button type="button" onClick={() => onApproval(agentSessionId, pending.request_id, 'reject')}>拒否</button>}
        {supports('cancel') && <button type="button" className="is-danger" onClick={() => onApproval(agentSessionId, pending.request_id, 'cancel')}>作業停止</button>}
      </div>
    </section>
  )
}

export function PendingQuestion({
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

  const choose = (questionId: string, value: string, allowMultiple: boolean): void => {
    setAnswers((current) => {
      const existing = current[questionId] ?? []
      const freeText = existing.filter((candidate) => candidate.startsWith('__free__:'))
      if (!allowMultiple) {
        return { ...current, [questionId]: [value, ...freeText] }
      }
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
          const allowMultiple = questionAllowsMultiple(question)
          const allowFreeText = questionAllowsFreeText(question)
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
                      type={allowMultiple ? 'checkbox' : 'radio'}
                      name={`agent-question-${questionId}`}
                      checked={answers[questionId]?.includes(label) ?? false}
                      onChange={() => choose(questionId, label, allowMultiple)}
                    />
                    <span><strong>{label}</strong>{asString(option.description) && <small>{asString(option.description)}</small>}</span>
                  </label>
                )
              })}
              {allowFreeText && (
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
              )}
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

export function PendingPlan({
  agentSessionId,
  pending,
  onPlan
}: {
  readonly agentSessionId: string
  readonly pending: AgentPendingInputPayload
  readonly onPlan: AgentTaskPanelProps['onPlan']
}): JSX.Element {
  const [reason, setReason] = useState('')
  useEffect(() => setReason(''), [pending.request_id])
  const text = asString(pending.payload.text) ?? asString(pending.payload.markdown) ?? asString(pending.payload.explanation)
  return (
    <section className="agent-master-card" aria-label="Agent計画承認待ち">
      <header><strong>Plan確認</strong><span>Master Decision</span></header>
      {text && <AgentMarkdown text={text} agentSessionId={agentSessionId} />}
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
