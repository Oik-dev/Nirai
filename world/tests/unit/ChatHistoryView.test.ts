import { describe, expect, it } from 'vitest'
import type { ChatEntry } from '../../src/renderer/src/stores/sessionStore'
import {
  captureChatHistoryScrollState,
  chatEntryLabel,
  chatEntryReadKey,
  chatHistoryViewKey,
  filterChatHistoryEntries,
  firstUnreadEntryIndex,
  initializeReadMarkers,
  isWhisperChatEntry,
  isWorldPresentationEntry,
  restoreChatHistoryScrollTop,
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
  entry('resident_chat', 'Lapan', 'resident-world', { to: 'Kina', requestId: 'R-4', ts: '2026-08-29T00:00:05+09:00' }),
  entry('holo_say', 'Holo', 'holo-world', { to: 'Lapan', ts: '2026-08-29T00:00:06+09:00' })
]

describe('chat history views', () => {
  it('shows only World Chat when no Resident is focused', () => {
    expect(filterChatHistoryEntries(ENTRIES, { kind: 'world' }).map((item) => item.text)).toEqual([
      'world-1',
      'world-2',
      'resident-world',
      'holo-world'
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

  it('keeps Whisper out of World presentation while allowing public Resident/Holo speech', () => {
    expect(isWorldPresentationEntry(ENTRIES[1])).toBe(true)
    expect(isWorldPresentationEntry(ENTRIES[3])).toBe(false)
    expect(isWorldPresentationEntry(ENTRIES[5])).toBe(true)
    expect(isWorldPresentationEntry(ENTRIES[6])).toBe(true)
  })

  it('returns completed Task reports to World as Resident speech but keeps lifecycle failures silent', () => {
    const completed = entry('task', 'Codex', 'できたよ！修正と確認まで終わってる。')
    const failed = entry('task', 'Codex', 'Task失敗: provider error')
    const interrupted = entry('task', 'Codex', 'Task中断: Provider利用制限へ到達しました')
    const cancelled = entry('task', 'Codex', 'Task停止: Masterが停止しました')

    expect(chatEntryLabel(completed)).toBe('Codex')
    expect(isWorldPresentationEntry(completed)).toBe(true)
    expect(isWorldPresentationEntry(failed)).toBe(false)
    expect(isWorldPresentationEntry(interrupted)).toBe(false)
    expect(isWorldPresentationEntry(cancelled)).toBe(false)
  })

  it('uses compact speaker-only labels for Whisper while keeping Say labels unchanged', () => {
    expect(chatEntryLabel(ENTRIES[0])).toBe('あなた')
    expect(chatEntryLabel(ENTRIES[2])).toBe('あなた')
    expect(chatEntryLabel(ENTRIES[3])).toBe('Lapan')
    expect(isWhisperChatEntry(ENTRIES[0])).toBe(false)
    expect(isWhisperChatEntry(ENTRIES[2])).toBe(true)
    expect(isWhisperChatEntry(ENTRIES[3])).toBe(true)
    expect(chatEntryLabel(ENTRIES[6])).toBe('Holo → Lapan')
    expect(isWhisperChatEntry(ENTRIES[6])).toBe(false)
    // The holo_say label follows the actual sender: the holo-addon resident
    // may carry any name Master chose.
    expect(chatEntryLabel(entry('holo_say', 'ホロ', 'named-holo', { ts: '2026-08-29T00:00:07+09:00' }))).toBe('ホロ')
  })

  it('finds the first unread entry per view and treats a fully read view as complete', () => {
    const lapan = filterChatHistoryEntries(ENTRIES, { kind: 'whisper', residentName: 'Lapan' })
    expect(firstUnreadEntryIndex(lapan, chatEntryReadKey(lapan[0]))).toBe(1)
    expect(firstUnreadEntryIndex(lapan, chatEntryReadKey(lapan[1]))).toBeNull()
  })

  it('uses stable entry ids for read markers even when timestamps and text match', () => {
    const first = { ...ENTRIES[0], entry_id: 'CE-1' }
    const second = { ...first, entry_id: 'CE-2' }
    expect(chatEntryReadKey(first)).not.toBe(chatEntryReadKey(second))
    expect(firstUnreadEntryIndex([first, second], chatEntryReadKey(second))).toBeNull()
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
    expect(shouldAutoLoadOlderHistory({
      hasOlder: true,
      historyLoading: false,
      visibleEntryCount: 0,
      scrollHeight: 0,
      clientHeight: 320,
      autoLoadedPageCount: 10
    })).toBe(false)
  })

  it('restores each tab return to its saved position unless it was following the bottom', () => {
    const readingOlder = captureChatHistoryScrollState(240, 1200, 400)
    expect(readingOlder).toEqual({ top: 240, pinnedToBottom: false })
    expect(restoreChatHistoryScrollTop(readingOlder, 1600, 400)).toBe(240)

    const following = captureChatHistoryScrollState(790, 1200, 400)
    expect(following.pinnedToBottom).toBe(true)
    expect(restoreChatHistoryScrollTop(following, 1600, 400)).toBe(1600)

    // display:none reports zero layout dimensions. That state must never
    // overwrite a real manual position as "following the bottom".
    expect(captureChatHistoryScrollState(0, 0, 0).pinnedToBottom).toBe(false)

    // If older history disappeared or the viewport grew, a saved manual
    // position is clamped into the currently scrollable range.
    expect(restoreChatHistoryScrollTop({ top: 900, pinnedToBottom: false }, 1000, 400)).toBe(600)
  })

  it('initializes independent read markers for World and each Whisper target', () => {
    const markers = new Map<string, string>()
    initializeReadMarkers('S-1', ENTRIES, markers)

    expect(markers.get(chatHistoryViewKey('S-1', { kind: 'world' }))).toBe(
      chatEntryReadKey(ENTRIES[6])
    )
    expect(markers.get(chatHistoryViewKey('S-1', { kind: 'whisper', residentName: 'Lapan' }))).toBe(
      chatEntryReadKey(ENTRIES[3])
    )
    expect(markers.get(chatHistoryViewKey('S-1', { kind: 'whisper', residentName: 'Kina' }))).toBe(
      chatEntryReadKey(ENTRIES[4])
    )
  })
})
