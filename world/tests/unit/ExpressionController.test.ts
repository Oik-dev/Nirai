import { describe, expect, it, vi } from 'vitest'
import { ExpressionController } from '../../src/renderer/src/world/vrm/ExpressionController'

describe('ExpressionController', () => {
  it('normalizes a blink preset that incorrectly blocks itself', () => {
    const blinkExpression = { overrideBlink: 'block' }

    new ExpressionController({
      setValue: vi.fn(),
      getExpression: vi.fn(() => blinkExpression)
    })

    expect(blinkExpression.overrideBlink).toBe('none')
  })

  it('replaces the active emotion and clears it for neutral', () => {
    const expressionManager = { setValue: vi.fn() }
    const controller = new ExpressionController(expressionManager)

    controller.setEmotion('happy')
    controller.setEmotion('sad')
    controller.setEmotion('neutral')

    expect(expressionManager.setValue.mock.calls).toEqual([
      ['happy', 1],
      ['happy', 0],
      ['sad', 1],
      ['sad', 0]
    ])
    expect(controller.currentEmotion).toBe('neutral')
  })

  it('closes and reopens both manual and automatic blinks', () => {
    const expressionManager = { setValue: vi.fn() }
    const controller = new ExpressionController(expressionManager, 4, 0.12)

    controller.triggerBlink()
    controller.update(0.12)
    controller.update(4)
    controller.update(0.12)

    expect(expressionManager.setValue.mock.calls).toEqual([
      ['blink', 1],
      ['blink', 0],
      ['blink', 1],
      ['blink', 0]
    ])
  })
})
