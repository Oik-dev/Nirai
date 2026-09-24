import { chatEntryKey, type ChatEntry } from '../stores/sessionStore'

export const MAX_AUTO_HISTORY_PAGES = 10

export type ChatHistoryView =
  | { readonly kind: 'world' }
  | { readonly kind: 'whisper'; readonly residentName: string }

export interface ChatHistoryScrollState {
  readonly top: number
  readonly pinnedToBottom: boolean
}

export function captureChatHistoryScrollState(
  scrollTop: number,
  scrollHeight: number,
  clientHeight: number,
  threshold = 72
): ChatHistoryScrollState {
  return {
    top: Math.max(0, scrollTop),
    // A hidden/display:none history reports zero layout dimensions. Never
    // reinterpret that non-layout state as "following the bottom".
    pinnedToBottom: clientHeight > 0
      && scrollHeight > 0
      && scrollHeight - scrollTop - clientHeight < threshold
  }
}

export function restoreChatHistoryScrollTop(
  state: ChatHistoryScrollState,
  scrollHeight: number,
  clientHeight: number
): number {
  if (state.pinnedToBottom) return Math.max(0, scrollHeight)
  return Math.min(Math.max(0, state.top), Math.max(0, scrollHeight - clientHeight))
}

export function createChatHistoryView(focusedResidentName: string | null): ChatHistoryView {
  return focusedResidentName
    ? { kind: 'whisper', residentName: focusedResidentName }
    : { kind: 'world' }
}

export function chatHistoryViewKey(sessionId: string, view: ChatHistoryView): string {
  return view.kind === 'world'
    ? `${sessionId}|world`
    : `${sessionId}|whisper:${view.residentName}`
}

export function chatEntryLabel(entry: ChatEntry): string {
  if (entry.kind === 'say') return 'あなた'
  if (entry.kind === 'whisper') return 'あなた'
  if (entry.kind === 'resident_whisper') return entry.from
  if (entry.kind === 'resident_chat') return `${entry.from} → ${entry.to ?? ''}`
  if (entry.kind === 'holo_say') return entry.to ? `${entry.from} → ${entry.to}` : entry.from
  if (entry.kind === 'task') return entry.from
  if (entry.kind === 'system') return '[お知らせ]'
  return entry.from
}

export function isWhisperChatEntry(entry: ChatEntry): boolean {
  return entry.kind === 'whisper' || entry.kind === 'resident_whisper'
}

export function isWorldPresentationEntry(entry: ChatEntry): boolean {
  if (entry.kind === 'task') {
    // Completed Agent work returns to World as the Resident's own report.
    // Failure/interruption/cancellation text is Core-authored lifecycle status,
    // so keep those in logs/Work rather than making the avatar speak them.
    return !/^(?:Task失敗|Task停止|Task中断):/.test(entry.text)
  }
  return entry.kind === 'resident_say'
    || entry.kind === 'resident_chat'
    || entry.kind === 'holo_say'
}

export function filterChatHistoryEntries(
  entries: readonly ChatEntry[],
  view: ChatHistoryView
): readonly ChatEntry[] {
  if (view.kind === 'world') {
    return entries.filter((entry) => !['whisper', 'resident_whisper'].includes(entry.kind))
  }

  return entries.filter((entry) => (
    (entry.kind === 'whisper' && entry.to === view.residentName)
    || (entry.kind === 'resident_whisper' && entry.from === view.residentName)
  ))
}

export function shouldAutoLoadOlderHistory(options: {
  readonly hasOlder: boolean
  readonly historyLoading: boolean
  readonly visibleEntryCount: number
  readonly scrollHeight: number
  readonly clientHeight: number
  readonly autoLoadedPageCount?: number
}): boolean {
  if (!options.hasOlder || options.historyLoading) return false
  if ((options.autoLoadedPageCount ?? 0) >= MAX_AUTO_HISTORY_PAGES) return false
  return options.visibleEntryCount === 0
    || options.scrollHeight <= options.clientHeight + 1
}

export function chatEntryReadKey(entry: ChatEntry): string {
  return chatEntryKey(entry)
}

export function firstUnreadEntryIndex(
  entries: readonly ChatEntry[],
  lastReadKey: string | null
): number | null {
  if (entries.length === 0) return null
  if (lastReadKey === null) return 0
  const readIndex = entries.findIndex((entry) => chatEntryReadKey(entry) === lastReadKey)
  if (readIndex < 0) return 0
  return readIndex + 1 < entries.length ? readIndex + 1 : null
}

export function initializeReadMarkers(
  sessionId: string,
  entries: readonly ChatEntry[],
  markers: Map<string, string>
): void {
  const worldEntries = filterChatHistoryEntries(entries, { kind: 'world' })
  const worldLast = worldEntries.at(-1)
  if (worldLast) markers.set(chatHistoryViewKey(sessionId, { kind: 'world' }), chatEntryReadKey(worldLast))

  const whisperNames = new Set<string>()
  for (const entry of entries) {
    if (entry.kind === 'whisper' && entry.to) whisperNames.add(entry.to)
    if (entry.kind === 'resident_whisper') whisperNames.add(entry.from)
  }
  for (const residentName of whisperNames) {
    const view = { kind: 'whisper', residentName } as const
    const last = filterChatHistoryEntries(entries, view).at(-1)
    if (last) markers.set(chatHistoryViewKey(sessionId, view), chatEntryReadKey(last))
  }
}
