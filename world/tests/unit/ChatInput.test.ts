import { describe, expect, it } from 'vitest'
import {
  completeResidentMention,
  parseChatInput,
  residentMentionCandidates
} from '../../src/renderer/src/ui/chatInput'

describe('parseChatInput', () => {
  it('treats plain text as Say', () => {
    expect(parseChatInput('  こんにちは  ', ['Lapan'])).toEqual({
      kind: 'say',
      text: 'こんにちは'
    })
  })

  it('routes plain text to the focused resident as Whisper', () => {
    expect(parseChatInput('  こんにちは  ', ['Lapan'], 'Lapan')).toEqual({
      kind: 'whisper',
      to: 'Lapan',
      text: 'こんにちは'
    })
  })

  it('keeps plain text as Say when the focused resident no longer exists', () => {
    expect(parseChatInput('こんにちは', ['Lapan'], 'Deleted')).toEqual({
      kind: 'say',
      text: 'こんにちは'
    })
  })

  it('resolves Whisper against actual resident names', () => {
    expect(parseChatInput('@Lapan これは秘密', ['Lapan'])).toEqual({
      kind: 'whisper',
      to: 'Lapan',
      text: 'これは秘密'
    })
  })

  it('supports resident names containing spaces by longest match', () => {
    expect(parseChatInput('@A B 秘密', ['A', 'A B'])).toEqual({
      kind: 'whisper',
      to: 'A B',
      text: '秘密'
    })
  })

  it('lets an explicit mention override the focused resident', () => {
    expect(parseChatInput('@Kina こっちへ', ['Lapan', 'Kina'], 'Lapan')).toEqual({
      kind: 'whisper',
      to: 'Kina',
      text: 'こっちへ'
    })
  })

  it('does not accidentally send an unknown mention as Say', () => {
    expect(parseChatInput('@Nobody 秘密', ['Lapan']).kind).toBe('invalid-whisper')
  })
})

describe('Resident mention completion', () => {
  it('shows every resident immediately after a half-width at sign', () => {
    expect(residentMentionCandidates('@', ['Lapan', 'Kina'])).toEqual(['Kina', 'Lapan'])
  })

  it('filters candidates by the typed prefix and closes after the separator', () => {
    expect(residentMentionCandidates('@La', ['Lapan', 'Kina'])).toEqual(['Lapan'])
    expect(residentMentionCandidates('@Lapan ', ['Lapan', 'Kina'])).toEqual([])
  })

  it('completes a mention with a trailing space ready for the message', () => {
    expect(completeResidentMention('Lapan')).toBe('@Lapan ')
  })
})
