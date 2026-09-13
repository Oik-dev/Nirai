import { describe, expect, it } from 'vitest'
import {
  taskRegistryItemCanCancel,
  taskRegistryItemIsTerminal,
  type TaskRegistryItem
} from '../../src/renderer/src/ui/TaskRegistryPanel'

function item(state: string): TaskRegistryItem {
  return {
    taskId: `T-${state}`,
    title: state,
    state,
    kind: 'task',
    resident: 'Codex',
    pendingAutoResumeCount: 0,
    updatedAt: null
  }
}

describe('TaskRegistryPanel terminal tickets', () => {
  it('allows only finished or cancelled tickets to be dismissed from the operational list', () => {
    expect(taskRegistryItemIsTerminal(item('completed'))).toBe(true)
    expect(taskRegistryItemIsTerminal(item('done'))).toBe(true)
    expect(taskRegistryItemIsTerminal(item('cancelled'))).toBe(true)
    expect(taskRegistryItemIsTerminal(item('failed'))).toBe(true)
    expect(taskRegistryItemIsTerminal(item('interrupted'))).toBe(false)
    expect(taskRegistryItemIsTerminal(item('waiting_for_master'))).toBe(false)
    expect(taskRegistryItemIsTerminal(item('running'))).toBe(false)
  })

  it('never offers generic cancellation for recoverable interrupted work', () => {
    expect(taskRegistryItemCanCancel(item('running'))).toBe(true)
    expect(taskRegistryItemCanCancel(item('waiting_for_master'))).toBe(true)
    expect(taskRegistryItemCanCancel(item('interrupted'))).toBe(false)
    expect(taskRegistryItemCanCancel(item('failed'))).toBe(false)
    expect(taskRegistryItemCanCancel(item('completed'))).toBe(false)
    expect(taskRegistryItemCanCancel({ ...item('workflow_stalled'), kind: 'workflow' })).toBe(true)
  })
})
