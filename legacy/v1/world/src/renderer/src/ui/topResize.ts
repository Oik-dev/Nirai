export interface TopResizeRange {
  readonly minimum: number
  readonly maximum: number
}

export function clampTopResizeHeight(
  startHeight: number,
  startPointerY: number,
  currentPointerY: number,
  range: TopResizeRange
): number {
  const minimum = Math.max(1, Math.min(range.minimum, range.maximum))
  const maximum = Math.max(minimum, range.maximum)
  const requested = startHeight + (startPointerY - currentPointerY)
  return Math.min(maximum, Math.max(minimum, requested))
}

export function clampStoredPanelHeight(
  height: number,
  options: {
    readonly viewportHeight: number
    readonly bottomGap: number
    readonly topClearance: number
    readonly minimum: number
  }
): number {
  const maximum = Math.max(1, options.viewportHeight - options.bottomGap - options.topClearance)
  return clampTopResizeHeight(height, 0, 0, {
    minimum: options.minimum,
    maximum
  })
}

export function activeResizableHeight(active: boolean, height: number | null): number | null {
  return active ? height : null
}
