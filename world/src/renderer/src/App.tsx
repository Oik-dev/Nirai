import { useEffect, useRef, useState } from 'react'
import { SceneRuntime } from './runtime/SceneRuntime'
import type { M0LocationName } from './runtime/SceneRuntime'
import type { AnimationName } from './world/vrm/AnimationController'
import type { EmotionName } from './world/vrm/ExpressionController'
import { installM0AcceptanceBridge } from './runtime/M0AcceptanceBridge'

const LAST_AVATAR_STORAGE_KEY = 'nirai:last-avatar'
const DEFAULT_AVATAR_PATH = 'lapan/lapan.vrm'

export function App(): JSX.Element {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const runtimeRef = useRef<SceneRuntime | null>(null)
  const [avatarStatus, setAvatarStatus] = useState('Avatar未選択')
  const [avatarLoaded, setAvatarLoaded] = useState(false)
  const [animationStatus, setAnimationStatus] = useState<AnimationName>('stand')
  const [emotionStatus, setEmotionStatus] = useState<EmotionName>('neutral')

  useEffect(() => {
    const canvas = canvasRef.current

    if (!canvas) {
      return
    }

    const runtime = new SceneRuntime()
    let cancelled = false
    runtimeRef.current = runtime
    runtime.start(canvas)
    const removeAcceptanceBridge = import.meta.env.DEV
      ? installM0AcceptanceBridge(runtime)
      : () => undefined

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
      </div>
    </main>
  )
}
