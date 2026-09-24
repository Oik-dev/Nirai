import { useCallback, useEffect, useState } from 'react'

export interface TaskRegistryItem {
  readonly taskId: string
  readonly title: string
  readonly state: string
  readonly kind: 'task' | 'review' | 'workflow'
  readonly resident: string | null
  readonly pendingAutoResumeCount: number
  readonly updatedAt: string | null
}

interface TaskRegistryPanelProps {
  readonly onRefresh: () => Promise<readonly TaskRegistryItem[]>
  readonly onCancel: (taskId: string) => Promise<void>
  readonly onDismiss: (taskId: string) => Promise<void>
}

const DISMISSIBLE_STATES = new Set(['completed', 'failed', 'cancelled', 'done'])

export function taskRegistryItemIsTerminal(item: TaskRegistryItem): boolean {
  return DISMISSIBLE_STATES.has(item.state)
}

export function taskRegistryItemCanCancel(item: TaskRegistryItem): boolean {
  if (item.kind === 'workflow') return true
  if (item.state === 'interrupted') return false
  return !taskRegistryItemIsTerminal(item)
}

function taskKindLabel(kind: TaskRegistryItem['kind']): string {
  return {
    task: 'Task',
    review: 'Review',
    workflow: 'Workflow'
  }[kind]
}

function taskStateLabel(state: string): string {
  return {
    queued: '待機',
    starting: '起動中',
    assigned: '割当済み',
    running: '作業中',
    waiting_for_master: 'Master待ち',
    interrupted: '中断',
    failed: '失敗',
    cancelled: '取消済み',
    completed: '完了',
    done: '完了',
    workflow_active: '監視中',
    workflow_stalled: '再開待ち'
  }[state] ?? state
}

export function TaskRegistryPanel({ onRefresh, onCancel, onDismiss }: TaskRegistryPanelProps): JSX.Element {
  const [items, setItems] = useState<readonly TaskRegistryItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [cancellingTaskId, setCancellingTaskId] = useState<string | null>(null)
  const [dismissingTaskId, setDismissingTaskId] = useState<string | null>(null)

  const refresh = useCallback(async (): Promise<void> => {
    setLoading(true)
    try {
      setItems(await onRefresh())
      setError(null)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Task一覧を取得できませんでした')
    } finally {
      setLoading(false)
    }
  }, [onRefresh])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const cancel = async (taskId: string): Promise<void> => {
    setCancellingTaskId(taskId)
    try {
      await onCancel(taskId)
      await refresh()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Taskを取り消せませんでした')
    } finally {
      setCancellingTaskId(null)
    }
  }

  const dismiss = async (taskId: string): Promise<void> => {
    setDismissingTaskId(taskId)
    try {
      await onDismiss(taskId)
      await refresh()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Taskを一覧から閉じられませんでした')
    } finally {
      setDismissingTaskId(null)
    }
  }

  return (
    <section className="task-registry-panel" aria-label="Task管理">
      <header className="task-registry-header">
        <div>
          <strong>Tasks</strong>
          <small>Task / Review / Auto Resume / Workflow</small>
        </div>
        <button type="button" disabled={loading} onClick={() => void refresh()}>
          {loading ? '更新中' : '更新'}
        </button>
      </header>

      {error && <p className="task-registry-error" role="alert">{error}</p>}
      {!loading && items.length === 0 && (
        <p className="task-registry-empty">管理対象のTaskはありません。</p>
      )}

      <div className="task-registry-list">
        {items.map((item) => {
          const terminal = taskRegistryItemIsTerminal(item)
          const cancellable = taskRegistryItemCanCancel(item)
          return (
            <article className="task-registry-row" key={item.taskId}>
              <div className="task-registry-row-main">
                <div className="task-registry-row-heading">
                  <strong>{item.title}</strong>
                  <span>{taskKindLabel(item.kind)}</span>
                </div>
                <small>
                  {item.taskId} · {taskStateLabel(item.state)}
                  {item.resident ? ` · ${item.resident}` : ''}
                  {item.pendingAutoResumeCount > 0 ? ` · Auto Resume ${item.pendingAutoResumeCount}` : ''}
                </small>
              </div>
              <div className="task-registry-actions">
                {cancellable && (
                  <button
                    type="button"
                    className="task-registry-cancel"
                    disabled={cancellingTaskId !== null || dismissingTaskId !== null}
                    onClick={() => void cancel(item.taskId)}
                  >
                    {cancellingTaskId === item.taskId ? '取消中' : '取消'}
                  </button>
                )}
                {terminal && (
                  <button
                    type="button"
                    className="task-registry-dismiss"
                    disabled={cancellingTaskId !== null || dismissingTaskId !== null}
                    title="Tasks一覧からのみ非表示にします。Work・Agentログ・会話履歴は残ります。"
                    onClick={() => void dismiss(item.taskId)}
                  >
                    {dismissingTaskId === item.taskId ? '整理中' : '閉じる'}
                  </button>
                )}
              </div>
            </article>
          )
        })}
      </div>
    </section>
  )
}
