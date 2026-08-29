import { describe, expect, it } from 'vitest'
import { formatResidentBubbleText } from '../../src/renderer/src/ui/ResidentSpeechBubble'

describe('ResidentSpeechBubble', () => {
  it('keeps short speech while normalizing whitespace', () => {
    expect(formatResidentBubbleText('  こんにちは\nMaster  ')).toBe('こんにちは Master')
  })

  it('truncates long head-up speech without changing the chat source text', () => {
    const text = '長い返答'.repeat(40)
    const formatted = formatResidentBubbleText(text)

    expect(formatted.length).toBe(96)
    expect(formatted.endsWith('…')).toBe(true)
    expect(text.length).toBeGreaterThan(formatted.length)
  })
})
