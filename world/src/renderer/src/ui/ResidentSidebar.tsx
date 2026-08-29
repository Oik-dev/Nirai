import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import type { ResidentTtsPayload } from '../protocol/types'
import { useConnectionStore } from '../stores/connectionStore'
import { useResidentStore } from '../stores/residentStore'
import { useUiStore } from '../stores/uiStore'
import { DeleteResidentDialog } from './DeleteResidentDialog'
import { VoiceSettingsPanel } from './VoiceSettingsPanel'

interface ResidentSidebarProps {
  readonly debugContent?: ReactNode
  readonly operationNotice?: { readonly key: number; readonly text: string } | null
  readonly onCreateResident: (name: string, provider: string) => boolean
  readonly onSetBrain: (name: string, provider: string) => boolean
  readonly onSetAvatar: (name: string, avatarPath: string) => boolean
  readonly onSetTts: (name: string, tts: ResidentTtsPayload) => boolean
  readonly onPreviewVoice: (audio: Uint8Array) => Promise<void>
  readonly onDeleteResident: (name: string, confirm: string) => boolean
}

const INVALID_WINDOWS_NAME = /[<>:"/\\|?*\u0000-\u001f]/
const RESERVED_WINDOWS_NAMES = new Set([
  'CON', 'PRN', 'AUX', 'NUL',
  ...Array.from({ length: 9 }, (_, index) => `COM${index + 1}`),
  ...Array.from({ length: 9 }, (_, index) => `LPT${index + 1}`)
])

function validateResidentName(name: string, existingNames: readonly string[]): string | null {
  const cleaned = name.trim()
  if (!cleaned) return '名前を入力してください'
  if (cleaned === '.' || cleaned === '..') return 'その名前は使用できません'
  if (cleaned.endsWith('.') || cleaned.endsWith(' ')) return '末尾にピリオドや空白は使用できません'
  if (INVALID_WINDOWS_NAME.test(cleaned)) return 'Windowsで使用できない文字が含まれています'
  if (RESERVED_WINDOWS_NAMES.has(cleaned.split('.', 1)[0].toUpperCase())) {
    return 'Windowsで予約されている名前は使用できません'
  }
  if (existingNames.some((candidate) => candidate.localeCompare(cleaned, undefined, { sensitivity: 'accent' }) === 0)) {
    return '同名のResidentが既に存在します'
  }
  return null
}

function brainLabel(brain: string | null): string {
  if (brain === null) return '未設定'
  if (brain === 'codex') return 'Codex'
  if (brain === 'claude-code') return 'Claude'
  if (brain === 'cursor') return 'Cursor'
  if (brain === 'gemini') return 'Gemini'
  if (brain === 'local-llm') return 'Local LLM'
  return brain
}

export function ResidentSidebar({
  debugContent,
  operationNotice,
  onCreateResident,
  onSetBrain,
  onSetAvatar,
  onSetTts,
  onPreviewVoice,
  onDeleteResident
}: ResidentSidebarProps): JSX.Element {
  const [creating, setCreating] = useState(false)
  const [nameDraft, setNameDraft] = useState('')
  const [brainDraft, setBrainDraft] = useState('')
  const [createError, setCreateError] = useState<string | null>(null)
  const [pendingName, setPendingName] = useState<string | null>(null)
  const [brainEditName, setBrainEditName] = useState<string | null>(null)
  const [brainEditDraft, setBrainEditDraft] = useState('')
  const [pendingBrain, setPendingBrain] = useState<{ name: string; provider: string } | null>(null)
  const [brainError, setBrainError] = useState<string | null>(null)
  const [pendingAvatar, setPendingAvatar] = useState<{ name: string; path: string } | null>(null)
  const [avatarError, setAvatarError] = useState<string | null>(null)
  const [personaError, setPersonaError] = useState<string | null>(null)
  const [deleteTargetName, setDeleteTargetName] = useState<string | null>(null)
  const [pendingDeleteName, setPendingDeleteName] = useState<string | null>(null)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const open = useUiStore((state) => state.rightSidebarOpen)
  const mode = useUiStore((state) => state.rightSidebarMode)
  const openRightSidebar = useUiStore((state) => state.openRightSidebar)
  const closeRightSidebar = useUiStore((state) => state.closeRightSidebar)
  const voicePanelResidentName = useUiStore((state) => state.voicePanelResidentName)
  const setVoicePanelResidentName = useUiStore((state) => state.setVoicePanelResidentName)
  const connected = useConnectionStore((state) => state.status === 'connected')
  const responseActive = useConnectionStore((state) => state.activeRequestId !== null)
  const residents = useResidentStore((state) => state.residents)
  const providerStatuses = useResidentStore((state) => state.providerStatuses)
  const expandedResidentName = useResidentStore((state) => state.expandedResidentName)
  const setExpandedResidentName = useResidentStore((state) => state.setExpandedResidentName)
  const residentLimitReached = residents.length >= 1

  useEffect(() => {
    if (pendingName == null) return
    if (!residents.some((resident) => resident.name === pendingName)) return
    setPendingName(null)
    setNameDraft('')
    setBrainDraft('')
    setCreateError(null)
    setCreating(false)
    setExpandedResidentName(pendingName)
  }, [pendingName, residents, setExpandedResidentName])

  useEffect(() => {
    if (pendingBrain == null) return
    const resident = residents.find((candidate) => candidate.name === pendingBrain.name)
    if (resident?.brain !== pendingBrain.provider) return
    setPendingBrain(null)
    setBrainEditName(null)
    setBrainEditDraft('')
    setBrainError(null)
  }, [pendingBrain, residents])

  useEffect(() => {
    if (pendingAvatar == null) return
    const resident = residents.find((candidate) => candidate.name === pendingAvatar.name)
    if (resident?.avatar !== pendingAvatar.path) return
    setPendingAvatar(null)
    setAvatarError(null)
  }, [pendingAvatar, residents])

  useEffect(() => {
    if (pendingDeleteName == null) return
    if (residents.some((resident) => resident.name === pendingDeleteName)) return
    setPendingDeleteName(null)
    setDeleteTargetName(null)
    setDeleteError(null)
  }, [pendingDeleteName, residents])

  useEffect(() => {
    if (!operationNotice) return
    const text = operationNotice.text
    if (pendingName !== null) {
      setPendingName(null)
      setCreateError(text)
    }
    if (pendingBrain !== null) {
      setPendingBrain(null)
      setBrainError(text)
    }
    if (pendingAvatar !== null) {
      setPendingAvatar(null)
      setAvatarError(text)
    }
    if (pendingDeleteName !== null) {
      setPendingDeleteName(null)
      setDeleteError(text)
    }
  }, [operationNotice?.key])

  const pickAvatar = async (residentName: string): Promise<void> => {
    if (!connected || pendingAvatar !== null) return
    setAvatarError(null)
    try {
      const avatarPath = await window.nirai.avatar.pick()
      if (!avatarPath) return
      if (!onSetAvatar(residentName, avatarPath)) {
        setAvatarError('Avatar設定をCoreへ送信できませんでした')
        return
      }
      setPendingAvatar({ name: residentName, path: avatarPath.replace(/\\/g, '/') })
    } catch (error) {
      setAvatarError(error instanceof Error ? error.message : 'VRMを選択できませんでした')
    }
  }

  const openPersona = async (residentName: string): Promise<void> => {
    setPersonaError(null)
    try {
      await window.nirai.persona.open(residentName)
    } catch (error) {
      setPersonaError(error instanceof Error ? error.message : 'Promptを開けませんでした')
    }
  }

  const submitCreate = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault()
    const name = nameDraft.trim()
    const error = validateResidentName(name, residents.map((resident) => resident.name))
    if (error) {
      setCreateError(error)
      return
    }
    if (residentLimitReached) {
      setCreateError('M1ではResidentは1人までです。現在のResidentを削除してから新規作成してください')
      return
    }
    if (!brainDraft) {
      setCreateError('AIを選択してください')
      return
    }
    const provider = providerStatuses.find((candidate) => candidate.name === brainDraft)
    if (!provider?.available) {
      setCreateError('選択したAIは現在利用できません')
      return
    }
    if (!connected) {
      setCreateError('Coreへ接続してから作成してください')
      return
    }
    if (!onCreateResident(name, brainDraft)) {
      setCreateError('Resident作成をCoreへ送信できませんでした')
      return
    }
    setCreateError(null)
    setPendingName(name)
  }

  return (
    <>
      <div className="right-sidebar-launchers" aria-label="右サイドバー切替">
        {debugContent && (
          <button
            className="sidebar-toggle sidebar-toggle-debug"
            type="button"
            aria-label="Debugを開く"
            aria-pressed={open && mode === 'debug'}
            onClick={() => {
              if (open && mode === 'debug') closeRightSidebar()
              else openRightSidebar('debug')
            }}
          >
            Debug
          </button>
        )}
        <button
          className="sidebar-toggle sidebar-toggle-right"
          type="button"
          aria-label="Resident設定を開く"
          aria-pressed={open && mode === 'resident'}
          onClick={() => {
            if (open && mode === 'resident') closeRightSidebar()
            else openRightSidebar('resident')
          }}
        >
          ⚙
        </button>
      </div>

      <aside
        className={`side-panel side-panel-right${open ? ' is-open' : ''}`}
        aria-label={mode === 'debug' ? 'Debug' : 'Resident設定'}
      >
        <header>
          <strong>{mode === 'debug' ? 'Debug' : 'Residents'}</strong>
        </header>

        {mode === 'debug' && debugContent ? (
          <div className="debug-sidebar-content">{debugContent}</div>
        ) : (
          <div className="resident-sidebar-content">
            <button
              className="side-panel-primary"
              type="button"
              disabled={!connected || pendingName !== null || residentLimitReached}
              title={residentLimitReached ? 'M1ではResidentは1人までです' : undefined}
              onClick={() => {
                setCreating((current) => !current)
                setCreateError(null)
              }}
            >
              ＋ 新規作成
            </button>
            {residentLimitReached && (
              <p className="resident-limit-note">M1ではResidentは1人までです</p>
            )}

            {creating && (
              <form className="resident-create-form" onSubmit={submitCreate}>
                <label htmlFor="resident-create-name">名前</label>
                <input
                  id="resident-create-name"
                  type="text"
                  autoComplete="off"
                  value={nameDraft}
                  disabled={pendingName !== null}
                  onChange={(event) => {
                    setNameDraft(event.currentTarget.value)
                    setCreateError(null)
                  }}
                  autoFocus
                />
                <label htmlFor="resident-create-brain">AI</label>
                <select
                  id="resident-create-brain"
                  value={brainDraft}
                  disabled={pendingName !== null}
                  onChange={(event) => {
                    setBrainDraft(event.currentTarget.value)
                    setCreateError(null)
                  }}
                >
                  <option value="">選択してください</option>
                  {providerStatuses.map((provider) => (
                    <option
                      key={provider.name}
                      value={provider.name}
                      disabled={!provider.available}
                    >
                      {provider.display_name}{provider.available ? '' : '（利用不可）'}
                    </option>
                  ))}
                </select>
                <div>
                  <button
                    type="submit"
                    disabled={pendingName !== null || !nameDraft.trim() || !brainDraft}
                  >
                    {pendingName !== null ? '作成中…' : '作成'}
                  </button>
                  <button
                    type="button"
                    disabled={pendingName !== null}
                    onClick={() => {
                      setCreating(false)
                      setNameDraft('')
                      setBrainDraft('')
                      setCreateError(null)
                    }}
                  >
                    キャンセル
                  </button>
                </div>
                {createError && <p role="alert">{createError}</p>}
              </form>
            )}

            <div className="resident-list">
              {residents.map((resident) => {
                const expanded = expandedResidentName === resident.name
                const voiceConfigured = resident.tts.speaker_uuid !== null && resident.tts.style_id !== null
                return (
                  <section className="resident-card" key={resident.name}>
                    <button
                      className="resident-card-title"
                      type="button"
                      aria-expanded={expanded}
                      onClick={() => setExpandedResidentName(expanded ? null : resident.name)}
                    >
                      <strong>{resident.name}</strong>
                      <span>{expanded ? '−' : '+'}</span>
                    </button>
                    {expanded && (
                      <div className="resident-card-details">
                        <dl>
                          <div><dt>AI</dt><dd>{brainLabel(resident.brain)}</dd></div>
                          <div><dt>VRM</dt><dd>{resident.avatar === null ? '未設定' : '設定済み'}</dd></div>
                          <div><dt>VOICE</dt><dd>{voiceConfigured ? '設定済み' : '未設定'}</dd></div>
                        </dl>
                        <button
                          type="button"
                          disabled={!connected || pendingBrain !== null}
                          onClick={() => {
                            if (brainEditName === resident.name) {
                              setBrainEditName(null)
                              setBrainEditDraft('')
                              setBrainError(null)
                              return
                            }
                            setBrainEditName(resident.name)
                            setBrainEditDraft(resident.brain ?? '')
                            setBrainError(null)
                          }}
                        >
                          {pendingBrain?.name === resident.name ? 'AI保存中…' : 'AI変更'}
                        </button>
                        {brainEditName === resident.name && (
                          <div className="brain-settings-panel">
                            <label htmlFor={`resident-brain-${resident.name}`}>AI</label>
                            <select
                              id={`resident-brain-${resident.name}`}
                              value={brainEditDraft}
                              disabled={pendingBrain !== null}
                              onChange={(event) => {
                                setBrainEditDraft(event.currentTarget.value)
                                setBrainError(null)
                              }}
                            >
                              <option value="">選択してください</option>
                              {providerStatuses.map((provider) => (
                                <option
                                  key={provider.name}
                                  value={provider.name}
                                  disabled={!provider.available}
                                >
                                  {provider.display_name}{provider.available ? '' : '（利用不可）'}
                                </option>
                              ))}
                            </select>
                            <div className="brain-settings-actions">
                              <button
                                type="button"
                                disabled={
                                  pendingBrain !== null
                                  || !brainEditDraft
                                  || brainEditDraft === resident.brain
                                }
                                onClick={() => {
                                  const provider = providerStatuses.find(
                                    (candidate) => candidate.name === brainEditDraft
                                  )
                                  if (!provider?.available) {
                                    setBrainError('選択したAIは現在利用できません')
                                    return
                                  }
                                  if (!onSetBrain(resident.name, brainEditDraft)) {
                                    setBrainError('AI変更をCoreへ送信できませんでした')
                                    return
                                  }
                                  setPendingBrain({ name: resident.name, provider: brainEditDraft })
                                  setBrainError(null)
                                }}
                              >
                                保存
                              </button>
                              <button
                                type="button"
                                disabled={pendingBrain !== null}
                                onClick={() => {
                                  setBrainEditName(null)
                                  setBrainEditDraft('')
                                  setBrainError(null)
                                }}
                              >
                                閉じる
                              </button>
                            </div>
                            {brainError && (
                              <p className="resident-setting-error" role="alert">{brainError}</p>
                            )}
                          </div>
                        )}
                        <button
                          type="button"
                          disabled={!connected || pendingAvatar !== null}
                          onClick={() => void pickAvatar(resident.name)}
                        >
                          {pendingAvatar?.name === resident.name
                            ? 'VRM保存中…'
                            : resident.avatar === null ? 'VRM読込' : 'VRM変更'}
                        </button>
                        {avatarError && expandedResidentName === resident.name && (
                          <p className="resident-setting-error" role="alert">{avatarError}</p>
                        )}
                        <button
                          type="button"
                          disabled={!connected}
                          onClick={() => setVoicePanelResidentName(
                            voicePanelResidentName === resident.name ? null : resident.name
                          )}
                        >
                          {voiceConfigured ? 'VOICE変更' : 'VOICE読込'}
                        </button>
                        {voicePanelResidentName === resident.name && (
                          <VoiceSettingsPanel
                            resident={resident}
                            onSave={onSetTts}
                            onPreviewAudio={onPreviewVoice}
                            onClose={() => setVoicePanelResidentName(null)}
                          />
                        )}
                        <button type="button" onClick={() => void openPersona(resident.name)}>
                          Promptを開く
                        </button>
                        {personaError && expandedResidentName === resident.name && (
                          <p className="resident-setting-error" role="alert">{personaError}</p>
                        )}
                        <button
                          className="resident-delete-button"
                          type="button"
                          disabled={!connected || responseActive}
                          onClick={() => {
                            setDeleteTargetName(resident.name)
                            setDeleteError(null)
                          }}
                        >
                          キャラクター削除
                        </button>
                      </div>
                    )}
                  </section>
                )
              })}
              {residents.length === 0 && <p className="resident-empty">Residentはいません</p>}
            </div>
          </div>
        )}
      </aside>
      {deleteTargetName && (
        <DeleteResidentDialog
          residentName={deleteTargetName}
          pending={pendingDeleteName === deleteTargetName}
          error={deleteError}
          onClose={() => {
            if (pendingDeleteName !== null) return
            setDeleteTargetName(null)
            setDeleteError(null)
          }}
          onConfirm={() => {
            if (!connected || pendingDeleteName !== null) return
            if (!onDeleteResident(deleteTargetName, 'Delete')) {
              setDeleteError('Resident削除をCoreへ送信できませんでした')
              return
            }
            setDeleteError(null)
            setPendingDeleteName(deleteTargetName)
          }}
        />
      )}
    </>
  )
}
