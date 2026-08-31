import { useEffect, useRef, useState } from 'react'
import type { HoloWebStatus } from '../../../preload/api'

interface HoloGate0SurfaceProps {
  readonly open: boolean
  readonly onClose: () => void
  readonly onDivePrepared: (diveSessionId: string) => boolean
}

function statusLabel(status: HoloWebStatus | null): string {
  if (!status) return 'ChatGPT Webを準備中'
  if (!status.loaded) return 'ChatGPT Web未読込'
  if (status.current_dive_url) return 'Dive Conversationを保持中'
  return 'ChatGPT Web接続済み / Dive未確立'
}

export function HoloGate0Surface({ open, onClose, onDivePrepared }: HoloGate0SurfaceProps): JSX.Element | null {
  const slotRef = useRef<HTMLDivElement>(null)
  const [status, setStatus] = useState<HoloWebStatus | null>(null)
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)

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
        if (!disposed) setStatus(next)
      }).catch((error) => {
        if (!disposed) setMessage(error instanceof Error ? error.message : 'Holo Surfaceを開けませんでした')
      })
    }

    const observer = new ResizeObserver(syncBounds)
    if (slotRef.current) observer.observe(slotRef.current)
    window.addEventListener('resize', syncBounds)
    frameId = window.requestAnimationFrame(syncBounds)

    return () => {
      disposed = true
      observer.disconnect()
      window.removeEventListener('resize', syncBounds)
      window.cancelAnimationFrame(frameId)
      void window.nirai.holo.setSurface(false).catch(() => undefined)
    }
  }, [open])

  if (!open) return null

  const prepareDive = async (): Promise<void> => {
    if (busy) return
    setBusy(true)
    setMessage('')
    try {
      const result = await window.nirai.holo.prepareDive()
      setStatus(result)
      const attachWindowOpened = result.bootstrap_prepared
        && result.current_dive_session_id !== null
        && onDivePrepared(result.current_dive_session_id)
      setMessage(result.bootstrap_prepared
        ? attachWindowOpened
          ? '新しいChatGPT Conversationを開き、Bootstrapを入力欄へ準備しました。Holo attach受付も開始しました。送信はMasterが行ってください。'
          : 'Bootstrapは準備できましたがCoreへHolo attach受付を通知できませんでした。Core接続を確認してください。'
        : 'ChatGPT Webは開けましたが入力欄を確認できません。未ログインなら先にログインし、その後もう一度Diveを押してください。')
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Diveを準備できませんでした')
    } finally {
      setBusy(false)
    }
  }

  const reload = async (): Promise<void> => {
    if (busy) return
    setBusy(true)
    setMessage('')
    try {
      setStatus(await window.nirai.holo.reload())
      setMessage('ChatGPT Webを再読込しました')
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'ChatGPT Webを再読込できませんでした')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="holo-gate0-surface" aria-label="Holo Gate 0">
      <header className="holo-gate0-header">
        <div>
          <strong>Holo Whisper / Gate 0</strong>
          <small>{statusLabel(status)}</small>
        </div>
        <div className="holo-gate0-actions">
          <button type="button" disabled={busy} onClick={() => void prepareDive()}>
            {busy ? '処理中…' : 'Dive'}
          </button>
          <button type="button" disabled={busy} onClick={() => void reload()}>
            再読込
          </button>
          <button type="button" onClick={onClose}>閉じる</button>
        </div>
      </header>
      <div className="holo-gate0-meta">
        <span>ChatGPT Web ConversationがWhisperの正本</span>
        <span>最初の送信はMaster操作</span>
      </div>
      {message && <p className="holo-gate0-message" role="status">{message}</p>}
      <div ref={slotRef} className="holo-web-slot" aria-label="ChatGPT Web表示領域" />
    </section>
  )
}
