import { useEffect, useRef, useState } from 'react'
import type { HoloAddonStatus } from '../../../preload/api'
import type { HoloLocalBridgeState } from '../protocol/types'
import { createHoloAttachDeadlineMs } from '../runtime/holoDiveSync'
import { clampStoredPanelHeight, clampTopResizeHeight } from './topResize'

interface HoloWhisperSurfaceProps {
  readonly open: boolean
  readonly coreConnected: boolean
  readonly localBridgeState: HoloLocalBridgeState
  readonly coreDiveSessionId: string | null
  readonly onClose: () => void
  readonly onDivePrepared: (diveSessionId: string, attachExpiresAtMs: number) => boolean
  readonly onRequestBridgeStatus: () => boolean
  readonly onStatusChange: (status: HoloAddonStatus) => void
}

export function holoSurfaceStatusLabel(status: HoloAddonStatus | null): string {
  if (!status || status.phase === 'loading') return 'ChatGPTを準備中'
  if (status.phase === 'unavailable' || status.phase === 'error') return 'ChatGPTを表示できません'
  if (status.dive_state === 'current') return '会話を継続できます'
  if (status.dive_state === 'preparing') return '新しいDiveを準備中'
  return 'ChatGPTを利用できます'
}

export function holoPersistenceWarning(status: HoloAddonStatus | null): string | null {
  if (status?.persistence_issue !== 'state_persistence_failed') return null
  return '現在のDiveを保存できていないため、再起動すると失われる可能性があります。'
}

export function holoBridgeStatusLabel(
  coreConnected: boolean,
  localBridgeState: HoloLocalBridgeState,
  webDiveSessionId: string | null,
  coreDiveSessionId: string | null
): string {
  if (!coreConnected) return 'Nirai連携を確認できません'
  if (webDiveSessionId !== null && coreDiveSessionId !== webDiveSessionId) {
    return 'Nirai連携不一致'
  }
  if (localBridgeState === 'attached' && webDiveSessionId !== null) return 'Nirai連携済み'
  if (localBridgeState === 'attach_waiting' && webDiveSessionId !== null) return 'Nirai連携待ち'
  if (webDiveSessionId !== null) return 'Nirai連携未開始'
  return 'Dive未開始'
}

export function HoloWhisperSurface({
  open,
  coreConnected,
  localBridgeState,
  coreDiveSessionId,
  onClose,
  onDivePrepared,
  onRequestBridgeStatus,
  onStatusChange
}: HoloWhisperSurfaceProps): JSX.Element | null {
  const surfaceRef = useRef<HTMLElement>(null)
  const slotRef = useRef<HTMLDivElement>(null)
  const resizeRef = useRef<{
    readonly pointerId: number
    readonly startY: number
    readonly startHeight: number
    readonly minimum: number
    readonly maximum: number
  } | null>(null)
  const [surfaceHeight, setSurfaceHeight] = useState<number | null>(null)
  const [status, setStatus] = useState<HoloAddonStatus | null>(null)
  const [message, setMessage] = useState('')
  const [busyAction, setBusyAction] = useState<'dive' | 'reload' | null>(null)
  const [engaged, setEngaged] = useState(false)

  const acceptStatus = (next: HoloAddonStatus): void => {
    setStatus(next)
    onStatusChange(next)
  }

  useEffect(() => {
    if (!open) return
    let disposed = false
    let frameId = 0

    const syncBounds = (): void => {
      const slot = slotRef.current
      if (!slot) return
      const rect = slot.getBoundingClientRect()
      if (rect.width < 1 || rect.height < 1) return
      void window.nirai.holo.setSurface(true, {
        x: rect.left,
        y: rect.top,
        width: rect.width,
        height: rect.height
      }).then((next) => {
        if (!disposed) acceptStatus(next)
      }).catch((error) => {
        if (!disposed) setMessage(error instanceof Error ? error.message : 'Holo Whisperを開けませんでした')
      })
    }

    const constrainStoredHeight = (): void => {
      const surface = surfaceRef.current
      if (!surface) return
      const rect = surface.getBoundingClientRect()
      const bottomGap = Math.max(0, window.innerHeight - rect.bottom)
      setSurfaceHeight((current) => current === null ? null : clampStoredPanelHeight(current, {
        viewportHeight: window.innerHeight,
        bottomGap,
        topClearance: 48,
        minimum: 20 * 16
      }))
    }

    const handleWindowResize = (): void => {
      constrainStoredHeight()
      syncBounds()
    }

    const refreshStatus = (): void => {
      void window.nirai.holo.status().then((next) => {
        if (!disposed) acceptStatus(next)
      }).catch(() => undefined)
      onRequestBridgeStatus()
    }

    const observer = new ResizeObserver(syncBounds)
    if (slotRef.current) observer.observe(slotRef.current)
    window.addEventListener('resize', handleWindowResize)
    frameId = window.requestAnimationFrame(() => {
      constrainStoredHeight()
      syncBounds()
    })
    refreshStatus()
    const statusTimer = window.setInterval(refreshStatus, 1000)
    // Chat log behavior: the glass darkens while Master is pressed into the
    // conversation. ChatGPT clicks are only observable as native view focus.
    window.nirai.holo.onWebFocusChanged((focused) => {
      if (!disposed) setEngaged(focused)
    })

    return () => {
      disposed = true
      observer.disconnect()
      window.removeEventListener('resize', handleWindowResize)
      window.cancelAnimationFrame(frameId)
      window.clearInterval(statusTimer)
      window.nirai.holo.offWebFocusChanged()
      setEngaged(false)
      void window.nirai.holo.setSurface(false).catch(() => undefined)
    }
  }, [open])

  if (!open) return null

  const prepareDive = async (): Promise<void> => {
    if (busyAction) return
    const attachExpiresAtMs = createHoloAttachDeadlineMs()
    setBusyAction('dive')
    setMessage('')
    try {
      const result = await window.nirai.holo.prepareDive()
      acceptStatus(result)
      const attachWindowOpened = result.bootstrap_prepared
        && result.current_dive_session_id !== null
        && onDivePrepared(result.current_dive_session_id, attachExpiresAtMs)
      setMessage(result.bootstrap_prepared
        ? attachWindowOpened
          ? '新しいConversationへDiveの準備を入力しました。内容を確認して送信してください。'
          : 'Conversationの準備は完了しましたが、Nirai連携を開始できませんでした。Core接続を確認してください。'
        : 'ChatGPTの入力欄を確認できませんでした。ログイン状態を確認して、もう一度Diveを押してください。')
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Diveを準備できませんでした')
    } finally {
      setBusyAction(null)
    }
  }

  const reload = async (): Promise<void> => {
    if (busyAction) return
    setBusyAction('reload')
    setMessage('')
    try {
      acceptStatus(await window.nirai.holo.reload())
      setMessage('ChatGPTを再読込しています')
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'ChatGPTを再読込できませんでした')
    } finally {
      setBusyAction(null)
    }
  }

  const beginResize = (event: React.PointerEvent<HTMLDivElement>): void => {
    const surface = surfaceRef.current
    if (!surface) return
    const rect = surface.getBoundingClientRect()
    const bottomGap = Math.max(0, window.innerHeight - rect.bottom)
    resizeRef.current = {
      pointerId: event.pointerId,
      startY: event.clientY,
      startHeight: rect.height,
      minimum: 20 * 16,
      maximum: Math.max(1, window.innerHeight - bottomGap - 48)
    }
    event.currentTarget.setPointerCapture(event.pointerId)
    event.preventDefault()
  }

  const resize = (event: React.PointerEvent<HTMLDivElement>): void => {
    const current = resizeRef.current
    if (!current || current.pointerId !== event.pointerId) return
    setSurfaceHeight(clampTopResizeHeight(
      current.startHeight,
      current.startY,
      event.clientY,
      { minimum: current.minimum, maximum: current.maximum }
    ))
  }

  const endResize = (event: React.PointerEvent<HTMLDivElement>): void => {
    const current = resizeRef.current
    if (!current || current.pointerId !== event.pointerId) return
    resizeRef.current = null
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
  }

  const phase = status?.phase ?? 'loading'
  const persistenceWarning = holoPersistenceWarning(status)
  return (
    <section
      ref={surfaceRef}
      className={`holo-whisper-surface is-${phase}${engaged ? ' is-engaged' : ''}${surfaceHeight !== null ? ' is-user-resized' : ''}`}
      aria-label="Holo Whisper"
      style={surfaceHeight === null ? undefined : { top: 'auto', height: `${surfaceHeight}px` }}
    >
      <div
        className="panel-top-resize-handle holo-whisper-resize-handle"
        role="separator"
        aria-label="Holo Whisperの高さを変更"
        aria-orientation="horizontal"
        onPointerDown={beginResize}
        onPointerMove={resize}
        onPointerUp={endResize}
        onPointerCancel={endResize}
      />
      <header className="holo-whisper-header">
        <div className="holo-whisper-heading">
          <strong>Holo Whisper</strong>
          <span className={`holo-whisper-state is-${phase}`}>{holoSurfaceStatusLabel(status)}</span>
          <small>{holoBridgeStatusLabel(
            coreConnected,
            localBridgeState,
            status?.current_dive_session_id ?? null,
            coreDiveSessionId
          )}</small>
        </div>
        <div className="holo-whisper-actions">
          <button className="is-primary" type="button" disabled={busyAction !== null} onClick={() => void prepareDive()}>
            {busyAction === 'dive' ? '準備中…' : 'Dive'}
          </button>
          <button
            className="is-secondary"
            type="button"
            disabled={busyAction !== null}
            aria-label="ChatGPTを再読込"
            title="再読込"
            onClick={() => void reload()}
          >
            {busyAction === 'reload' ? '…' : '↻'}
          </button>
          <button className="is-secondary" type="button" onClick={onClose}>閉じる</button>
        </div>
      </header>
      <p
        className={`holo-whisper-message${persistenceWarning ? ' is-warning' : ''}`}
        role={persistenceWarning ? 'alert' : 'status'}
        aria-live={persistenceWarning ? 'assertive' : 'polite'}
      >
        {persistenceWarning ?? (message || '\u00A0')}
      </p>
      <div ref={slotRef} className="holo-web-slot" aria-label="ChatGPT Conversation" />
    </section>
  )
}
