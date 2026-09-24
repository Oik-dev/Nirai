import { useEffect, useMemo, useRef } from 'react'
import { chatEntryKey, useSessionStore } from '../stores/sessionStore'
import { useUiStore } from '../stores/uiStore'
import { MarkdownContent } from './AgentMarkdown'
import {
  captureChatHistoryScrollState,
  chatEntryLabel,
  chatEntryReadKey,
  chatHistoryViewKey,
  createChatHistoryView,
  filterChatHistoryEntries,
  firstUnreadEntryIndex,
  initializeReadMarkers,
  isWhisperChatEntry,
  restoreChatHistoryScrollTop,
  shouldAutoLoadOlderHistory,
  type ChatHistoryScrollState
} from './chatHistoryView'

interface ChatHistoryProps {
  readonly focusedResidentName: string | null
  readonly onLoadOlder: (sessionId: string, before: string) => boolean
}

function whenHistoryHasLayout(
  getElement: () => HTMLElement | null,
  callback: (element: HTMLElement) => void
): () => void {
  let frameId = 0
  let observer: ResizeObserver | null = null
  let disposed = false
  let completed = false

  const cleanup = (): void => {
    if (frameId) {
      window.cancelAnimationFrame(frameId)
      frameId = 0
    }
    observer?.disconnect()
    observer = null
  }

  const tryComplete = (): boolean => {
    if (disposed || completed) return true
    const element = getElement()
    if (!element || element.clientHeight <= 0 || element.scrollHeight <= 0) return false
    completed = true
    cleanup()
    callback(element)
    return true
  }

  const scheduleFallback = (): void => {
    if (disposed || completed || frameId) return
    frameId = window.requestAnimationFrame(() => {
      frameId = 0
      if (!tryComplete()) scheduleFallback()
    })
  }

  if (!tryComplete()) {
    const element = getElement()
    if (element && typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(() => { void tryComplete() })
      observer.observe(element)
      // ResizeObserver is the normal browser path. One rAF also covers a
      // layout that becomes measurable before the first observer delivery.
      frameId = window.requestAnimationFrame(() => {
        frameId = 0
        void tryComplete()
      })
    } else {
      // Test/legacy environments may not expose ResizeObserver. Keep retrying
      // until the owning React effect is cleaned up rather than silently giving
      // up after a few hidden 0x0 frames.
      scheduleFallback()
    }
  }

  return () => {
    disposed = true
    cleanup()
  }
}

export function ChatHistory({ focusedResidentName, onLoadOlder }: ChatHistoryProps): JSX.Element | null {
  const historyRef = useRef<HTMLElement>(null)
  const readMarkersRef = useRef(new Map<string, string>())
  const initializedSessionsRef = useRef(new Set<string>())
  const activePositionedViewRef = useRef<string | null>(null)
  const scrollStatesRef = useRef(new Map<string, ChatHistoryScrollState>())
  const visibleCountRef = useRef(new Map<string, number>())
  const autoLoadedPageCountRef = useRef(new Map<string, number>())
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
      // `hidden` removes the Chat surface from layout. Force one explicit
      // restore pass when it becomes visible again instead of treating the
      // still-mounted DOM as already positioned.
      activePositionedViewRef.current = null
      return
    }
    if (!viewKey || activePositionedViewRef.current === viewKey) return
    if (historyLoadedSessionId !== activeSessionId && entries.length === 0) return

    // New entries may have arrived while Work/Tasks was visible. The restore
    // pass below owns that transition, so establish the active baseline before
    // the ordinary live-append effect runs.
    visibleCountRef.current.set(viewKey, visibleEntries.length)
    return whenHistoryHasLayout(() => historyRef.current, (element) => {
      const savedScroll = scrollStatesRef.current.get(viewKey)
      if (savedScroll) {
        element.scrollTop = restoreChatHistoryScrollTop(
          savedScroll,
          element.scrollHeight,
          element.clientHeight
        )
        if (savedScroll.pinnedToBottom) {
          const lastVisibleEntry = visibleEntries.at(-1)
          if (lastVisibleEntry) {
            readMarkersRef.current.set(viewKey, chatEntryReadKey(lastVisibleEntry))
          }
        }
      } else {
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
      }
      scrollStatesRef.current.set(viewKey, captureChatHistoryScrollState(
        element.scrollTop,
        element.scrollHeight,
        element.clientHeight
      ))
      activePositionedViewRef.current = viewKey
    })
  }, [activeSessionId, chatActive, entries.length, historyLoadedSessionId, viewKey, visibleEntries])

  useEffect(() => {
    prependAnchorRef.current = null
  }, [activeSessionId, viewKey])

  useEffect(() => {
    // Older-history loading may finish while Work/Tasks hides the Chat surface.
    // Keep the anchor intact until Chat has real layout again; consuming it at
    // display:none would turn zero dimensions into a bogus restore position.
    if (!chatActive || historyLoading || !prependAnchorRef.current) return
    const anchor = prependAnchorRef.current
    return whenHistoryHasLayout(() => historyRef.current, (element) => {
      // The anchor can be cleared by a session/view change while this rAF chain
      // is waiting for layout. Never apply a stale anchor to the new view.
      if (prependAnchorRef.current !== anchor) return
      element.scrollTop = anchor.top + (element.scrollHeight - anchor.height)
      prependAnchorRef.current = null
      if (viewKey) {
        scrollStatesRef.current.set(viewKey, captureChatHistoryScrollState(
          element.scrollTop,
          element.scrollHeight,
          element.clientHeight
        ))
      }
    })
  }, [chatActive, entries.length, historyLoading, viewKey])

  useEffect(() => {
    if (
      !chatActive
      || !viewKey
      || !hasOlder
      || olderHistoryCursor === null
      || historyLoading
      || entries.length === 0
    ) return

    return whenHistoryHasLayout(() => historyRef.current, (element) => {
      // A manual prepend restore owns this frame. Do not start another history
      // request until that anchor has been applied to the visible layout.
      if (prependAnchorRef.current) return
      const autoLoadedPageCount = autoLoadedPageCountRef.current.get(viewKey) ?? 0
      if (shouldAutoLoadOlderHistory({
        hasOlder,
        historyLoading,
        visibleEntryCount: visibleEntries.length,
        scrollHeight: element.scrollHeight,
        clientHeight: element.clientHeight,
        autoLoadedPageCount
      }) && requestOlderHistory(false)) {
        autoLoadedPageCountRef.current.set(viewKey, autoLoadedPageCount + 1)
      }
    })
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
    if (!viewKey || !chatActive) return
    const previousCount = visibleCountRef.current.get(viewKey)
    visibleCountRef.current.set(viewKey, visibleEntries.length)
    if (activePositionedViewRef.current !== viewKey) return
    if (previousCount === undefined || visibleEntries.length <= previousCount) return
    const element = historyRef.current
    if (!element) return
    const nearBottom = element.scrollHeight - element.scrollTop - element.clientHeight < 72
    if (!nearBottom) return
    const frameId = window.requestAnimationFrame(() => {
      const current = historyRef.current
      if (!current) return
      current.scrollTop = current.scrollHeight
      scrollStatesRef.current.set(viewKey, captureChatHistoryScrollState(
        current.scrollTop,
        current.scrollHeight,
        current.clientHeight
      ))
      const lastVisibleEntry = visibleEntries.at(-1)
      if (lastVisibleEntry) {
        readMarkersRef.current.set(viewKey, chatEntryReadKey(lastVisibleEntry))
      }
    })
    return () => window.cancelAnimationFrame(frameId)
  }, [chatActive, viewKey, visibleEntries.length])

  return (
    <section
      ref={historyRef}
      hidden={!chatActive}
      className={`chat-history${historyOpaque ? ' is-opaque' : ''}`}
      aria-label={focusedResidentName ? `${focusedResidentName}とのWhisper履歴` : 'World Chat履歴'}
      onPointerDown={() => setHistoryOpaque(true)}
      onScroll={(event) => {
        const element = event.currentTarget
        if (!chatActive || element.clientHeight <= 0 || element.scrollHeight <= 0) return
        if (viewKey) {
          scrollStatesRef.current.set(viewKey, captureChatHistoryScrollState(
            element.scrollTop,
            element.scrollHeight,
            element.clientHeight
          ))
        }
        if (element.scrollTop > 32) return
        requestOlderHistory(true)
      }}
    >
      {hasOlder && olderHistoryCursor !== null && (
        <button
          type="button"
          className="chat-history-load-older"
          disabled={historyLoading}
          onClick={() => requestOlderHistory(false)}
        >
          {historyLoading ? '履歴を読み込み中…' : 'さらに古い履歴を読み込む'}
        </button>
      )}
      {visibleEntries.map((entry, index) => (
        <article
          className="chat-history-entry"
          key={chatEntryKey(entry)}
          data-history-index={index}
        >
          <strong className={isWhisperChatEntry(entry) ? 'chat-history-whisper-speaker' : undefined}>
            {chatEntryLabel(entry)}:
          </strong>
          <MarkdownContent text={entry.text} className="chat-history-markdown" />
        </article>
      ))}
    </section>
  )
}
