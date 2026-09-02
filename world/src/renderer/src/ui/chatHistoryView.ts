import type { ChatEntry } from '../stores/sessionStore'

export type ChatHistoryView =
  | { readonly kind: 'world' }
  | { readonly kind: 'whisper'; readonly residentName: string }

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
  if (entry.kind === 'task') return '[タスク]'
  if (entry.kind === 'system') return '[お知らせ]'
  return entry.from
}

export function isWhisperChatEntry(entry: ChatEntry): boolean {
  return entry.kind === 'whisper' || entry.kind === 'resident_whisper'
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
}): boolean {
  if (!options.hasOlder || options.historyLoading) return false
  return options.visibleEntryCount === 0
    || options.scrollHeight <= options.clientHeight + 1
}

export function chatEntryReadKey(entry: ChatEntry): string {
  return [
    entry.ts,
    entry.kind,
    entry.from,
    entry.to ?? '',
    entry.request_id ?? ''
  ].join('|')
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
