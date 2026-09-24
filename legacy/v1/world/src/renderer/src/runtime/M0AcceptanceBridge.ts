import type { AnimationName } from '../world/vrm/AnimationController'
import type { EmotionName } from '../world/vrm/ExpressionController'
import type { SceneRuntime } from './SceneRuntime'
import type { EnvironmentEffectName } from '../world/environment/EnvironmentController'
import type { M0LocationName } from './worldConfig'
import type { VisualTuning } from './VisualTuning'

// Dev/QA command surface on window.__niraiM0. Not a product API.
// Keep it for smoke, isolator, and Visual Speed Lab automation.
export interface M0AcceptanceBridgeOptions {
  readonly onVisualTuningChange?: (value: VisualTuning) => void
  readonly onVisualTuningPanelVisibilityChange?: (visible: boolean) => void
}

export function installM0AcceptanceBridge(
  runtime: SceneRuntime,
  options: M0AcceptanceBridgeOptions = {}
): () => void {
  const target = window as Window & { __niraiM0?: unknown }
  const applyVisualTuning = (value: VisualTuning): VisualTuning => {
    const next = runtime.setVisualTuning(value)
    options.onVisualTuningChange?.(next)
    return next
  }
  const setVisualTuningPanelVisible = (visible: boolean): boolean => {
    const next = Boolean(visible)
    options.onVisualTuningPanelVisibilityChange?.(next)
    if (next) options.onVisualTuningChange?.(runtime.getVisualTuning())
    return next
  }
  const bridge = Object.freeze({
    loadAvatar: (relativePath: string) => runtime.loadAvatar(relativePath),
    playAnimation: (name: AnimationName) => runtime.playAnimation(name),
    setEmotion: (name: EmotionName) => runtime.setEmotion(name),
    getAvailableEmotions: () => runtime.getAvailableEmotions(),
    triggerBlink: () => runtime.triggerBlink(),
    moveTo: (location: M0LocationName) => runtime.moveResidentTo(location),
    setEffect: (name: EnvironmentEffectName, enabled: boolean) =>
      runtime.setEnvironmentEffect(name, enabled),
    setVisualTuning: (value: VisualTuning) => applyVisualTuning(value),
    tuneVisuals: (patch: Partial<VisualTuning>) => applyVisualTuning({
      ...runtime.getVisualTuning(),
      ...patch
    }),
    setVisualTuningPanelVisible,
    showVisualTuningPanel: () => setVisualTuningPanelVisible(true),
    hideVisualTuningPanel: () => setVisualTuningPanelVisible(false),
    setWaterSurfaceEnabled: (enabled: boolean) =>
      runtime.setEnvironmentEffect('waterSurface', enabled),
    getVisualTuning: () => runtime.getVisualTuning(),
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
