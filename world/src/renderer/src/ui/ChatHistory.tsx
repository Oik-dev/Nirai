import { useEffect, useMemo, useRef } from 'react'
import { useSessionStore, type ChatEntry } from '../stores/sessionStore'
import { useUiStore } from '../stores/uiStore'
import {
  chatEntryReadKey,
  chatHistoryViewKey,
  createChatHistoryView,
  filterChatHistoryEntries,
  firstUnreadEntryIndex,
  initializeReadMarkers,
  shouldAutoLoadOlderHistory
} from './chatHistoryView'

function entryLabel(entry: ChatEntry): string {
  if (entry.kind === 'say') return 'あなた'
  if (entry.kind === 'whisper') return `あなた → ${entry.to ?? ''}`
  if (entry.kind === 'resident_whisper') return `${entry.from} → あなた`
  if (entry.kind === 'resident_chat') return `${entry.from} → ${entry.to ?? ''}`
  if (entry.kind === 'task') return '[タスク]'
  if (entry.kind === 'system') return '[お知らせ]'
  return entry.from
}

interface ChatHistoryProps {
  readonly focusedResidentName: string | null
  readonly onLoadOlder: (sessionId: string, before: string) => boolean
}

export function ChatHistory({ focusedResidentName, onLoadOlder }: ChatHistoryProps): JSX.Element | null {
  const historyRef = useRef<HTMLElement>(null)
  const readMarkersRef = useRef(new Map<string, string>())
  const initializedSessionsRef = useRef(new Set<string>())
  const positionedViewRef = useRef<string | null>(null)
  const visibleCountRef = useRef(new Map<string, number>())
  const prependAnchorRef = useRef<{ readonly height: number; readonly top: number } | null>(null)
  const chatActive = useUiStore((state) => state.chatActive)
  const historyOpaque = useUiStore((state) => state.historyOpaque)
  const setHistoryOpaque = useUiStore((state) => state.setHistoryOpaque)
  const activeSessionId = useSessionStore((state) => state.activeSessionId)
  const entries = useSessionStore((state) => state.entries)
  const historyLoadedSessionId = useSessionStore((state) => state.historyLoadedSessionId)
  const hasOlder = useSessionStore((state) => state.hasOlder)
  const olderHistoryCursor = useSessionStore((state) => state.olderHistoryCursor)
  const historyLoading = useSessionStore((state) => state.historyLoading)
  const view = useMemo(() => createChatHistoryView(focusedResidentName), [focusedResidentName])
  const visibleEntries = useMemo(() => filterChatHistoryEntries(entries, view), [entries, view])
  const viewKey = activeSessionId ? chatHistoryViewKey(activeSessionId, view) : null

  const requestOlderHistory = (preserveScrollPosition: boolean): boolean => {
    const element = historyRef.current
    if (
      !element
      || !activeSessionId
      || !hasOlder
      || olderHistoryCursor === null
      || historyLoading
      || entries.length === 0
    ) return false

    if (preserveScrollPosition) {
      prependAnchorRef.current = {
        height: element.scrollHeight,
        top: element.scrollTop
      }
    }
    if (!onLoadOlder(activeSessionId, olderHistoryCursor)) {
      prependAnchorRef.current = null
      return false
    }
    return true
  }

  useEffect(() => {
    if (!activeSessionId || historyLoadedSessionId !== activeSessionId) return
    if (initializedSessionsRef.current.has(activeSessionId)) return
    initializeReadMarkers(activeSessionId, entries, readMarkersRef.current)
    initializedSessionsRef.current.add(activeSessionId)
  }, [activeSessionId, entries, historyLoadedSessionId])

  useEffect(() => {
    if (!chatActive) {
      positionedViewRef.current = null
      return
    }
    if (!viewKey || positionedViewRef.current === viewKey) return
    if (historyLoadedSessionId !== activeSessionId && entries.length === 0) return

    positionedViewRef.current = viewKey
    const frameId = window.requestAnimationFrame(() => {
      const element = historyRef.current
      if (!element) return
      const lastReadKey = readMarkersRef.current.get(viewKey) ?? null
      const firstUnreadIndex = firstUnreadEntryIndex(visibleEntries, lastReadKey)
      if (firstUnreadIndex === null) {
        element.scrollTop = element.scrollHeight
      } else {
        const unreadElement = element.querySelector<HTMLElement>(`[data-history-index="${firstUnreadIndex}"]`)
        if (unreadElement) {
          const containerRect = element.getBoundingClientRect()
          const unreadRect = unreadElement.getBoundingClientRect()
          element.scrollTop += unreadRect.top - containerRect.top - 8
        }
      }
      const lastVisibleEntry = visibleEntries.at(-1)
      if (lastVisibleEntry) {
        readMarkersRef.current.set(viewKey, chatEntryReadKey(lastVisibleEntry))
      }
    })
    return () => window.cancelAnimationFrame(frameId)
  }, [activeSessionId, chatActive, entries.length, historyLoadedSessionId, viewKey, visibleEntries])

  useEffect(() => {
    prependAnchorRef.current = null
  }, [activeSessionId, viewKey])

  useEffect(() => {
    if (historyLoading || !prependAnchorRef.current) return
    const anchor = prependAnchorRef.current
    prependAnchorRef.current = null
    const frameId = window.requestAnimationFrame(() => {
      const element = historyRef.current
      if (!element) return
      element.scrollTop = anchor.top + (element.scrollHeight - anchor.height)
    })
    return () => window.cancelAnimationFrame(frameId)
  }, [entries.length, historyLoading])

  useEffect(() => {
    if (
      !chatActive
      || !viewKey
      || !hasOlder
      || olderHistoryCursor === null
      || historyLoading
      || entries.length === 0
    ) return

    const frameId = window.requestAnimationFrame(() => {
      const element = historyRef.current
      if (!element) return
      if (shouldAutoLoadOlderHistory({
        hasOlder,
        historyLoading,
        visibleEntryCount: visibleEntries.length,
        scrollHeight: element.scrollHeight,
        clientHeight: element.clientHeight
      })) {
        requestOlderHistory(false)
      }
    })
    return () => window.cancelAnimationFrame(frameId)
  }, [
    activeSessionId,
    chatActive,
    entries.length,
    hasOlder,
    historyLoading,
    olderHistoryCursor,
    onLoadOlder,
    viewKey,
    visibleEntries.length
  ])

  useEffect(() => {
    if (!viewKey) return
    const previousCount = visibleCountRef.current.get(viewKey)
    visibleCountRef.current.set(viewKey, visibleEntries.length)
    if (!chatActive || positionedViewRef.current !== viewKey) return
    if (previousCount === undefined || visibleEntries.length <= previousCount) return
    const element = historyRef.current
    if (!element) return
    const nearBottom = element.scrollHeight - element.scrollTop - element.clientHeight < 72
    if (!nearBottom) return
    const frameId = window.requestAnimationFrame(() => {
      const current = historyRef.current
      if (!current) return
      current.scrollTop = current.scrollHeight
      const lastVisibleEntry = visibleEntries.at(-1)
      if (lastVisibleEntry) {
        readMarkersRef.current.set(viewKey, chatEntryReadKey(lastVisibleEntry))
      }
    })
    return () => window.cancelAnimationFrame(frameId)
  }, [chatActive, viewKey, visibleEntries.length])

  if (!chatActive) return null

  return (
    <section
      ref={historyRef}
      className={`chat-history${historyOpaque ? ' is-opaque' : ''}`}
      aria-label={focusedResidentName ? `${focusedResidentName}とのWhisper履歴` : 'World Chat履歴'}
      onPointerDown={() => setHistoryOpaque(true)}
      onScroll={(event) => {
        const element = event.currentTarget
        if (element.scrollTop > 32) return
        requestOlderHistory(true)
      }}
    >
      {visibleEntries.map((entry, index) => (
        <p
          key={`${entry.request_id ?? entry.ts}-${entry.kind}-${index}`}
          data-history-index={index}
        >
          <strong>{entryLabel(entry)}:</strong> {entry.text}
        </p>
      ))}
    </section>
  )
}
