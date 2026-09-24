import { describe, expect, it } from 'vitest'
import {
  WORLD_SELECTION_DRAG_THRESHOLD_PX,
  installWorldSelectionGesture
} from '../../src/renderer/src/runtime/worldSelectionGesture'

interface PointerEventShape {
  readonly pointerId?: number
  readonly button?: number
  readonly clientX?: number
  readonly clientY?: number
  readonly isPrimary?: boolean
}

function pointerEvent(type: string, values: PointerEventShape = {}): Event {
  const event = new Event(type)
  Object.defineProperties(event, {
    pointerId: { value: values.pointerId ?? 1 },
    button: { value: values.button ?? 0 },
    clientX: { value: values.clientX ?? 100 },
    clientY: { value: values.clientY ?? 100 },
    isPrimary: { value: values.isPrimary ?? true }
  })
  return event
}

describe('World selection pointer gesture', () => {
  it('commits only a completed primary pointerdown -> pointerup gesture below the drag threshold', () => {
    const target = new EventTarget()
    const commits: Array<{ clientX: number; clientY: number }> = []
    const remove = installWorldSelectionGesture(target, (gesture) => commits.push(gesture))

    target.dispatchEvent(pointerEvent('pointerdown', { clientX: 100, clientY: 100 }))
    target.dispatchEvent(pointerEvent('pointermove', {
      clientX: 100 + WORLD_SELECTION_DRAG_THRESHOLD_PX - 1,
      clientY: 100
    }))
    target.dispatchEvent(pointerEvent('pointerup', {
      clientX: 100 + WORLD_SELECTION_DRAG_THRESHOLD_PX - 1,
      clientY: 100
    }))

    expect(commits).toEqual([{
      clientX: 100 + WORLD_SELECTION_DRAG_THRESHOLD_PX - 1,
      clientY: 100
    }])
    remove()
  })

  it('does not commit pointerdown -> move -> pointerup after the drag threshold is crossed', () => {
    const target = new EventTarget()
    const commits: Array<{ clientX: number; clientY: number }> = []
    const remove = installWorldSelectionGesture(target, (gesture) => commits.push(gesture))

    target.dispatchEvent(pointerEvent('pointerdown', { clientX: 100, clientY: 100 }))
    target.dispatchEvent(pointerEvent('pointermove', {
      clientX: 100 + WORLD_SELECTION_DRAG_THRESHOLD_PX + 1,
      clientY: 100
    }))
    target.dispatchEvent(pointerEvent('pointerup', {
      clientX: 100 + WORLD_SELECTION_DRAG_THRESHOLD_PX + 1,
      clientY: 100
    }))

    expect(commits).toEqual([])
    remove()
  })

  it('discards pointercancel, pointerleave, right-click and disabled gestures', () => {
    const target = new EventTarget()
    const commits: Array<{ clientX: number; clientY: number }> = []
    let enabled = true
    const remove = installWorldSelectionGesture(
      target,
      (gesture) => commits.push(gesture),
      () => enabled
    )

    target.dispatchEvent(pointerEvent('pointerdown'))
    target.dispatchEvent(pointerEvent('pointercancel'))
    target.dispatchEvent(pointerEvent('pointerup'))

    target.dispatchEvent(pointerEvent('pointerdown', { pointerId: 2 }))
    target.dispatchEvent(pointerEvent('pointerleave', { pointerId: 2 }))
    target.dispatchEvent(pointerEvent('pointerup', { pointerId: 2 }))

    target.dispatchEvent(pointerEvent('pointerdown', { pointerId: 3, button: 2 }))
    target.dispatchEvent(pointerEvent('pointerup', { pointerId: 3, button: 2 }))

    enabled = false
    target.dispatchEvent(pointerEvent('pointerdown', { pointerId: 4 }))
    target.dispatchEvent(pointerEvent('pointerup', { pointerId: 4 }))

    expect(commits).toEqual([])
    remove()
  })
})
