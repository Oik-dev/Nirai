import { useEffect, useMemo, useState } from 'react'
import type { VoicevoxSpeaker } from '../../../preload/api'
import type { ResidentPayload, ResidentTtsPayload } from '../protocol/types'
import { useAudioStore } from '../stores/audioStore'

const PREVIEW_TEXT = 'こんにちは。Niraiでこの声を使います。'

interface VoiceSettingsPanelProps {
  readonly resident: ResidentPayload
  readonly onSave: (name: string, tts: ResidentTtsPayload) => boolean
  readonly onPreviewAudio: (audio: Uint8Array) => Promise<void>
  readonly onClose: () => void
}

interface PendingSave {
  readonly speakerUuid: string
  readonly styleId: number
  readonly speed: number
  readonly pitch: number
  readonly intonation: number
}

function sameSavedVoice(resident: ResidentPayload, pending: PendingSave): boolean {
  const tts = resident.tts
  return tts.speaker_uuid === pending.speakerUuid
    && tts.style_id === pending.styleId
    && tts.speed === pending.speed
    && tts.pitch === pending.pitch
    && tts.intonation === pending.intonation
}

export function VoiceSettingsPanel({
  resident,
  onSave,
  onPreviewAudio,
  onClose
}: VoiceSettingsPanelProps): JSX.Element {
  const [speakers, setSpeakers] = useState<VoicevoxSpeaker[]>([])
  const [speakerUuid, setSpeakerUuid] = useState(resident.tts.speaker_uuid ?? '')
  const [styleId, setStyleId] = useState<number | null>(resident.tts.style_id)
  const [speed, setSpeed] = useState(resident.tts.speed)
  const [pitch, setPitch] = useState(resident.tts.pitch)
  const [intonation, setIntonation] = useState(resident.tts.intonation)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [previewing, setPreviewing] = useState(false)
  const [pendingSave, setPendingSave] = useState<PendingSave | null>(null)
  const [saveStatus, setSaveStatus] = useState('')

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    void window.nirai.voicevox.health().then(async (available) => {
      useAudioStore.setState({ voicevoxAvailable: available })
      if (!available) {
        throw new Error('VOICEVOXに接続できません')
      }
      const nextSpeakers = await window.nirai.voicevox.speakers()
      if (cancelled) return
      setSpeakers(nextSpeakers)
      const initialSpeaker = nextSpeakers.find((speaker) => speaker.speaker_uuid === resident.tts.speaker_uuid)
        ?? nextSpeakers[0]
      if (initialSpeaker) {
        setSpeakerUuid(initialSpeaker.speaker_uuid)
        const configuredStyle = initialSpeaker.styles.find((style) => style.id === resident.tts.style_id)
        setStyleId(configuredStyle?.id ?? initialSpeaker.styles[0]?.id ?? null)
      }
    }).catch((cause) => {
      if (!cancelled) {
        useAudioStore.setState({ voicevoxAvailable: false })
        setError(cause instanceof Error ? cause.message : 'VOICEVOX設定を読み込めませんでした')
      }
    }).finally(() => {
      if (!cancelled) setLoading(false)
    })
    return () => { cancelled = true }
  }, [resident.name])

  useEffect(() => {
    if (!pendingSave || !sameSavedVoice(resident, pendingSave)) return
    setPendingSave(null)
    setSaveStatus('保存済み')
  }, [pendingSave, resident])

  const selectedSpeaker = useMemo(
    () => speakers.find((speaker) => speaker.speaker_uuid === speakerUuid) ?? null,
    [speakerUuid, speakers]
  )

  const makeTts = (): ResidentTtsPayload | null => {
    if (!selectedSpeaker || styleId === null) return null
    if (![speed, pitch, intonation].every(Number.isFinite)) return null
    return {
      enabled: true,
      provider: 'voicevox',
      speaker_uuid: selectedSpeaker.speaker_uuid,
      style_id: styleId,
      speed,
      pitch,
      intonation
    }
  }

  const preview = async (): Promise<void> => {
    const tts = makeTts()
    if (!tts || tts.style_id === null) return
    setPreviewing(true)
    setError(null)
    try {
      const audio = await window.nirai.voicevox.synthesize({
        text: PREVIEW_TEXT,
        style_id: tts.style_id,
        speed: tts.speed,
        pitch: tts.pitch,
        intonation: tts.intonation
      })
      await onPreviewAudio(audio)
      useAudioStore.setState({ voicevoxAvailable: true })
    } catch (cause) {
      useAudioStore.setState({ voicevoxAvailable: false })
      setError(cause instanceof Error ? cause.message : 'VOICE試聴に失敗しました')
    } finally {
      setPreviewing(false)
    }
  }

  const save = (): void => {
    const tts = makeTts()
    if (!tts || tts.speaker_uuid === null || tts.style_id === null) return
    setError(null)
    setSaveStatus('')
    const pending = {
      speakerUuid: tts.speaker_uuid,
      styleId: tts.style_id,
      speed: tts.speed,
      pitch: tts.pitch,
      intonation: tts.intonation
    }
    if (sameSavedVoice(resident, pending)) {
      setSaveStatus('保存済み')
      return
    }
    if (!onSave(resident.name, tts)) {
      setError('VOICE設定をCoreへ送信できませんでした')
      return
    }
    setPendingSave(pending)
    setSaveStatus('保存待ち…')
  }

  return (
    <section className="voice-settings-panel" aria-label={`${resident.name}のVOICE設定`}>
      <header>
        <strong>VOICE</strong>
        <button type="button" onClick={onClose}>閉じる</button>
      </header>

      {loading ? <p>VOICEVOX確認中…</p> : error && speakers.length === 0 ? <p role="alert">{error}</p> : (
        <>
          <label>
            <span>Provider</span>
            <input value="VOICEVOX" readOnly />
          </label>
          <label>
            <span>Speaker</span>
            <select
              value={speakerUuid}
              onChange={(event) => {
                const nextUuid = event.currentTarget.value
                const nextSpeaker = speakers.find((speaker) => speaker.speaker_uuid === nextUuid)
                setSpeakerUuid(nextUuid)
                setStyleId(nextSpeaker?.styles[0]?.id ?? null)
                setSaveStatus('')
              }}
            >
              {speakers.map((speaker) => (
                <option key={speaker.speaker_uuid} value={speaker.speaker_uuid}>{speaker.name}</option>
              ))}
            </select>
          </label>
          <label>
            <span>Style</span>
            <select
              value={styleId ?? ''}
              onChange={(event) => {
                setStyleId(Number(event.currentTarget.value))
                setSaveStatus('')
              }}
            >
              {(selectedSpeaker?.styles ?? []).map((style) => (
                <option key={style.id} value={style.id}>{style.name}</option>
              ))}
            </select>
          </label>
          <label>
            <span>話速</span>
            <input type="number" step="0.05" value={speed} onChange={(event) => { setSpeed(Number(event.currentTarget.value)); setSaveStatus('') }} />
          </label>
          <label>
            <span>音高</span>
            <input type="number" step="0.01" value={pitch} onChange={(event) => { setPitch(Number(event.currentTarget.value)); setSaveStatus('') }} />
          </label>
          <label>
            <span>抑揚</span>
            <input type="number" step="0.05" value={intonation} onChange={(event) => { setIntonation(Number(event.currentTarget.value)); setSaveStatus('') }} />
          </label>
          <div className="voice-settings-actions">
            <button type="button" disabled={previewing || styleId === null} onClick={() => void preview()}>
              {previewing ? '試聴中…' : '試聴'}
            </button>
            <button type="button" disabled={pendingSave !== null || styleId === null} onClick={save}>
              保存
            </button>
          </div>
          {saveStatus && <p className="voice-settings-status" aria-live="polite">{saveStatus}</p>}
          {error && <p className="resident-setting-error" role="alert">{error}</p>}
        </>
      )}
    </section>
  )
}
