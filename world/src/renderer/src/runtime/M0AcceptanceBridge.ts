import type { AnimationName } from '../world/vrm/AnimationController'
import type { EmotionName } from '../world/vrm/ExpressionController'
import type { SceneRuntime } from './SceneRuntime'
import type { EnvironmentEffectName } from '../world/environment/EnvironmentController'
import type { M0LocationName } from './worldConfig'

export function installM0AcceptanceBridge(runtime: SceneRuntime): () => void {
  const target = window as Window & { __niraiM0?: unknown }
  const bridge = Object.freeze({
    loadAvatar: (relativePath: string) => runtime.loadAvatar(relativePath),
    playAnimation: (name: AnimationName) => runtime.playAnimation(name),
    setEmotion: (name: EmotionName) => runtime.setEmotion(name),
    triggerBlink: () => runtime.triggerBlink(),
    moveTo: (location: M0LocationName) => runtime.moveResidentTo(location),
    setEffect: (name: EnvironmentEffectName, enabled: boolean) =>
      runtime.setEnvironmentEffect(name, enabled),
    snapshot: () => runtime.getM0Diagnostics()
  })
  Object.defineProperty(target, '__niraiM0', {
    configurable: true,
    value: bridge
  })

  return () => {
    if (target.__niraiM0 === bridge) {
      delete target.__niraiM0
    }
  }
}
