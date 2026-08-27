import { useEffect, useRef, useState } from 'react'
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

const LAST_AVATAR_STORAGE_KEY = 'nirai:last-avatar'
const DEFAULT_AVATAR_PATH = 'lapan/lapan.vrm'
// Persisted Visual Speed Lab values. Renaming this key resets operator tuning.
const VISUAL_TUNING_STORAGE_KEY = 'nirai:temporary-visual-tuning'

// DEV-only isolator lists. Product rendering keeps every layer enabled.
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
  readonly onChange: (value: number) => void
}

function TuningSlider({
  id,
  label,
  value,
  hint,
  minimum = 0,
  maximum = 1000,
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
        step="5"
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

export function App(): JSX.Element {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const runtimeRef = useRef<SceneRuntime | null>(null)
  const [avatarStatus, setAvatarStatus] = useState('Avatar未選択')
  const [avatarLoaded, setAvatarLoaded] = useState(false)
  const [animationStatus, setAnimationStatus] = useState<AnimationName>('stand')
  const [emotionStatus, setEmotionStatus] = useState<EmotionName>('neutral')
  const [availableEmotions, setAvailableEmotions] = useState<readonly EmotionName[]>(['neutral'])
  const [visualTuning, setVisualTuningState] = useState<VisualTuning>(() =>
    DEFAULT_VISUAL_TUNING
  )
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

  useEffect(() => {
    const canvas = canvasRef.current

    if (!canvas) {
      return
    }

    const runtime = new SceneRuntime()
    let cancelled = false
    runtimeRef.current = runtime
    runtime.setVisualTuning(visualTuning)
    runtime.start(canvas)
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

    const initialAvatar = localStorage.getItem(LAST_AVATAR_STORAGE_KEY) ?? DEFAULT_AVATAR_PATH
    setAvatarStatus('Avatar読込中')
    void runtime.loadAvatar(initialAvatar).then(() => {
      if (cancelled) return
      setAvatarStatus(initialAvatar)
      setAvatarLoaded(true)
      setAnimationStatus('stand')
      setEmotionStatus('neutral')
      setAvailableEmotions(runtime.getAvailableEmotions())
      setPoseMotionOptions(runtime.getPoseAdjustMotionOptions())
    }).catch((error) => {
      if (cancelled) return
      const message = error instanceof Error ? error.message : 'Avatarを読み込めませんでした'
      setAvatarStatus(message)
      setAvatarLoaded(false)
    })

    return () => {
      cancelled = true
      removeAcceptanceBridge()
      runtime.dispose()
      runtimeRef.current = null
    }
  }, [])

  useEffect(() => {
    if (visualTuningPanelVisible) {
      return
    }

    runtimeRef.current?.stopPoseAdjustment()
    setPoseAdjustClip(null)
    setPoseAdjustment(null)
    setPoseCopyStatus('')
  }, [visualTuningPanelVisible])

  const pickAvatar = async (): Promise<void> => {
    const relativePath = await window.nirai.avatar.pick()

    if (!relativePath || !runtimeRef.current) {
      return
    }

    setAvatarStatus('Avatar読込中')

    try {
      await runtimeRef.current.loadAvatar(relativePath)
      localStorage.setItem(LAST_AVATAR_STORAGE_KEY, relativePath)
      setAvatarStatus(relativePath)
      setAvatarLoaded(true)
      setAnimationStatus('stand')
      setEmotionStatus('neutral')
      setAvailableEmotions(runtimeRef.current.getAvailableEmotions())
      setPoseMotionOptions(runtimeRef.current.getPoseAdjustMotionOptions())
    } catch (error) {
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
    try {
      await navigator.clipboard.writeText(formatVisualTuning(visualTuning))
      setTuningCopyStatus('コピーしました')
    } catch {
      setTuningCopyStatus('コピーできませんでした。表示値をそのまま伝えてください')
    }
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

    try {
      await navigator.clipboard.writeText(text)
      setPoseCopyStatus('コピーしました')
    } catch {
      setPoseCopyStatus(text)
    }
  }

  return (
    <main className="app-shell">
      <canvas ref={canvasRef} aria-label="Nirai 3D World" />
      <h1>Nirai</h1>
      <div className="avatar-controls">
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
        {import.meta.env.DEV && (
          <button
            className="debug-toggle"
            type="button"
            aria-pressed={visualTuningPanelVisible}
            onClick={() => setVisualTuningPanelVisible((visible) => !visible)}
          >
            Debug
          </button>
        )}
      </div>
      {import.meta.env.DEV && visualTuningPanelVisible && (
        <section className="visual-tuning-panel" aria-label="一時Visual Speed Lab">
          <header>
            <div>
              <strong>Visual Speed Lab</strong>
              <small>開発コマンド専用・通常は非表示</small>
            </div>
          </header>
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
    </main>
  )
}
