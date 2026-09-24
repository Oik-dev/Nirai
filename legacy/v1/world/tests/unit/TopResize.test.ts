import { describe, expect, it } from 'vitest'
import {
  activeResizableHeight,
  clampStoredPanelHeight,
  clampTopResizeHeight
} from '../../src/renderer/src/ui/topResize'

describe('top-edge panel resizing', () => {
  it('grows upward and shrinks downward while keeping the bottom anchored', () => {
    expect(clampTopResizeHeight(400, 300, 220, { minimum: 160, maximum: 700 })).toBe(480)
    expect(clampTopResizeHeight(400, 300, 360, { minimum: 160, maximum: 700 })).toBe(340)
  })

  it('clamps resize requests to the available panel range', () => {
    expect(clampTopResizeHeight(400, 300, -1000, { minimum: 160, maximum: 700 })).toBe(700)
    expect(clampTopResizeHeight(400, 300, 1000, { minimum: 160, maximum: 700 })).toBe(160)
  })

  it('normalizes an inverted range without producing an invalid height', () => {
    expect(clampTopResizeHeight(400, 300, 250, { minimum: 500, maximum: 300 })).toBe(300)
  })

  it('reclamps a stored height when the viewport becomes shorter', () => {
    expect(clampStoredPanelHeight(700, {
      viewportHeight: 600,
      bottomGap: 24,
      topClearance: 48,
      minimum: 320
    })).toBe(528)
    expect(clampStoredPanelHeight(500, {
      viewportHeight: 300,
      bottomGap: 12,
      topClearance: 48,
      minimum: 320
    })).toBe(240)
  })

  it('drops the remembered height while a resizable panel is inactive', () => {
    expect(activeResizableHeight(true, 420)).toBe(420)
    expect(activeResizableHeight(false, 420)).toBeNull()
    expect(activeResizableHeight(true, null)).toBeNull()
  })
})
