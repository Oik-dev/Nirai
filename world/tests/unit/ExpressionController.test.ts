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
    const expressionMap = {
      happy: {},
      sad: {}
    }
    const expressionManager = {
      setValue: vi.fn(),
      expressionMap,
      getExpression: vi.fn((name: string) => expressionMap[name as keyof typeof expressionMap] ?? null)
    }
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

    controller.triggerBlink(0.28)
    controller.update(0.2)
    expect(expressionManager.setValue).toHaveBeenLastCalledWith('blink', 1)
    controller.update(0.08)
    controller.update(4)
    controller.update(0.12)

    expect(expressionManager.setValue.mock.calls).toEqual([
      ['blink', 1],
      ['blink', 0],
      ['blink', 1],
      ['blink', 0]
    ])
  })

  it('maps semantic emotions to avatar-specific variants and exposes only supported emotions', () => {
    const expressionMap = {
      '01-prefab_happy01': {},
      '02-prefab_happy02': {},
      '07-prefab_angry01': {},
      '12-prefab_sad01': {},
      '15-prefab_awkward01': {},
      '19-prefab_doubt': {},
      '20-prefab_startled': {},
      blink: {}
    }
    const expressionManager = {
      setValue: vi.fn(),
      expressionMap,
      getExpression: vi.fn((name: string) => expressionMap[name as keyof typeof expressionMap] ?? null)
    }
    const controller = new ExpressionController(expressionManager, 4, 0.12, () => 0.999)

    expect(controller.getAvailableEmotions()).toEqual([
      'neutral',
      'happy',
      'angry',
      'sad',
      'surprised',
      'awkward',
      'doubt'
    ])

    controller.setEmotion('happy')
    expect(expressionManager.setValue).toHaveBeenLastCalledWith('02-prefab_happy02', 1)
    controller.setEmotion('surprised')
    expect(expressionManager.setValue.mock.calls.slice(-2)).toEqual([
      ['02-prefab_happy02', 0],
      ['20-prefab_startled', 1]
    ])
    controller.setEmotion('neutral')
    expect(expressionManager.setValue).toHaveBeenLastCalledWith('20-prefab_startled', 0)
  })

  it('maps legacy VRM0 joy, sorrow, and fun presets to Nirai semantic emotions', () => {
    const expressionMap = {
      joy: {},
      sorrow: {},
      fun: {},
      blink: {}
    }
    const expressionManager = {
      setValue: vi.fn(),
      expressionMap,
      getExpression: vi.fn((name: string) => expressionMap[name as keyof typeof expressionMap] ?? null)
    }
    const controller = new ExpressionController(expressionManager, 4, 0.12, () => 0)

    expect(controller.getAvailableEmotions()).toEqual([
      'neutral',
      'happy',
      'sad',
      'relaxed'
    ])
    controller.setEmotion('happy')
    expect(expressionManager.setValue).toHaveBeenLastCalledWith('joy', 1)
    controller.setEmotion('sad')
    expect(expressionManager.setValue).toHaveBeenLastCalledWith('sorrow', 1)
    controller.setEmotion('relaxed')
    expect(expressionManager.setValue).toHaveBeenLastCalledWith('fun', 1)
  })

  it('prefers the standard expression before model-specific variants when choosing the first variant', () => {
    const expressionMap = {
      happy: {},
      '01-prefab_happy01': {},
      relaxed: {},
      surprised: {}
    }
    const expressionManager = {
      setValue: vi.fn(),
      expressionMap,
      getExpression: vi.fn((name: string) => expressionMap[name as keyof typeof expressionMap] ?? null)
    }
    const controller = new ExpressionController(expressionManager, 4, 0.12, () => 0)

    expect(controller.getAvailableEmotions()).toEqual([
      'neutral',
      'happy',
      'relaxed',
      'surprised'
    ])
    controller.setEmotion('happy')
    expect(expressionManager.setValue).toHaveBeenLastCalledWith('happy', 1)
  })

  it('holds the eyes closed until the presentation releases them', () => {
    const happyExpression = { overrideBlink: 'block' }
    const expressionManager = {
      setValue: vi.fn(),
      getExpression: vi.fn((name: string) => name === 'happy' ? happyExpression : null)
    }
    const controller = new ExpressionController(expressionManager, 0.1, 0.05)

    controller.setEmotion('happy')
    controller.setBlinkHeld(true)
    controller.update(1)
    controller.update(1)
    expect(expressionManager.setValue).toHaveBeenLastCalledWith('blink', 1)
    expect(happyExpression.overrideBlink).toBe('none')

    controller.setBlinkHeld(false)
    expect(expressionManager.setValue).toHaveBeenLastCalledWith('blink', 0)
    expect(happyExpression.overrideBlink).toBe('block')
  })
})
