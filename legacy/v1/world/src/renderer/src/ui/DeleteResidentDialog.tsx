import { useState } from 'react'

interface DeleteResidentDialogProps {
  readonly residentName: string
  readonly pending: boolean
  readonly error: string | null
  readonly onConfirm: () => void
  readonly onClose: () => void
}

export function DeleteResidentDialog({
  residentName,
  pending,
  error,
  onConfirm,
  onClose
}: DeleteResidentDialogProps): JSX.Element {
  const [confirmation, setConfirmation] = useState('')
  const confirmed = confirmation === 'Delete'

  return (
    <div className="confirm-dialog-backdrop" role="presentation">
      <section className="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="delete-resident-title">
        <h2 id="delete-resident-title">{residentName}を削除</h2>
        <p>
          Resident固有の人格・設定・Private Memoryを完全に削除します。
          VRM本体とWorld Memoryは残ります。
        </p>
        <label>
          確認のため <strong>Delete</strong> と入力
          <input
            type="text"
            autoComplete="off"
            value={confirmation}
            disabled={pending}
            onChange={(event) => setConfirmation(event.currentTarget.value)}
            autoFocus
          />
        </label>
        {error && <p className="resident-setting-error" role="alert">{error}</p>}
        <div className="confirm-dialog-actions">
          <button type="button" disabled={pending} onClick={onClose}>キャンセル</button>
          <button type="button" disabled={!confirmed || pending} onClick={onConfirm}>
            {pending ? '削除中…' : '完全に削除'}
          </button>
        </div>
      </section>
    </div>
  )
}
