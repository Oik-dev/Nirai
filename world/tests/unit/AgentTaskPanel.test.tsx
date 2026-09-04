import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { AgentEventPayload, AgentPendingInputPayload } from '../../src/renderer/src/protocol/types'
import {
  AgentMarkdown,
  CollapsedText,
  canApprovePendingInput,
  canCancelAgentSession,
  findFileChangeApprovalContext,
  parseAgentFileReference,
  safeHttpUrl
} from '../../src/renderer/src/ui/AgentTaskPanel'

function fileEvent(seq: number, operationId: string): AgentEventPayload {
  return {
    event_id: `AE-AS-1-${seq}`,
    seq,
    ts: '2026-09-04T00:00:00+09:00',
    task_id: 'TASK-1',
    agent_session_id: 'AS-1',
    resident: 'Codex',
    provider: 'codex',
    type: 'file_change',
    payload: {
      operation_id: operationId,
      changes: [{ path: `D:\\Products\\Nirai\\runtime\\workspace\\TASK-1\\${operationId}.txt` }]
    }
  }
}

function pending(operationId: string): AgentPendingInputPayload {
  return {
    type: 'approval_request',
    request_id: 'approval-1',
    payload: {
      kind: 'file_change',
      operation_id: operationId,
      grant_root: 'D:\\Products\\Nirai\\runtime\\workspace\\TASK-1'
    }
  }
}

describe('AgentTaskPanel safety helpers', () => {
  it('correlates File Change approval only to the matching operation_id', () => {
    const unrelated = fileEvent(1, 'file-other')
    const matching = fileEvent(2, 'file-target')
    const approval = pending('file-target')

    expect(findFileChangeApprovalContext([unrelated, matching], approval)?.event_id).toBe(matching.event_id)
    expect(findFileChangeApprovalContext([unrelated], approval)).toBeNull()
    expect(canApprovePendingInput(approval, null)).toBe(false)
    expect(canApprovePendingInput(approval, matching)).toBe(true)
  })

  it('accepts only http/https URLs and parses file references with line numbers', () => {
    expect(safeHttpUrl('https://example.com/path')).toBe('https://example.com/path')
    expect(safeHttpUrl('javascript:alert(1)')).toBeNull()
    expect(parseAgentFileReference('src/main.ts:42')).toEqual({ path: 'src/main.ts', line: 42 })
    expect(parseAgentFileReference('https://example.com/file.ts')).toBeNull()
  })

  it('renders tables and markdown links while leaving raw HTML inert', () => {
    const html = renderToStaticMarkup(
      <AgentMarkdown
        workingDir="D:\\Products\\Nirai\\runtime\\workspace\\TASK-1"
        text={'| Name | Value |\n| --- | --- |\n| A | B |\n[Open](https://example.com)\n[Bad](javascript:alert(1))\n<script>alert(1)</script>'}
      />
    )

    expect(html).toContain('<table')
    expect(html).toContain('agent-inline-link')
    expect(html).toContain('&lt;script&gt;alert(1)&lt;/script&gt;')
    expect(html).not.toContain('<script>')
    expect(html).not.toContain('javascript:alert')
  })

  it('does not offer another cancel while cancellation is already in progress', () => {
    expect(canCancelAgentSession('running')).toBe(true)
    expect(canCancelAgentSession('waiting_for_master')).toBe(true)
    expect(canCancelAgentSession('cancelling')).toBe(false)
    expect(canCancelAgentSession('cancelled')).toBe(false)
    expect(canCancelAgentSession('failed')).toBe(false)
  })

  it('keeps large output collapsed by default', () => {
    const html = renderToStaticMarkup(<CollapsedText text={'x\n'.repeat(200)} label="Diff" />)
    expect(html).toContain('<details>')
    expect(html).not.toContain('<details open="">')
    expect(html).toContain('<summary>Diff')
  })
})
