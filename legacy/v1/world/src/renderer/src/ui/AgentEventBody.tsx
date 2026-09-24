import type { AgentEventPayload } from '../protocol/types'
import { AgentMarkdown } from './AgentMarkdown'
import { asString, asNumber, asRecordArray, eventTitle } from './agentPresentation'

export function StepList({ steps }: { readonly steps: readonly Record<string, unknown>[] }): JSX.Element | null {
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

function FileLink({ path, agentSessionId }: { readonly path: string; readonly agentSessionId: string }): JSX.Element {
  return (
    <button
      type="button"
      className="agent-file-link"
      onClick={() => { void window.nirai.agent.openFile(path, agentSessionId) }}
    >
      {path}
    </button>
  )
}

export function EventBody({ event, agentSessionId }: { readonly event: AgentEventPayload; readonly agentSessionId: string }): JSX.Element | null {
  const payload = event.payload
  if (event.type === 'assistant_message') {
    const text = asString(payload.text)
    return text ? <AgentMarkdown text={text} agentSessionId={agentSessionId} /> : null
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
                ? <FileLink path={absolutePath} agentSessionId={agentSessionId} />
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
        {text && <AgentMarkdown text={text} agentSessionId={agentSessionId} />}
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
        {prompt && <AgentMarkdown text={prompt} agentSessionId={agentSessionId} />}
      </div>
    )
  }
  if (event.type === 'artifact') {
    const path = asString(payload.savedPath) ?? asString(payload.path)
    return path ? <FileLink path={path} agentSessionId={agentSessionId} /> : <p>Artifact created</p>
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
