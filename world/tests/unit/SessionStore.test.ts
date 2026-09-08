import { beforeEach, describe, expect, it } from 'vitest'
import { useSessionStore, type ChatEntry } from '../../src/renderer/src/stores/sessionStore'

function entry(index: number): ChatEntry {
  return {
    ts: `2026-08-29T00:00:${String(index % 60).padStart(2, '0')}.${String(index).padStart(6, '0')}+09:00`,
    kind: 'say',
    from: 'master',
    text: `message-${index}`,
    session: 'S-1',
    request_id: `REQ-${index}`
  }
}

describe('sessionStore history pagination', () => {
  beforeEach(() => {
    useSessionStore.setState({
      sessions: [],
      activeSessionId: null,
      entries: [],
      hasOlder: false,
      olderHistoryCursor: null,
      historyLoading: false,
      historyLoadedSessionId: null
    })
  })

  it('prepends older history without replacing the latest page', () => {
    const store = useSessionStore.getState()
    store.setSessionList([
      {
        id: 'S-1',
        title: 'History',
        created_at: '2026-08-29T00:00:00+09:00',
        updated_at: '2026-08-29T00:00:00+09:00'
      }
    ], 'S-1')

    const latest = Array.from({ length: 50 }, (_, index) => entry(index + 25))
    useSessionStore.getState().setHistory('S-1', latest, '25')
    expect(useSessionStore.getState().hasOlder).toBe(true)
    expect(useSessionStore.getState().olderHistoryCursor).toBe('25')
    expect(useSessionStore.getState().beginOlderHistoryLoad('S-1')).toBe(true)

    const older = Array.from({ length: 25 }, (_, index) => entry(index))
    useSessionStore.getState().setHistory('S-1', older, null)

    const state = useSessionStore.getState()
    expect(state.entries).toHaveLength(75)
    expect(state.entries[0]?.request_id).toBe('REQ-0')
    expect(state.entries.at(-1)?.request_id).toBe('REQ-74')
    expect(state.hasOlder).toBe(false)
    expect(state.olderHistoryCursor).toBeNull()
    expect(state.historyLoading).toBe(false)
  })

  it('does not start concurrent or cross-session older-history loads', () => {
    useSessionStore.getState().setSessionList([], 'S-1')
    useSessionStore.getState().setHistory(
      'S-1',
      Array.from({ length: 50 }, (_, index) => entry(index)),
      '50'
    )

    expect(useSessionStore.getState().beginOlderHistoryLoad('S-2')).toBe(false)
    expect(useSessionStore.getState().beginOlderHistoryLoad('S-1')).toBe(true)
    expect(useSessionStore.getState().beginOlderHistoryLoad('S-1')).toBe(false)
  })

  it('keeps distinct entries from the same request and deduplicates stable ids', () => {
    const store = useSessionStore.getState()
    store.setSessionList([], 'S-1')
    const first = { ...entry(1), entry_id: 'CE-1' }
    const second = { ...first, entry_id: 'CE-2', text: 'another utterance' }
    store.appendEntry(first)
    store.appendEntry(second)
    store.appendEntry({ ...first, request_id: undefined })
    expect(useSessionStore.getState().entries).toEqual([first, second])
  })

  it('preserves identical content with distinct stable ids across history pages', () => {
    const store = useSessionStore.getState()
    store.setSessionList([], 'S-1')
    const first = { ...entry(1), entry_id: 'CE-1' }
    const second = { ...first, entry_id: 'CE-2' }
    store.setHistory('S-1', [second], '1')
    store.beginOlderHistoryLoad('S-1')
    store.setHistory('S-1', [first, second], null)
    expect(useSessionStore.getState().entries).toEqual([first, second])
  })

  it('deduplicates exact legacy entries without collapsing different text', () => {
    const store = useSessionStore.getState()
    store.setSessionList([], 'S-1')
    const first = { ...entry(1), request_id: undefined }
    const second = { ...first, text: 'different' }
    store.appendEntry(first)
    store.appendEntry({ ...first })
    store.appendEntry(second)
    expect(useSessionStore.getState().entries).toEqual([first, second])
  })

  it('deduplicates exact legacy duplicates already contained in one history page', () => {
    const store = useSessionStore.getState()
    store.setSessionList([], 'S-1')
    const first = { ...entry(1), request_id: undefined }
    const second = { ...first, text: 'different' }

    store.setHistory('S-1', [first, { ...first }, second], null)

    expect(useSessionStore.getState().entries).toEqual([first, second])
  })
})
