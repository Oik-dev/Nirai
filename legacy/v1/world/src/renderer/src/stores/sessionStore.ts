import { create } from 'zustand'

export type ChatEntryKind =
  | 'say'
  | 'whisper'
  | 'resident_say'
  | 'resident_whisper'
  | 'resident_chat'
  | 'holo_say'
  | 'task'
  | 'system'

export interface ChatEntry {
  readonly entry_id?: string
  readonly ts: string
  readonly kind: ChatEntryKind
  readonly from: string
  readonly to?: string
  readonly text: string
  readonly session: string
  readonly request_id?: string
  readonly task_id?: string
  readonly agent_session_id?: string
}

export interface ChatSessionSummary {
  readonly id: string
  readonly title: string
  readonly created_at: string
  readonly updated_at: string
}

interface SessionState {
  sessions: readonly ChatSessionSummary[]
  activeSessionId: string | null
  entries: readonly ChatEntry[]
  entryKeys: Set<string>
  hasOlder: boolean
  olderHistoryCursor: string | null
  historyLoading: boolean
  historyLoadedSessionId: string | null
  historyRefreshSessionId: string | null
  historyRefreshBaselineKeys: Set<string> | null
  setSessionList: (sessions: readonly ChatSessionSummary[], activeSessionId: string | null) => void
  setHistory: (
    sessionId: string,
    entries: readonly ChatEntry[],
    nextBefore: string | null
  ) => void
  beginHistoryRefresh: (sessionId: string) => boolean
  cancelHistoryRefresh: () => void
  beginOlderHistoryLoad: (sessionId: string) => boolean
  cancelHistoryLoad: () => void
  appendEntry: (entry: ChatEntry) => void
}

export function chatEntryKey(entry: ChatEntry): string {
  if (entry.entry_id) return JSON.stringify([entry.session, entry.entry_id])
  return JSON.stringify([
    entry.session,
    entry.ts,
    entry.kind,
    entry.from,
    entry.to ?? '',
    entry.request_id ?? '',
    entry.task_id ?? '',
    entry.agent_session_id ?? '',
    entry.text
  ])
}

function deduplicateChatEntries(entries: readonly ChatEntry[]): readonly ChatEntry[] {
  const seen = new Set<string>()
  const unique: ChatEntry[] = []
  for (const entry of entries) {
    const key = chatEntryKey(entry)
    if (seen.has(key)) continue
    seen.add(key)
    unique.push(entry)
  }
  return unique
}

export const useSessionStore = create<SessionState>((set, get) => ({
  sessions: [],
  activeSessionId: null,
  entries: [],
  entryKeys: new Set<string>(),
  hasOlder: false,
  olderHistoryCursor: null,
  historyLoading: false,
  historyLoadedSessionId: null,
  historyRefreshSessionId: null,
  historyRefreshBaselineKeys: null,
  setSessionList: (sessions, activeSessionId) => set((current) => {
    const sameSession = current.activeSessionId === activeSessionId
    return {
      sessions,
      activeSessionId,
      entries: sameSession ? current.entries : [],
      entryKeys: sameSession ? current.entryKeys : new Set<string>(),
      hasOlder: sameSession ? current.hasOlder : false,
      olderHistoryCursor: sameSession ? current.olderHistoryCursor : null,
      historyLoading: sameSession ? current.historyLoading : false,
      historyLoadedSessionId: sameSession ? current.historyLoadedSessionId : null,
      historyRefreshSessionId: sameSession ? current.historyRefreshSessionId : null,
      historyRefreshBaselineKeys: sameSession ? current.historyRefreshBaselineKeys : null
    }
  }),
  setHistory: (sessionId, entries, nextBefore) => set((current) => {
    if (current.activeSessionId !== sessionId) return current

    const uniqueEntries = deduplicateChatEntries(entries)
    const loadingOlder = current.historyLoading
      && current.historyLoadedSessionId === sessionId
    if (!loadingOlder) {
      const historyKeys = new Set(uniqueEntries.map(chatEntryKey))
      const refreshBaseline = current.historyRefreshSessionId === sessionId
        ? current.historyRefreshBaselineKeys
        : null
      const liveAfterRefresh = refreshBaseline === null
        ? []
        : current.entries.filter((entry) => {
            const key = chatEntryKey(entry)
            return !refreshBaseline.has(key) && !historyKeys.has(key)
          })
      const mergedEntries = liveAfterRefresh.length > 0
        ? [...uniqueEntries, ...liveAfterRefresh]
        : uniqueEntries
      return {
        entries: mergedEntries,
        entryKeys: new Set(mergedEntries.map(chatEntryKey)),
        hasOlder: nextBefore !== null,
        olderHistoryCursor: nextBefore,
        historyLoading: false,
        historyLoadedSessionId: sessionId,
        historyRefreshSessionId: null,
        historyRefreshBaselineKeys: null
      }
    }

    const olderEntries = uniqueEntries.filter((entry) => !current.entryKeys.has(chatEntryKey(entry)))
    for (const entry of olderEntries) current.entryKeys.add(chatEntryKey(entry))
    return {
      entries: [...olderEntries, ...current.entries],
      entryKeys: current.entryKeys,
      hasOlder: nextBefore !== null,
      olderHistoryCursor: nextBefore,
      historyLoading: false,
      historyLoadedSessionId: sessionId
    }
  }),
  beginHistoryRefresh: (sessionId) => {
    const current = get()
    if (current.activeSessionId !== sessionId) return false
    set({
      historyLoading: false,
      historyRefreshSessionId: sessionId,
      historyRefreshBaselineKeys: new Set(current.entryKeys)
    })
    return true
  },
  cancelHistoryRefresh: () => set({
    historyRefreshSessionId: null,
    historyRefreshBaselineKeys: null
  }),
  beginOlderHistoryLoad: (sessionId) => {
    const current = get()
    if (
      current.activeSessionId !== sessionId
      || current.historyLoadedSessionId !== sessionId
      || current.historyLoading
      || !current.hasOlder
      || current.olderHistoryCursor === null
      || current.entries.length === 0
    ) {
      return false
    }
    set({ historyLoading: true })
    return true
  },
  cancelHistoryLoad: () => set({ historyLoading: false }),
  appendEntry: (entry) => set((current) => {
    if (current.activeSessionId !== entry.session) return current
    const key = chatEntryKey(entry)
    if (current.entryKeys.has(key)) return current
    current.entryKeys.add(key)
    return {
      entries: [...current.entries, entry],
      entryKeys: current.entryKeys
    }
  })
}))
