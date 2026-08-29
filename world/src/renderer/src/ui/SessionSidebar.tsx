import { useState } from 'react'
import { useConnectionStore } from '../stores/connectionStore'
import { useSessionStore } from '../stores/sessionStore'
import { useUiStore } from '../stores/uiStore'

interface SessionSidebarProps {
  readonly onCreateSession: () => boolean
  readonly onSelectSession: (sessionId: string) => boolean
  readonly onDeleteSession: (sessionId: string) => boolean
  readonly onForgetSession: (sessionId: string) => boolean
}

export function SessionSidebar({
  onCreateSession,
  onSelectSession,
  onDeleteSession,
  onForgetSession
}: SessionSidebarProps): JSX.Element {
  const [menuSessionId, setMenuSessionId] = useState<string | null>(null)
  const open = useUiStore((state) => state.leftSidebarOpen)
  const toggle = useUiStore((state) => state.toggleLeftSidebar)
  const sessions = useSessionStore((state) => state.sessions)
  const activeSessionId = useSessionStore((state) => state.activeSessionId)
  const connected = useConnectionStore((state) => state.status === 'connected')
  const responseActive = useConnectionStore((state) => state.activeRequestId !== null)

  return (
    <>
      <button
        className="sidebar-toggle sidebar-toggle-left"
        type="button"
        aria-label="チャットセッションを開く"
        aria-expanded={open}
        onClick={toggle}
      >
        ≡
      </button>
      <aside className={`side-panel side-panel-left${open ? ' is-open' : ''}`} aria-label="チャットセッション">
        <header>
          <strong>Chats</strong>
        </header>
        <button
          className="side-panel-primary"
          type="button"
          disabled={!connected}
          onClick={() => onCreateSession()}
        >
          ＋ 新しいチャット
        </button>
        <div className="side-panel-group">
          <small>最近</small>
          {sessions.length === 0 ? (
            <p className="session-empty">チャット履歴はありません</p>
          ) : sessions.map((session) => (
            <div className="session-row" key={session.id}>
              <button
                className={`session-row-select${session.id === activeSessionId ? ' is-selected' : ''}`}
                type="button"
                disabled={!connected || session.id === activeSessionId}
                aria-current={session.id === activeSessionId ? 'page' : undefined}
                onClick={() => onSelectSession(session.id)}
              >
                {session.title}
              </button>
              <button
                className="session-row-menu-button"
                type="button"
                disabled={!connected || responseActive}
                aria-label={`${session.title}の操作`}
                aria-expanded={menuSessionId === session.id}
                onClick={() => setMenuSessionId((current) => current === session.id ? null : session.id)}
              >
                …
              </button>
              {menuSessionId === session.id && (
                <div className="session-row-menu">
                  <button
                    type="button"
                    onClick={() => {
                      if (!window.confirm(`「${session.title}」のチャット履歴を削除しますか？\n世界の記憶は残ります。`)) return
                      if (onDeleteSession(session.id)) setMenuSessionId(null)
                    }}
                  >
                    履歴を削除
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      if (!window.confirm(`「${session.title}」を世界の記憶から忘れさせますか？\nチャット履歴も削除されます。`)) return
                      if (onForgetSession(session.id)) setMenuSessionId(null)
                    }}
                  >
                    世界の記憶から忘れさせる
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      </aside>
    </>
  )
}
