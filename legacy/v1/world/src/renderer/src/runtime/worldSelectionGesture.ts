export const WORLD_SELECTION_DRAG_THRESHOLD_PX = 6

export interface CommittedWorldSelectionGesture {
  readonly clientX: number
  readonly clientY: number
}

interface ActivePointerGesture {
  readonly pointerId: number
  readonly startX: number
  readonly startY: number
  dragged: boolean
}

function isPrimaryPointer(event: PointerEvent): boolean {
  return event.isPrimary !== false
}

export function installWorldSelectionGesture(
  target: EventTarget,
  onCommit: (gesture: CommittedWorldSelectionGesture) => void,
  enabled: () => boolean = () => true
): () => void {
  let active: ActivePointerGesture | null = null
  const thresholdSquared = WORLD_SELECTION_DRAG_THRESHOLD_PX * WORLD_SELECTION_DRAG_THRESHOLD_PX

  const reset = (): void => {
    active = null
  }

  const onPointerDown = (rawEvent: Event): void => {
    const event = rawEvent as PointerEvent
    reset()
    if (!enabled() || !isPrimaryPointer(event) || event.button !== 0) return
    active = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      dragged: false
    }
  }

  const onPointerMove = (rawEvent: Event): void => {
    const event = rawEvent as PointerEvent
    const gesture = active
    if (!gesture || event.pointerId !== gesture.pointerId) return
    const deltaX = event.clientX - gesture.startX
    const deltaY = event.clientY - gesture.startY
    if (deltaX * deltaX + deltaY * deltaY > thresholdSquared) {
      gesture.dragged = true
    }
  }

  const onPointerUp = (rawEvent: Event): void => {
    const event = rawEvent as PointerEvent
    const gesture = active
    reset()
    if (
      !gesture
      || !enabled()
      || !isPrimaryPointer(event)
      || event.pointerId !== gesture.pointerId
      || event.button !== 0
    ) return

    const deltaX = event.clientX - gesture.startX
    const deltaY = event.clientY - gesture.startY
    const movedTooFar = gesture.dragged || deltaX * deltaX + deltaY * deltaY > thresholdSquared
    if (movedTooFar) return

    onCommit({ clientX: event.clientX, clientY: event.clientY })
  }

  const onPointerCancel = (rawEvent: Event): void => {
    const event = rawEvent as PointerEvent
    if (active?.pointerId === event.pointerId) reset()
  }

  const onPointerLeave = (rawEvent: Event): void => {
    const event = rawEvent as PointerEvent
    if (active?.pointerId === event.pointerId) reset()
  }

  target.addEventListener('pointerdown', onPointerDown)
  target.addEventListener('pointermove', onPointerMove)
  target.addEventListener('pointerup', onPointerUp)
  target.addEventListener('pointercancel', onPointerCancel)
  target.addEventListener('pointerleave', onPointerLeave)

  return () => {
    reset()
    target.removeEventListener('pointerdown', onPointerDown)
    target.removeEventListener('pointermove', onPointerMove)
    target.removeEventListener('pointerup', onPointerUp)
    target.removeEventListener('pointercancel', onPointerCancel)
    target.removeEventListener('pointerleave', onPointerLeave)
  }
}
