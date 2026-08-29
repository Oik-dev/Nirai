import { describe, expect, it } from 'vitest'
import type { ChatEntry } from '../../src/renderer/src/stores/sessionStore'
import {
  chatEntryReadKey,
  chatHistoryViewKey,
  filterChatHistoryEntries,
  firstUnreadEntryIndex,
  initializeReadMarkers,
  shouldAutoLoadOlderHistory
} from '../../src/renderer/src/ui/chatHistoryView'

function entry(
  kind: ChatEntry['kind'],
  from: string,
  text: string,
  options: { to?: string; ts?: string; requestId?: string } = {}
): ChatEntry {
  return {
    ts: options.ts ?? '2026-08-29T00:00:00+09:00',
    kind,
    from,
    ...(options.to ? { to: options.to } : {}),
    text,
    session: 'S-1',
    ...(options.requestId ? { request_id: options.requestId } : {})
  }
}

const ENTRIES: readonly ChatEntry[] = [
  entry('say', 'Master', 'world-1', { requestId: 'R-1' }),
  entry('resident_say', 'Lapan', 'world-2', { requestId: 'R-1', ts: '2026-08-29T00:00:01+09:00' }),
  entry('whisper', 'Master', 'secret-lapan-1', { to: 'Lapan', requestId: 'R-2', ts: '2026-08-29T00:00:02+09:00' }),
  entry('resident_whisper', 'Lapan', 'secret-lapan-2', { requestId: 'R-2', ts: '2026-08-29T00:00:03+09:00' }),
  entry('whisper', 'Master', 'secret-kina', { to: 'Kina', requestId: 'R-3', ts: '2026-08-29T00:00:04+09:00' }),
  entry('resident_chat', 'Lapan', 'resident-world', { to: 'Kina', requestId: 'R-4', ts: '2026-08-29T00:00:05+09:00' })
]

describe('chat history views', () => {
  it('shows only World Chat when no Resident is focused', () => {
    expect(filterChatHistoryEntries(ENTRIES, { kind: 'world' }).map((item) => item.text)).toEqual([
      'world-1',
      'world-2',
      'resident-world'
    ])
  })

  it('shows only the focused Resident whisper conversation', () => {
    expect(filterChatHistoryEntries(ENTRIES, { kind: 'whisper', residentName: 'Lapan' }).map((item) => item.text)).toEqual([
      'secret-lapan-1',
      'secret-lapan-2'
    ])
    expect(filterChatHistoryEntries(ENTRIES, { kind: 'whisper', residentName: 'Kina' }).map((item) => item.text)).toEqual([
      'secret-kina'
    ])
  })

  it('finds the first unread entry per view and treats a fully read view as complete', () => {
    const lapan = filterChatHistoryEntries(ENTRIES, { kind: 'whisper', residentName: 'Lapan' })
    expect(firstUnreadEntryIndex(lapan, chatEntryReadKey(lapan[0]))).toBe(1)
    expect(firstUnreadEntryIndex(lapan, chatEntryReadKey(lapan[1]))).toBeNull()
  })

  it('auto-loads older pages when a filtered view is empty or too short to scroll', () => {
    expect(shouldAutoLoadOlderHistory({
      hasOlder: true,
      historyLoading: false,
      visibleEntryCount: 0,
      scrollHeight: 0,
      clientHeight: 320
    })).toBe(true)
    expect(shouldAutoLoadOlderHistory({
      hasOlder: true,
      historyLoading: false,
      visibleEntryCount: 2,
      scrollHeight: 280,
      clientHeight: 320
    })).toBe(true)
    expect(shouldAutoLoadOlderHistory({
      hasOlder: true,
      historyLoading: false,
      visibleEntryCount: 10,
      scrollHeight: 640,
      clientHeight: 320
    })).toBe(false)
    expect(shouldAutoLoadOlderHistory({
      hasOlder: true,
      historyLoading: true,
      visibleEntryCount: 0,
      scrollHeight: 0,
      clientHeight: 320
    })).toBe(false)
  })

  it('initializes independent read markers for World and each Whisper target', () => {
    const markers = new Map<string, string>()
    initializeReadMarkers('S-1', ENTRIES, markers)

    expect(markers.get(chatHistoryViewKey('S-1', { kind: 'world' }))).toBe(
      chatEntryReadKey(ENTRIES[5])
    )
    expect(markers.get(chatHistoryViewKey('S-1', { kind: 'whisper', residentName: 'Lapan' }))).toBe(
      chatEntryReadKey(ENTRIES[3])
    )
    expect(markers.get(chatHistoryViewKey('S-1', { kind: 'whisper', residentName: 'Kina' }))).toBe(
      chatEntryReadKey(ENTRIES[4])
    )
  })
})
