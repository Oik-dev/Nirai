import { useEffect, useRef, useState } from 'react'
import { SceneRuntime } from './runtime/SceneRuntime'
import type { M0LocationName } from './runtime/SceneRuntime'
import type { AnimationName } from './world/vrm/AnimationController'
import type { EmotionName } from './world/vrm/ExpressionController'
import {
  DEFAULT_VISUAL_TUNING,
  formatVisualTuning,
  sanitizeVisualTuning,
  type VisualTuning
} from './runtime/VisualTuning'

const LAST_AVATAR_STORAGE_KEY = 'nirai:last-avatar'
const DEFAULT_AVATAR_PATH = 'lapan/lapan.vrm'
const VISUAL_TUNING_STORAGE_KEY = 'nirai:temporary-visual-tuning'

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
  const [visualTuning, setVisualTuningState] = useState<VisualTuning>(() =>
    DEFAULT_VISUAL_TUNING
  )
  const [visualTuningPanelVisible, setVisualTuningPanelVisible] = useState(false)
  const [tuningCopyStatus, setTuningCopyStatus] = useState('')

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
    if (
      runtimeRef.current?.moveResidentTo(location, () => setAnimationStatus('stand'))
    ) {
      setAnimationStatus('walk')
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

  const copyVisualTuning = async (): Promise<void> => {
    try {
      await navigator.clipboard.writeText(formatVisualTuning(visualTuning))
      setTuningCopyStatus('コピーしました')
    } catch {
      setTuningCopyStatus('コピーできませんでした。表示値をそのまま伝えてください')
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
          aria-pressed={animationStatus === 'walk'}
          onClick={() => playAnimation('walk')}
        >
          Walk
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
        {(['neutral', 'happy', 'angry', 'sad'] as const).map((name) => (
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
