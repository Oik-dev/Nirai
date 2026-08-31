import { useEffect, useRef, useState } from 'react'
import { ChatBar } from './ui/ChatBar'
import { ChatHistory } from './ui/ChatHistory'
import { HoloGate0Surface } from './ui/HoloGate0Surface'
import { ResidentSidebar } from './ui/ResidentSidebar'
import { ResidentSpeechBubble } from './ui/ResidentSpeechBubble'
import { SessionSidebar } from './ui/SessionSidebar'
import { VolumeControl } from './ui/VolumeControl'
import { AudioService } from './audio/AudioService'
import { SpeechQueue } from './audio/SpeechQueue'
import { TtsService } from './audio/TtsService'
import { useAudioStore } from './stores/audioStore'
import { useConnectionStore } from './stores/connectionStore'
import { useResidentStore } from './stores/residentStore'
import { useSessionStore, type ChatEntry, type ChatSessionSummary } from './stores/sessionStore'
import { useUiStore } from './stores/uiStore'
import {
  isActionMessage,
  isBrainProviderListMessage,
  isChatAppendMessage,
  isChatSessionListMessage,
  isHelloAckMessage,
  isHistoryResponseMessage,
  isNoticeMessage,
  isResidentRosterUpdatedMessage,
  isResidentSettingsUpdatedMessage,
  isResponseStateMessage
} from './protocol/parser'
import type { BrainProviderPayload, NoticePayload, ProtocolMessage, ResidentPayload } from './protocol/types'
import { CoreConnection } from './runtime/CoreConnection'
import { SceneRuntime } from './runtime/SceneRuntime'
import type {
  AnimationClipName,
  M0LocationName,
  MotionPoseOption,
  PoseAdjustment,
  PoseAdjustScope
} from './runtime/SceneRuntime'
import type { AnimationName } from './world/vrm/AnimationController'
import type { EmotionName } from './world/vrm/ExpressionController'
import type {
  EnvironmentEffectName,
  SeabedMaterialDebugLayer,
  SeabedSurfaceDebugLayer
} from './world/environment/EnvironmentController'
import {
  DEFAULT_VISUAL_TUNING,
  formatVisualTuning,
  sanitizeVisualTuning,
  type VisualTuning
} from './runtime/VisualTuning'
import {
  DEFAULT_MOTION_TUNING,
  formatMotionTuning,
  sanitizeMotionTuning,
  type MotionTuning
} from './runtime/MotionTuning'

const LAST_AVATAR_STORAGE_KEY = 'nirai:last-avatar'
// Persisted Visual Speed Lab values. Renaming this key resets operator tuning.
const VISUAL_TUNING_STORAGE_KEY = 'nirai:temporary-visual-tuning'

// Operator Debug isolator lists. Product rendering keeps every layer enabled until toggled.
const HORIZON_DEBUG_EFFECTS = [
  ['overheadGlow', 'Backdrop'],
  ['seabed', 'Seabed'],
  ['caustics', 'Caustics'],
  ['waterSurface', 'WaterSurface']
] as const satisfies readonly (readonly [EnvironmentEffectName, string])[]

type HorizonDebugEffectName = typeof HORIZON_DEBUG_EFFECTS[number][0]

const SEABED_MATERIAL_DEBUG_LAYERS = [
  ['colorMap', 'Color Map'],
  ['ao', 'AO'],
  ['macroVariation', 'Macro Variation']
] as const satisfies readonly (readonly [SeabedMaterialDebugLayer, string])[]

const SEABED_SURFACE_DEBUG_LAYERS = [
  ['shadows', 'Shadows'],
  ['geometry', 'Geometry']
] as const satisfies readonly (readonly [SeabedSurfaceDebugLayer, string])[]

interface TuningSliderProps {
  readonly id: string
  readonly label: string
  readonly value: number
  readonly hint: string
  readonly minimum?: number
  readonly maximum?: number
  readonly step?: number
  readonly onChange: (value: number) => void
}

function TuningSlider({
  id,
  label,
  value,
  hint,
  minimum = 0,
  maximum = 1000,
  step = 5,
  onChange
}: TuningSliderProps): JSX.Element {
  return (
    <label className="tuning-slider" htmlFor={id}>
      <span>
        <strong>{label}</strong>
        <output htmlFor={id}>{Math.round(value)}%</output>
      </span>
      <input
        id={id}
        type="range"
        min={minimum}
        max={maximum}
        step={step}
        value={Math.round(value)}
        aria-valuetext={`${Math.round(value)}%`}
        onChange={(event) => onChange(Number(event.currentTarget.value))}
      />
      <small>{hint}</small>
    </label>
  )
}

function persistVisualTuning(value: VisualTuning): void {
  localStorage.setItem(VISUAL_TUNING_STORAGE_KEY, JSON.stringify(value))
}

async function writeClipboardText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    // Electron/dev contexts can deny the Clipboard API. Fall through to the
    // DOM copy path so Debug value buttons still work without permissions.
  }

  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.setAttribute('readonly', '')
  textarea.style.position = 'fixed'
  textarea.style.opacity = '0'
  textarea.style.pointerEvents = 'none'
  document.body.appendChild(textarea)
  textarea.select()
  textarea.setSelectionRange(0, text.length)
  const copied = document.execCommand('copy')
  textarea.remove()
  return copied
}

export function App(): JSX.Element {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const runtimeRef = useRef<SceneRuntime | null>(null)
  const coreConnectionRef = useRef<CoreConnection | null>(null)
  const avatarLoadRequestRef = useRef(0)
  const residentRosterSyncGenerationRef = useRef(0)
  const appliedResidentAvatarsRef = useRef(new Map<string, string>())
  const audioServiceRef = useRef<AudioService | null>(null)
  const speechQueueRef = useRef<SpeechQueue | null>(null)
  const ttsServiceRef = useRef<TtsService | null>(null)
  const speechSynthesisTailRef = useRef<Promise<void>>(Promise.resolve())
  const speechResidentGenerationRef = useRef(new Map<string, number>())
  const noticeSequenceRef = useRef(0)
  const bubbleSequenceRef = useRef(0)
  const [notice, setNotice] = useState<({ readonly key: number } & NoticePayload) | null>(null)
  const [speechBubble, setSpeechBubble] = useState<{
    readonly key: number
    readonly residentName: string
    readonly text: string
  } | null>(null)
  const [avatarStatus, setAvatarStatus] = useState('Avatar未選択')
  const [avatarLoaded, setAvatarLoaded] = useState(false)
  const [animationStatus, setAnimationStatus] = useState<AnimationName>('stand')
  const [emotionStatus, setEmotionStatus] = useState<EmotionName>('neutral')
  const [availableEmotions, setAvailableEmotions] = useState<readonly EmotionName[]>(['neutral'])
  const [visualTuning, setVisualTuningState] = useState<VisualTuning>(() =>
    DEFAULT_VISUAL_TUNING
  )
  const [motionTuning, setMotionTuningState] = useState<MotionTuning>(() =>
    DEFAULT_MOTION_TUNING
  )
  const [motionCopyStatus, setMotionCopyStatus] = useState('')
  const [visualTuningPanelVisible, setVisualTuningPanelVisible] = useState(false)
  const [tuningCopyStatus, setTuningCopyStatus] = useState('')
  const [horizonDebugEffects, setHorizonDebugEffects] = useState<
    Record<HorizonDebugEffectName, boolean>
  >({
    overheadGlow: true,
    seabed: true,
    caustics: true,
    waterSurface: true
  })
  const [seabedMaterialDebugLayers, setSeabedMaterialDebugLayers] = useState<
    Record<SeabedMaterialDebugLayer, boolean>
  >({
    colorMap: true,
    ao: true,
    macroVariation: true
  })
  const [seabedSurfaceDebugLayers, setSeabedSurfaceDebugLayers] = useState<
    Record<SeabedSurfaceDebugLayer, boolean>
  >({
    shadows: true,
    geometry: true
  })
  const [poseMotionOptions, setPoseMotionOptions] = useState<readonly MotionPoseOption[]>([])
  const [poseAdjustClip, setPoseAdjustClip] = useState<AnimationClipName | null>(null)
  const [poseAdjustScope, setPoseAdjustScope] = useState<PoseAdjustScope>('root')
  const [poseAdjustment, setPoseAdjustment] = useState<PoseAdjustment | null>(null)
  const [poseCopyStatus, setPoseCopyStatus] = useState('')
  const [runtimeReady, setRuntimeReady] = useState(false)
  const [environmentStartupSettled, setEnvironmentStartupSettled] = useState(false)
  const [residentRosterHydrated, setResidentRosterHydrated] = useState(false)
  const [avatarStartupSettled, setAvatarStartupSettled] = useState(false)
  const [startupReady, setStartupReady] = useState(false)
  const [focusedResidentName, setFocusedResidentName] = useState<string | null>(null)
  const [holoGate0Open, setHoloGate0Open] = useState(false)
  const [conversationDebugStatus, setConversationDebugStatus] = useState('')
  const residents = useResidentStore((state) => state.residents)
  const volume = useAudioStore((state) => state.volume)
  const chatActive = useUiStore((state) => state.chatActive)

  if (!audioServiceRef.current) {
    audioServiceRef.current = new AudioService()
  }
  if (!ttsServiceRef.current) {
    ttsServiceRef.current = new TtsService()
  }
  if (!speechQueueRef.current) {
    speechQueueRef.current = new SpeechQueue(
      audioServiceRef.current,
      (residentName, requestId) => useAudioStore.setState({
        speakingResidentName: residentName,
        activeSpeechRequestId: requestId
      }),
      (residentName, analyser) => runtimeRef.current?.setSpeechAnalyser(residentName, analyser)
    )
  }

  const enqueueResidentSpeech = (entry: ChatEntry): void => {
    if (!['resident_say', 'resident_whisper', 'resident_chat'].includes(entry.kind)) return
    if (entry.kind !== 'resident_chat' && !entry.request_id) return
    const currentAudio = useAudioStore.getState()
    if (currentAudio.volume === 0) return
    const resident = useResidentStore.getState().residents.find((candidate) => candidate.name === entry.from)
    if (!resident || !resident.tts.enabled || resident.tts.speaker_uuid === null || resident.tts.style_id === null) return
    const queue = speechQueueRef.current
    const tts = ttsServiceRef.current
    if (!queue || !tts) return
    const generation = queue.generationToken
    const residentGeneration = speechResidentGenerationRef.current.get(entry.from) ?? 0

    // Keep synthesis in the same global order as text replies. SpeechQueue
    // already serializes playback, but parallel VOICEVOX calls could otherwise
    // finish out of order and enqueue a later Resident before an earlier one.
    speechSynthesisTailRef.current = speechSynthesisTailRef.current.then(async () => {
      if (
        generation !== queue.generationToken
        || residentGeneration !== (speechResidentGenerationRef.current.get(entry.from) ?? 0)
        || useAudioStore.getState().volume === 0
      ) return
      try {
        const audio = await tts.synthesize(entry.text, resident.tts)
        if (
          !audio
          || generation !== queue.generationToken
          || residentGeneration !== (speechResidentGenerationRef.current.get(entry.from) ?? 0)
          || useAudioStore.getState().volume === 0
        ) return
        useAudioStore.setState({ voicevoxAvailable: true })
        queue.enqueue({
          requestId: entry.request_id ?? `resident-chat:${entry.ts}:${entry.from}`,
          residentName: entry.from,
          text: entry.text,
          audio
        }, generation)
      } catch {
        useAudioStore.setState({ voicevoxAvailable: false })
      }
    })
  }

  useEffect(() => {
    const handleProtocolMessage = (message: ProtocolMessage): void => {
      if (isHelloAckMessage(message)) {
        useSessionStore.getState().setSessionList([], message.payload.active_session)
        useResidentStore.getState().setResidents(message.payload.residents)
        setResidentRosterHydrated(true)
        useAudioStore.getState().setVolume(message.payload.settings.audio_volume)
        connection.send('brain_provider_list_request', {})
        connection.send('chat_session_list_request', {})
        if (message.payload.active_session) {
          connection.send('history_request', {
            session_id: message.payload.active_session,
            limit: 50
          })
        }
        return
      }

      if (isBrainProviderListMessage(message)) {
        const payload = message.payload as { providers: readonly BrainProviderPayload[] }
        useResidentStore.getState().setProviderStatuses(payload.providers)
        return
      }

      if (isChatSessionListMessage(message)) {
        const payload = message.payload as {
          sessions: readonly ChatSessionSummary[]
          active_session: string | null
        }
        useSessionStore.getState().setSessionList(payload.sessions, payload.active_session)
        return
      }

      if (isHistoryResponseMessage(message)) {
        const payload = message.payload as {
          session_id: string
          entries: readonly ChatEntry[]
          next_before: string | null
        }
        useSessionStore.getState().setHistory(
          payload.session_id,
          payload.entries,
          payload.next_before
        )
        return
      }

      if (isActionMessage(message)) {
        const actionId = message.id
        const { name, command, args } = message.payload
        const finish = (ok: boolean, reason?: string): void => {
          connection.send('action_done', {
            name,
            ok,
            ...(reason ? { reason } : {})
          }, actionId)
        }
        const runtime = runtimeRef.current
        if (!runtime) {
          finish(false, 'World runtime is not ready')
          return
        }
        if (command === 'approach') {
          const target = args.target
          if (typeof target !== 'string' || !target) {
            finish(false, 'approach target is required')
            return
          }
          const started = runtime.approachResident(name, target, () => finish(true))
          if (!started) finish(false, `approach failed: ${name} -> ${target}`)
          return
        }
        if (command === 'gather') {
          const participants = args.participants
          if (
            !Array.isArray(participants)
            || participants.length < 2
            || participants.length > 10
            || participants.some((participant) => typeof participant !== 'string' || !participant)
            || new Set(participants).size !== participants.length
          ) {
            finish(false, 'gather participants must contain 2-10 unique Resident names')
            return
          }
          const started = runtime.gatherResidents(participants as string[], () => finish(true))
          if (!started) finish(false, 'gather failed')
          return
        }
        if (command === 'face') {
          const target = args.target
          if (typeof target !== 'string' || !target) {
            finish(false, 'face target is required')
            return
          }
          const ok = target === 'master'
            ? runtime.faceResidentToMaster(name)
            : runtime.faceResidentToResident(name, target)
          finish(ok, ok ? undefined : `face failed: ${name} -> ${target}`)
          return
        }
        if (command === 'stand') {
          const ok = runtime.standResident(name)
          finish(ok, ok ? undefined : `stand failed: ${name}`)
          return
        }
        finish(false, `unsupported action command: ${command}`)
        return
      }

      if (isChatAppendMessage(message)) {
        const payload = message.payload as { entry: ChatEntry }
        useSessionStore.getState().appendEntry(payload.entry)
        if (
          payload.entry.kind === 'resident_say'
          || payload.entry.kind === 'resident_whisper'
          || payload.entry.kind === 'resident_chat'
        ) {
          if (payload.entry.kind !== 'resident_chat') {
            runtimeRef.current?.faceResidentToMaster(payload.entry.from)
          }
          if (!useUiStore.getState().chatActive) {
            setSpeechBubble({
              key: ++bubbleSequenceRef.current,
              residentName: payload.entry.from,
              text: payload.entry.text
            })
          }
        }
        enqueueResidentSpeech(payload.entry)
        return
      }

      if (isNoticeMessage(message)) {
        setNotice({
          key: ++noticeSequenceRef.current,
          ...message.payload
        })
        return
      }

      if (isResidentRosterUpdatedMessage(message)) {
        const payload = message.payload as { residents: readonly ResidentPayload[] }
        useResidentStore.getState().setResidents(payload.residents)
        return
      }

      if (isResidentSettingsUpdatedMessage(message)) {
        const payload = message.payload as {
          resident: ResidentPayload | null
          deleted_name?: string
        }
        if (payload.resident) {
          useResidentStore.getState().upsertResident(payload.resident)
        } else if (payload.deleted_name) {
          residentRosterSyncGenerationRef.current += 1
          useResidentStore.getState().removeResident(payload.deleted_name)
          speechResidentGenerationRef.current.set(
            payload.deleted_name,
            (speechResidentGenerationRef.current.get(payload.deleted_name) ?? 0) + 1
          )
          speechQueueRef.current?.cancelResident(payload.deleted_name)
          const runtime = runtimeRef.current
          const removedPrimary = runtime?.getPrimaryResidentName() === payload.deleted_name
          runtime?.unloadResident(payload.deleted_name)
          appliedResidentAvatarsRef.current.delete(payload.deleted_name)
          if (removedPrimary) {
            const nextPrimary = runtime?.getPrimaryResidentName() ?? null
            const nextAvatar = nextPrimary
              ? appliedResidentAvatarsRef.current.get(nextPrimary) ?? null
              : null
            setAvatarStatus(nextAvatar ?? 'Avatar未選択')
            setAvatarLoaded(nextAvatar !== null)
          }
        }
        return
      }

      if (isResponseStateMessage(message)) {
        const current = useConnectionStore.getState()
        if (message.payload.active && message.payload.request_id) {
          useConnectionStore.setState({ activeRequestId: message.payload.request_id })
        } else if (!message.payload.active
          && (!message.payload.request_id || current.activeRequestId === message.payload.request_id)) {
          useConnectionStore.setState({ activeRequestId: null })
        }
      }
    }

    const connection = new CoreConnection({ onProtocolMessage: handleProtocolMessage })
    coreConnectionRef.current = connection
    connection.start()
    return () => {
      coreConnectionRef.current = null
      connection.stop()
    }
  }, [])

  useEffect(() => {
    return () => {
      speechQueueRef.current?.stopAll()
      audioServiceRef.current?.dispose()
    }
  }, [])

  useEffect(() => {
    audioServiceRef.current?.setVolume(volume / 100)
    if (volume === 0) {
      speechQueueRef.current?.stopAll()
    }
  }, [volume])

  useEffect(() => {
    if (!notice) return
    const noticeKey = notice.key
    const timeout = window.setTimeout(() => {
      setNotice((current) => current?.key === noticeKey ? null : current)
    }, 5000)
    return () => window.clearTimeout(timeout)
  }, [notice])

  useEffect(() => {
    if (chatActive) {
      setSpeechBubble(null)
    }
  }, [chatActive])

  useEffect(() => {
    if (!speechBubble) return
    const bubbleKey = speechBubble.key
    const durationMs = Math.min(12000, Math.max(4500, speechBubble.text.length * 95))
    const timeout = window.setTimeout(() => {
      setSpeechBubble((current) => current?.key === bubbleKey ? null : current)
    }, durationMs)
    return () => window.clearTimeout(timeout)
  }, [speechBubble])

  useEffect(() => {
    const canvas = canvasRef.current

    if (!canvas) {
      return
    }

    const runtime = new SceneRuntime()
    let cancelled = false
    runtimeRef.current = runtime
    runtime.setFocusChangeListener(setFocusedResidentName)
    runtime.setVisualTuning(visualTuning)
    runtime.setMotionTuning(motionTuning)
    runtime.start(canvas)
    setRuntimeReady(true)
    void runtime.whenInitialSceneReady().then(() => {
      if (!cancelled) setEnvironmentStartupSettled(true)
    })
    let removeAcceptanceBridge: () => void = () => undefined
    if (import.meta.env.DEV) {
      void import('./runtime/M0AcceptanceBridge').then(({ installM0AcceptanceBridge }) => {
        if (cancelled) {
          return
        }

        removeAcceptanceBridge = installM0AcceptanceBridge(runtime, {
          onVisualTuningChange: setVisualTuningState,
          onVisualTuningPanelVisibilityChange: setVisualTuningPanelVisible
        })
      })
    }

    return () => {
      cancelled = true
      removeAcceptanceBridge()
      runtime.setFocusChangeListener(null)
      runtime.dispose()
      runtimeRef.current = null
    }
  }, [])

  useEffect(() => {
    if (!runtimeReady || !residentRosterHydrated) return
    const runtime = runtimeRef.current
    if (!runtime) return

    const syncGeneration = ++residentRosterSyncGenerationRef.current
    runtime.setResidentRosterOrder(residents.map((resident) => resident.name))
    const targetNames = new Set(residents.map((resident) => resident.name))
    setAvatarStartupSettled(false)

    for (const appliedName of [...appliedResidentAvatarsRef.current.keys()]) {
      if (targetNames.has(appliedName)) continue
      runtime.unloadResident(appliedName)
      appliedResidentAvatarsRef.current.delete(appliedName)
    }

    const syncTasks = residents.map(async (resident) => {
      const nextAvatar = resident.avatar?.replace(/\\/g, '/') ?? null
      if (nextAvatar === null) {
        runtime.unloadResident(resident.name)
        appliedResidentAvatarsRef.current.delete(resident.name)
        return
      }
      if (
        appliedResidentAvatarsRef.current.get(resident.name) === nextAvatar
        && runtime.residents.get(resident.name)?.vrm
      ) return

      await runtime.loadResidentAvatar(resident.name, nextAvatar)
      if (
        syncGeneration !== residentRosterSyncGenerationRef.current
        || runtime !== runtimeRef.current
      ) return
      appliedResidentAvatarsRef.current.set(resident.name, nextAvatar)
    })

    void Promise.allSettled(syncTasks).then((results) => {
      if (
        syncGeneration !== residentRosterSyncGenerationRef.current
        || runtime !== runtimeRef.current
      ) return

      const primaryName = runtime.getPrimaryResidentName()
      const primaryResident = residents.find((resident) => resident.name === primaryName) ?? null
      const primaryAvatar = primaryResident?.avatar?.replace(/\\/g, '/') ?? null
      const primaryLoaded = primaryAvatar !== null && Boolean(runtime.residents.get(primaryName)?.vrm)
      const rejected = results.find((result) => result.status === 'rejected')

      setAvatarStatus(
        rejected?.status === 'rejected'
          ? rejected.reason instanceof Error ? rejected.reason.message : 'Avatarを読み込めませんでした'
          : primaryAvatar ?? 'Avatar未選択'
      )
      setAvatarLoaded(primaryLoaded)
      if (primaryLoaded) {
        localStorage.setItem(LAST_AVATAR_STORAGE_KEY, primaryAvatar!)
        setAnimationStatus('stand')
        setEmotionStatus('neutral')
        setAvailableEmotions(runtime.getAvailableEmotions())
        setPoseMotionOptions(runtime.getPoseAdjustMotionOptions())
      }
      setAvatarStartupSettled(true)
    })

    return () => {
      residentRosterSyncGenerationRef.current += 1
    }
  }, [residents, residentRosterHydrated, runtimeReady])

  useEffect(() => {
    if (
      startupReady
      || !runtimeReady
      || !environmentStartupSettled
      || !residentRosterHydrated
      || !avatarStartupSettled
    ) {
      return
    }

    let secondFrameId = 0
    const firstFrameId = window.requestAnimationFrame(() => {
      secondFrameId = window.requestAnimationFrame(() => setStartupReady(true))
    })
    return () => {
      window.cancelAnimationFrame(firstFrameId)
      if (secondFrameId) window.cancelAnimationFrame(secondFrameId)
    }
  }, [
    avatarStartupSettled,
    environmentStartupSettled,
    residentRosterHydrated,
    runtimeReady,
    startupReady
  ])

  useEffect(() => {
    if (visualTuningPanelVisible) {
      return
    }

    runtimeRef.current?.stopPoseAdjustment()
    setPoseAdjustClip(null)
    setPoseAdjustment(null)
    setPoseCopyStatus('')
  }, [visualTuningPanelVisible])

  useEffect(() => {
    if (!runtimeReady) return
    const runtime = runtimeRef.current
    if (!runtime) return

    runtime.stopPoseAdjustment()
    setPoseAdjustClip(null)
    setPoseAdjustment(null)
    setPoseCopyStatus('')
    setMotionTuningState(runtime.getMotionTuning())
    setAvailableEmotions(runtime.getAvailableEmotions())
    setPoseMotionOptions(runtime.getPoseAdjustMotionOptions())
    setAnimationStatus('stand')
    setEmotionStatus('neutral')

    const targetName = runtime.getDebugTargetResidentName()
    const targetResident = residents.find((resident) => resident.name === targetName) ?? null
    const targetAvatar = targetResident?.avatar?.replace(/\\/g, '/') ?? null
    setAvatarStatus(targetAvatar ?? 'Avatar未選択')
    setAvatarLoaded(Boolean(targetAvatar && runtime.residents.get(targetName)?.vrm))
  }, [focusedResidentName, residents, runtimeReady])

  const pickAvatar = async (): Promise<void> => {
    let requestId: number | null = null
    try {
      const relativePath = await window.nirai.avatar.pick()
      const runtime = runtimeRef.current

      if (!relativePath || !runtime) {
        return
      }

      requestId = ++avatarLoadRequestRef.current
      setAvatarStatus('Avatar読込中')
      await runtime.loadAvatar(relativePath)
      if (requestId !== avatarLoadRequestRef.current || runtime !== runtimeRef.current) {
        return
      }

      localStorage.setItem(LAST_AVATAR_STORAGE_KEY, relativePath)
      setAvatarStatus(relativePath)
      setAvatarLoaded(true)
      setAnimationStatus('stand')
      setEmotionStatus('neutral')
      setAvailableEmotions(runtime.getAvailableEmotions())
      setPoseMotionOptions(runtime.getPoseAdjustMotionOptions())
    } catch (error) {
      if (requestId !== null && requestId !== avatarLoadRequestRef.current) {
        return
      }
      const message = error instanceof Error ? error.message : 'Avatarを読み込めませんでした'
      setAvatarStatus(message)
    }
  }

  const playAnimation = (name: AnimationName): void => {
    if (runtimeRef.current?.playAnimation(name)) {
      setAnimationStatus(name)
    }
  }

  const setEmotion = (name: EmotionName): void => {
    if (runtimeRef.current?.setEmotion(name)) {
      setEmotionStatus(name)
    }
  }

  const moveTo = (location: M0LocationName): void => {
    runtimeRef.current?.moveResidentTo(location, () => setAnimationStatus('stand'))
  }

  const runTwoResidentConversationDebug = (): void => {
    const runtime = runtimeRef.current
    if (!runtime) return
    const names = residents
      .map((resident) => resident.name)
      .filter((name) => Boolean(runtime.residents.get(name)?.vrm))
      .slice(0, 2)
    if (names.length < 2) {
      setConversationDebugStatus('表示中Avatarが2人必要です')
      return
    }

    const [source, target] = names
    setConversationDebugStatus(`${source} → ${target}: approach中`)
    const started = runtime.approachResident(source, target, () => {
      runtime.faceResidentToResident(source, target)
      runtime.faceResidentToResident(target, source)
      setConversationDebugStatus(`${source} / ${target}: 向き合い中。4秒後にstand復帰`)
      window.setTimeout(() => {
        runtime.standResident(source)
        runtime.standResident(target)
        setConversationDebugStatus('2人会話演出完了。Separation復帰を確認してください')
      }, 4000)
    })
    if (!started) {
      setConversationDebugStatus('2人会話演出を開始できませんでした')
    }
  }

  const runThreeResidentConversationDebug = (): void => {
    const runtime = runtimeRef.current
    if (!runtime) return
    const names = residents
      .map((resident) => resident.name)
      .filter((name) => Boolean(runtime.residents.get(name)?.vrm))
      .slice(0, 3)
    if (names.length < 3) {
      setConversationDebugStatus('表示中Avatarが3人必要です')
      return
    }

    setConversationDebugStatus(`${names.join(' / ')}: gather中`)
    const started = runtime.gatherResidents(names, () => {
      setConversationDebugStatus('3人Group Formation確認中。4秒後にstand復帰')
      window.setTimeout(() => {
        for (const name of names) runtime.standResident(name)
        setConversationDebugStatus('3人会話演出完了。Separation復帰を確認してください')
      }, 4000)
    })
    if (!started) {
      setConversationDebugStatus('3人会話演出を開始できませんでした')
    }
  }

  const updateVisualTuning = (
    name: keyof VisualTuning,
    percent: number
  ): void => {
    setVisualTuningState((current) => {
      const next = sanitizeVisualTuning({
        ...current,
        [name]: percent / 100
      })
      persistVisualTuning(next)
      runtimeRef.current?.setVisualTuning(next)
      setTuningCopyStatus('')
      return next
    })
  }

  const resetVisualTuning = (): void => {
    const next = { ...DEFAULT_VISUAL_TUNING }
    persistVisualTuning(next)
    runtimeRef.current?.setVisualTuning(next)
    setVisualTuningState(next)
    setTuningCopyStatus('初期値へ戻しました')
  }

  const updateMotionTuning = (
    name: keyof MotionTuning,
    percent: number
  ): void => {
    setMotionTuningState((current) => {
      const next = sanitizeMotionTuning({
        ...current,
        [name]: percent / 100
      })
      runtimeRef.current?.setMotionTuning(next)
      setMotionCopyStatus('')
      return next
    })
  }

  const resetMotionTuning = (): void => {
    const next = { ...DEFAULT_MOTION_TUNING }
    runtimeRef.current?.setMotionTuning(next)
    setMotionTuningState(next)
    setMotionCopyStatus('初期値へ戻しました')
  }

  const copyMotionTuning = async (): Promise<void> => {
    const text = formatMotionTuning(motionTuning)
    const copied = await writeClipboardText(text)
    setMotionCopyStatus(copied ? 'コピーしました' : text)
  }

  const toggleHorizonDebugEffect = (name: HorizonDebugEffectName): void => {
    setHorizonDebugEffects((current) => {
      const enabled = !current[name]
      runtimeRef.current?.setEnvironmentEffect(name, enabled)
      return {
        ...current,
        [name]: enabled
      }
    })
  }

  const restoreHorizonDebugEffects = (): void => {
    for (const [name] of HORIZON_DEBUG_EFFECTS) {
      runtimeRef.current?.setEnvironmentEffect(name, true)
    }
    setHorizonDebugEffects({
      overheadGlow: true,
      seabed: true,
      caustics: true,
      waterSurface: true
    })
  }

  const toggleSeabedMaterialDebugLayer = (name: SeabedMaterialDebugLayer): void => {
    setSeabedMaterialDebugLayers((current) => {
      const enabled = !current[name]
      runtimeRef.current?.setSeabedMaterialLayerEnabled(name, enabled)
      return {
        ...current,
        [name]: enabled
      }
    })
  }

  const restoreSeabedMaterialDebugLayers = (): void => {
    for (const [name] of SEABED_MATERIAL_DEBUG_LAYERS) {
      runtimeRef.current?.setSeabedMaterialLayerEnabled(name, true)
    }
    setSeabedMaterialDebugLayers({
      colorMap: true,
      ao: true,
      macroVariation: true
    })
  }

  const toggleSeabedSurfaceDebugLayer = (name: SeabedSurfaceDebugLayer): void => {
    setSeabedSurfaceDebugLayers((current) => {
      const enabled = !current[name]
      runtimeRef.current?.setSeabedSurfaceLayerEnabled(name, enabled)
      return {
        ...current,
        [name]: enabled
      }
    })
  }

  const restoreSeabedSurfaceDebugLayers = (): void => {
    for (const [name] of SEABED_SURFACE_DEBUG_LAYERS) {
      runtimeRef.current?.setSeabedSurfaceLayerEnabled(name, true)
    }
    setSeabedSurfaceDebugLayers({
      shadows: true,
      geometry: true
    })
  }

  const copyVisualTuning = async (): Promise<void> => {
    const text = formatVisualTuning(visualTuning)
    const copied = await writeClipboardText(text)
    setTuningCopyStatus(copied ? 'コピーしました' : 'コピーできませんでした。表示値をそのまま伝えてください')
  }

  const beginPoseAdjustment = (
    clip: AnimationClipName,
    scope: PoseAdjustScope
  ): void => {
    const runtime = runtimeRef.current
    if (!runtime) {
      return
    }

    const next = runtime.beginPoseAdjustment(clip, scope, (value) => {
      setPoseAdjustment(value)
      setPoseCopyStatus('')
    })
    if (!next) {
      return
    }

    setPoseAdjustClip(clip)
    setPoseAdjustScope(scope)
    setPoseAdjustment(next)
    setPoseCopyStatus('')
  }

  const selectPoseMotion = (clip: AnimationClipName): void => {
    const runtime = runtimeRef.current
    if (!runtime) {
      return
    }

    const head = runtime.getPoseAdjustment(clip, 'head')
    const preferredScope: PoseAdjustScope = head
      && (Math.abs(head.pitchDeg) > 0.0001 || Math.abs(head.yawDeg) > 0.0001)
      ? 'head'
      : 'root'
    beginPoseAdjustment(clip, preferredScope)
  }

  const stopPoseAdjustment = (): void => {
    runtimeRef.current?.stopPoseAdjustment()
    setPoseAdjustClip(null)
    setPoseAdjustment(null)
    setPoseCopyStatus('')
  }

  const copyPoseAdjustment = async (): Promise<void> => {
    if (!poseAdjustClip || !poseAdjustment) {
      return
    }

    const label = poseMotionOptions.find((option) => option.clip === poseAdjustClip)?.label ?? poseAdjustClip
    const text = [
      `${label} clip=${poseAdjustClip}`,
      `scope=${poseAdjustScope}`,
      `pitchDeg=${poseAdjustment.pitchDeg.toFixed(2)}`,
      `yawDeg=${poseAdjustment.yawDeg.toFixed(2)}`
    ].join(' ')

    const copied = await writeClipboardText(text)
    setPoseCopyStatus(copied ? 'コピーしました' : text)
  }

  const closeTransientUiFromWorld = (event: React.PointerEvent<HTMLElement>): void => {
    const target = event.target
    if (!(target instanceof Element)) return
    if (target.closest('.sidebar-toggle, .side-panel, .chat-dock, .volume-control, .holo-gate0-surface')) return

    const ui = useUiStore.getState()
    ui.closeSidebars()
    ui.setChatActive(false)
    ui.setHistoryOpaque(false)
  }

  return (
    <main className="app-shell" onPointerDown={closeTransientUiFromWorld}>
      <canvas ref={canvasRef} aria-label="Nirai 3D World" />
      {!startupReady && (
        <div className="startup-loading-screen" role="status" aria-live="polite">
          <div className="startup-loading-content">
            <strong>NIRAI</strong>
            <span>海中世界を準備しています</span>
            <i aria-hidden="true" />
          </div>
        </div>
      )}
      <h1>Nirai</h1>
      {speechBubble && !chatActive && (
        <ResidentSpeechBubble
          key={speechBubble.key}
          runtime={runtimeReady ? runtimeRef.current : null}
          residentName={speechBubble.residentName}
          text={speechBubble.text}
        />
      )}
      {notice && (
        <div className={`notice-toast notice-toast-${notice.level.toLowerCase()}`} role="alert">
          {notice.text}
        </div>
      )}
      <SessionSidebar
        onCreateSession={() => (
          coreConnectionRef.current?.send('chat_session_create', {}) ?? false
        )}
        onSelectSession={(sessionId) => (
          coreConnectionRef.current?.send('chat_session_select', { session_id: sessionId }) ?? false
        )}
        onDeleteSession={(sessionId) => (
          coreConnectionRef.current?.send('chat_session_delete', { session_id: sessionId }) ?? false
        )}
        onForgetSession={(sessionId) => (
          coreConnectionRef.current?.send('world_memory_forget_session', { session_id: sessionId }) ?? false
        )}
      />
      <ResidentSidebar
        operationNotice={notice}
        onCreateResident={(name, provider, model, reasoningEffort) => (
          coreConnectionRef.current?.send('resident_create', {
            name,
            provider,
            model,
            reasoning_effort: reasoningEffort
          }) ?? false
        )}
        onSetBrain={(name, provider, model, reasoningEffort) => (
          coreConnectionRef.current?.send('resident_set_brain', {
            name,
            provider,
            model,
            reasoning_effort: reasoningEffort
          }) ?? false
        )}
        onReorderResidents={(names) => (
          coreConnectionRef.current?.send('resident_reorder', { names: [...names] }) ?? false
        )}
        onSetAvatar={(name, avatarPath) => (
          coreConnectionRef.current?.send('resident_set_avatar', {
            name,
            avatar_path: avatarPath
          }) ?? false
        )}
        onSetTts={(name, tts) => (
          coreConnectionRef.current?.send('resident_set_tts', { name, tts }) ?? false
        )}
        onPreviewVoice={async (audio) => {
          speechQueueRef.current?.stopAll()
          await audioServiceRef.current?.play(audio)
        }}
        onDeleteResident={(name, confirm) => (
          coreConnectionRef.current?.send('resident_delete', { name, confirm }) ?? false
        )}
        debugContent={<>
      <div className="pose-adjust-panel">
        <div className="pose-adjust-heading">
          <strong>Holo Addon Gate 0</strong>
          <small>ChatGPT Web埋め込み・Session保持・Dive Bootstrapの成立確認</small>
        </div>
        <div className="pose-adjust-actions">
          <button
            type="button"
            onClick={() => {
              useUiStore.getState().closeRightSidebar()
              setHoloGate0Open(true)
            }}
          >
            Holo Surfaceを開く
          </button>
        </div>
      </div>
      <div className="avatar-controls">
        <small>Target: {focusedResidentName ?? runtimeRef.current?.getPrimaryResidentName() ?? 'Resident'}</small>
        <button type="button" onClick={() => void pickAvatar()}>
          VRMを選ぶ
        </button>
        <span>{avatarStatus}</span>
      </div>
      <div className="animation-controls" aria-label="Animation切替">
        <button
          type="button"
          disabled={!avatarLoaded}
          aria-pressed={animationStatus === 'stand'}
          onClick={() => playAnimation('stand')}
        >
          Stand
        </button>
        <button
          type="button"
          disabled={!avatarLoaded}
          aria-pressed={animationStatus === 'afk'}
          onClick={() => playAnimation('afk')}
        >
          AFK
        </button>
        <button
          type="button"
          disabled={!avatarLoaded}
          aria-pressed={animationStatus === 'sleep'}
          onClick={() => playAnimation('sleep')}
        >
          Sleep
        </button>
      </div>
      <div className="expression-controls" aria-label="Expression切替">
        {availableEmotions.map((name) => (
          <button
            key={name}
            type="button"
            disabled={!avatarLoaded}
            aria-pressed={emotionStatus === name}
            onClick={() => setEmotion(name)}
          >
            {name}
          </button>
        ))}
        <button
          type="button"
          disabled={!avatarLoaded}
          onClick={() => runtimeRef.current?.triggerBlink()}
        >
          blink
        </button>
      </div>
      <div className="movement-controls" aria-label="Location移動">
        <button type="button" disabled={!avatarLoaded} onClick={() => moveTo('a')}>
          Move A
        </button>
        <button type="button" disabled={!avatarLoaded} onClick={() => moveTo('b')}>
          Move B
        </button>
        <button
          className="debug-toggle"
          type="button"
          aria-pressed={visualTuningPanelVisible}
          onClick={() => setVisualTuningPanelVisible((visible) => !visible)}
        >
          Debug
        </button>
      </div>
      {visualTuningPanelVisible && (
        <section className="visual-tuning-panel" aria-label="一時Visual Speed Lab">
          <header>
            <div>
              <strong>Visual Speed Lab</strong>
              <small>開発コマンド専用・通常は非表示</small>
            </div>
          </header>
          <div className="pose-adjust-panel">
            <div className="pose-adjust-heading">
              <strong>Conversation Motion QA</strong>
              <small>Brainを呼ばず、M2の接近・向き合い・Formation・stand復帰だけを確認</small>
            </div>
            <div className="pose-adjust-actions">
              <button
                type="button"
                disabled={residents.length < 2}
                onClick={runTwoResidentConversationDebug}
              >
                2人会話演出
              </button>
              <button
                type="button"
                disabled={residents.length < 3}
                onClick={runThreeResidentConversationDebug}
              >
                3人会話演出
              </button>
              <small>2人版は続けて2回押し、2回目のapproachも自然か確認</small>
            </div>
            <p className="pose-adjust-status" aria-live="polite">{conversationDebugStatus}</p>
          </div>
          <div className="pose-adjust-panel">
            <div className="pose-adjust-heading">
              <strong>Motion Pose Editor</strong>
              <small>モーションを選び、キャラを掴んでドラッグ。Shiftで微調整</small>
            </div>
            <div className="pose-adjust-selects">
              <label>
                <span>Motion</span>
                <select
                  value={poseAdjustClip ?? ''}
                  onChange={(event) => {
                    const clip = event.currentTarget.value as AnimationClipName
                    if (clip) selectPoseMotion(clip)
                    else stopPoseAdjustment()
                  }}
                >
                  <option value="">選択...</option>
                  {poseMotionOptions.map((option) => (
                    <option key={option.clip} value={option.clip}>{option.label}</option>
                  ))}
                </select>
              </label>
              <label>
                <span>Scope</span>
                <select
                  value={poseAdjustScope}
                  disabled={!poseAdjustClip}
                  onChange={(event) => {
                    const scope = event.currentTarget.value as PoseAdjustScope
                    if (poseAdjustClip) beginPoseAdjustment(poseAdjustClip, scope)
                    else setPoseAdjustScope(scope)
                  }}
                >
                  <option value="root">Root</option>
                  <option value="head">Head</option>
                </select>
              </label>
              <button
                type="button"
                disabled={!poseAdjustClip}
                onClick={stopPoseAdjustment}
              >
                OFF
              </button>
            </div>
            <div className="pose-adjust-values">
              <span>Pitch <strong>{poseAdjustment ? poseAdjustment.pitchDeg.toFixed(1) : '---'}°</strong></span>
              <span>Yaw <strong>{poseAdjustment ? poseAdjustment.yawDeg.toFixed(1) : '---'}°</strong></span>
            </div>
            <div className="pose-adjust-actions">
              <button
                type="button"
                disabled={!poseAdjustClip || !poseAdjustment}
                onClick={() => void copyPoseAdjustment()}
              >
                値をコピー
              </button>
              <small>{poseAdjustClip ? `${poseAdjustScope === 'head' ? 'Head' : 'Root'}補正` : '対象を選択'}</small>
            </div>
            <p className="pose-adjust-status" aria-live="polite">{poseCopyStatus}</p>
          </div>
          <div className="pose-adjust-panel">
            <div className="pose-adjust-heading">
              <strong>Move Motion Lab</strong>
              <small>移動モーションの数値調整。再起動で現行値へ戻る</small>
            </div>
            <TuningSlider
              id="move-turn-speed"
              label="Turn Speed"
              value={motionTuning.turnSpeedScale * 100}
              hint="開始・終了の振り向き速度"
              minimum={20}
              maximum={150}
              onChange={(value) => updateMotionTuning('turnSpeedScale', value)}
            />
            <TuningSlider
              id="move-right-leg-match"
              label="Right Leg Match"
              value={motionTuning.rightLegMatch * 100}
              hint="0%=元Animation / 100%=左脚の振りを左右対称に合わせる"
              minimum={0}
              maximum={100}
              step={5}
              onChange={(value) => updateMotionTuning('rightLegMatch', value)}
            />
            <TuningSlider
              id="move-knee-straightening"
              label="Knee Straightening"
              value={motionTuning.kneeStraightening * 100}
              hint="高いほど膝を真っすぐへ戻す"
              minimum={0}
              maximum={100}
              step={1}
              onChange={(value) => updateMotionTuning('kneeStraightening', value)}
            />
            <div className="pose-adjust-actions">
              <button type="button" onClick={resetMotionTuning}>Move調整を初期値へ戻す</button>
              <button type="button" onClick={() => void copyMotionTuning()}>値をコピー</button>
              <small>{formatMotionTuning(motionTuning)}</small>
            </div>
            <p className="pose-adjust-status" aria-live="polite">{motionCopyStatus}</p>
          </div>
          <div className="horizon-debug-panel">
            <div className="horizon-debug-heading">
              <strong>Horizon Layer Isolator</strong>
              <small>横線が消えるレイヤを特定するための一時ON/OFF</small>
            </div>
            <div className="horizon-debug-grid">
              {HORIZON_DEBUG_EFFECTS.map(([name, label]) => (
                <button
                  key={name}
                  type="button"
                  aria-pressed={horizonDebugEffects[name]}
                  onClick={() => toggleHorizonDebugEffect(name)}
                >
                  {label}: {horizonDebugEffects[name] ? 'ON' : 'OFF'}
                </button>
              ))}
            </div>
            <button
              className="horizon-debug-restore"
              type="button"
              onClick={restoreHorizonDebugEffects}
            >
              4レイヤを全部ONへ戻す
            </button>
          </div>
          <div className="horizon-debug-panel">
            <div className="horizon-debug-heading">
              <strong>Seabed Material Isolator</strong>
              <small>横筋の犯人をColor / AO / Macroへ分解</small>
            </div>
            <div className="horizon-debug-grid">
              {SEABED_MATERIAL_DEBUG_LAYERS.map(([name, label]) => (
                <button
                  key={name}
                  type="button"
                  aria-pressed={seabedMaterialDebugLayers[name]}
                  onClick={() => toggleSeabedMaterialDebugLayer(name)}
                >
                  {label}: {seabedMaterialDebugLayers[name] ? 'ON' : 'OFF'}
                </button>
              ))}
            </div>
            <button
              className="horizon-debug-restore"
              type="button"
              onClick={restoreSeabedMaterialDebugLayers}
            >
              3要素を全部ONへ戻す
            </button>
          </div>
          <div className="horizon-debug-panel">
            <div className="horizon-debug-heading">
              <strong>Seabed Surface Isolator</strong>
              <small>Material以外の影と床形状を分離</small>
            </div>
            <div className="horizon-debug-grid">
              {SEABED_SURFACE_DEBUG_LAYERS.map(([name, label]) => (
                <button
                  key={name}
                  type="button"
                  aria-pressed={seabedSurfaceDebugLayers[name]}
                  onClick={() => toggleSeabedSurfaceDebugLayer(name)}
                >
                  {label}: {seabedSurfaceDebugLayers[name] ? 'ON' : 'OFF'}
                </button>
              ))}
            </div>
            <button
              className="horizon-debug-restore"
              type="button"
              onClick={restoreSeabedSurfaceDebugLayers}
            >
              2要素を全部ONへ戻す
            </button>
          </div>
          <TuningSlider
            id="water-speed"
            label="水面速度"
            value={visualTuning.waterSpeed * 100}
            hint="100%が現在値"
            onChange={(value) => updateVisualTuning('waterSpeed', value)}
          />
          <TuningSlider
            id="water-calmness"
            label="水面の穏やかさ"
            value={visualTuning.waterCalmness * 100}
            hint="高いほど波が穏やか"
            maximum={500}
            onChange={(value) => updateVisualTuning('waterCalmness', value)}
          />
          <TuningSlider
            id="light-shaft-speed"
            label="光柱速度"
            value={visualTuning.lightShaftSpeed * 100}
            hint="形・位置・明るさは固定"
            onChange={(value) => updateVisualTuning('lightShaftSpeed', value)}
          />
          <TuningSlider
            id="caustics-speed"
            label="Caustics速度"
            value={visualTuning.causticsSpeed * 100}
            hint="白砂の光網だけを調整"
            onChange={(value) => updateVisualTuning('causticsSpeed', value)}
          />
          <TuningSlider
            id="bubble-rise-speed"
            label="気泡の上昇速度"
            value={visualTuning.bubbleRiseSpeed * 100}
            hint="位置を戻さず上昇速度だけ変更"
            onChange={(value) => updateVisualTuning('bubbleRiseSpeed', value)}
          />
          <TuningSlider
            id="bubble-vertical-density"
            label="気泡の縦密度"
            value={visualTuning.bubbleVerticalDensity * 100}
            hint="高いほど1本あたりの泡数が増える"
            maximum={500}
            onChange={(value) => updateVisualTuning('bubbleVerticalDensity', value)}
          />
          <TuningSlider
            id="bubble-horizontal-density"
            label="気泡の横密度"
            value={visualTuning.bubbleHorizontalDensity * 100}
            hint="高いほど泡筋が細く密集"
            minimum={20}
            maximum={500}
            onChange={(value) => updateVisualTuning('bubbleHorizontalDensity', value)}
          />
          <TuningSlider
            id="horizon-haze"
            label="① 水平線の溶け込み"
            value={visualTuning.horizonHaze * 100}
            hint="高いほど床と遠景の境界を青い霞へ溶かす"
            maximum={2000}
            onChange={(value) => updateVisualTuning('horizonHaze', value)}
          />
          <TuningSlider
            id="water-paleness"
            label="② 青の淡さ"
            value={visualTuning.waterPaleness * 100}
            hint="高いほど暗い深海色を減らして淡い青へ寄せる"
            maximum={200}
            onChange={(value) => updateVisualTuning('waterPaleness', value)}
          />
          <TuningSlider
            id="sand-whiteness"
            label="③ 砂の白さ"
            value={visualTuning.sandWhiteness * 100}
            hint="GroundSand005の模様を残したまま白へ寄せる"
            maximum={165}
            onChange={(value) => updateVisualTuning('sandWhiteness', value)}
          />
          <TuningSlider
            id="sand-relief"
            label="砂の凹凸（波）"
            value={visualTuning.sandRelief * 100}
            hint="元のNormal / BUMPだけを増幅。床の実高さは変えない"
            maximum={2000}
            onChange={(value) => updateVisualTuning('sandRelief', value)}
          />
          <TuningSlider
            id="water-surface-presence"
            label="④ 水面感"
            value={visualTuning.waterSurfacePresence * 100}
            hint="水面メッシュ・背景・屈折表現の存在感をまとめて調整"
            maximum={250}
            onChange={(value) => updateVisualTuning('waterSurfacePresence', value)}
          />
          <TuningSlider
            id="resident-brightness"
            label="⑤ キャラの明るさ"
            value={visualTuning.residentBrightness * 100}
            hint="Residentを読むAmbient / Cyan Fillだけを相対調整"
            minimum={40}
            maximum={250}
            onChange={(value) => updateVisualTuning('residentBrightness', value)}
          />
          <div className="tuning-actions">
            <button type="button" onClick={resetVisualTuning}>初期値へ戻す</button>
            <button type="button" onClick={() => void copyVisualTuning()}>値をコピー</button>
          </div>
          <p className="tuning-status" aria-live="polite">{tuningCopyStatus}</p>
        </section>
      )}
      </>} />
      <div className="chat-dock">
        <ChatHistory
          focusedResidentName={focusedResidentName}
          onLoadOlder={(sessionId, before) => {
            const store = useSessionStore.getState()
            if (!store.beginOlderHistoryLoad(sessionId)) return false
            const sent = coreConnectionRef.current?.send('history_request', {
              session_id: sessionId,
              before,
              limit: 50
            }) ?? false
            if (!sent) store.cancelHistoryLoad()
            return sent
          }}
        />
        <ChatBar
          focusedResidentName={focusedResidentName}
          onSend={(text, requestId) => {
            void audioServiceRef.current?.resume()
            return coreConnectionRef.current?.send('master_say', { text, request_id: requestId }) ?? false
          }}
          onSendWhisper={(to, text, requestId) => {
            void audioServiceRef.current?.resume()
            return coreConnectionRef.current?.send('master_whisper', {
              to,
              text,
              request_id: requestId
            }) ?? false
          }}
          onCancel={(requestId) => {
            speechQueueRef.current?.cancel(requestId)
            const activeCoreRequestId = useConnectionStore.getState().activeRequestId
            if (activeCoreRequestId !== requestId) return true
            return coreConnectionRef.current?.send('cancel_response', { request_id: requestId }) ?? false
          }}
        />
      </div>
      <HoloGate0Surface
        open={holoGate0Open}
        onClose={() => setHoloGate0Open(false)}
        onDivePrepared={(diveSessionId) => (
          coreConnectionRef.current?.send('holo_dive_started', {
            dive_session_id: diveSessionId
          }) ?? false
        )}
      />
      <VolumeControl
        onVolumeChange={(nextVolume) => {
          coreConnectionRef.current?.send('audio_volume_changed', { volume: nextVolume })
        }}
      />
    </main>
  )
}
