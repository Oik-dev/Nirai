import { useEffect, useRef, useState } from 'react'
import { useAudioStore } from '../stores/audioStore'
import { useConnectionStore } from '../stores/connectionStore'
import { useResidentStore } from '../stores/residentStore'
import { useUiStore } from '../stores/uiStore'
import {
  completeResidentMention,
  parseChatInput,
  residentMentionCandidates
} from './chatInput'

interface ChatBarProps {
  readonly focusedResidentName: string | null
  readonly onSend: (text: string, requestId: string) => boolean
  readonly onSendTask: (text: string, requestId: string) => boolean
  readonly onSendWhisper: (to: string, text: string, requestId: string) => boolean
  readonly onCancel: (requestId: string) => boolean
}

export function ChatBar({
  focusedResidentName,
  onSend,
  onSendTask,
  onSendWhisper,
  onCancel
}: ChatBarProps): JSX.Element {
  const [text, setText] = useState('')
  const [isComposing, setIsComposing] = useState(false)
  const [sendError, setSendError] = useState<string | null>(null)
  const [mentionIndex, setMentionIndex] = useState(0)
  const [mentionDismissed, setMentionDismissed] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const chatActive = useUiStore((state) => state.chatActive)
  const setChatActive = useUiStore((state) => state.setChatActive)
  const connected = useConnectionStore((state) => state.status === 'connected')
  const activeRequestId = useConnectionStore((state) => state.activeRequestId)
  const activeSpeechRequestId = useAudioStore((state) => state.activeSpeechRequestId)
  const residentNames = useResidentStore((state) => state.residents.map((resident) => resident.name))
  const stoppableRequestId = activeRequestId ?? activeSpeechRequestId
  const mentionCandidates = mentionDismissed ? [] : residentMentionCandidates(text, residentNames)
  const selectedMentionIndex = mentionCandidates.length === 0
    ? 0
    : Math.min(mentionIndex, mentionCandidates.length - 1)

  useEffect(() => {
    const textarea = textareaRef.current
    if (!textarea) return
    textarea.style.height = 'auto'
    textarea.style.height = `${textarea.scrollHeight}px`
  }, [text])

  useEffect(() => {
    setMentionIndex(0)
  }, [text])

  const completeMention = (residentName: string): void => {
    setText(completeResidentMention(residentName))
    setMentionDismissed(true)
    setMentionIndex(0)
    setSendError(null)
    requestAnimationFrame(() => textareaRef.current?.focus())
  }

  const send = (): void => {
    const trimmed = text.trim()
    if (!connected || stoppableRequestId !== null || !trimmed) return
    if (trimmed === '/task' || trimmed.startsWith('/task ')) {
      const taskText = trimmed.slice('/task'.length).trim()
      if (!taskText) {
        setSendError('/task の後に作業内容を入力してください')
        return
      }
      const requestId = crypto.randomUUID()
      if (!onSendTask(taskText, requestId)) return
      setSendError(null)
      setText('')
      return
    }
    const parsed = parseChatInput(trimmed, residentNames, focusedResidentName)
    if (parsed.kind === 'invalid-whisper') {
      setSendError('Whisper先のResidentが見つかりません')
      return
    }
    const requestId = crypto.randomUUID()
    const sent = parsed.kind === 'whisper'
      ? onSendWhisper(parsed.to, parsed.text, requestId)
      : onSend(parsed.text, requestId)
    if (!sent) return
    useConnectionStore.setState({ activeRequestId: requestId })
    setSendError(null)
    setText('')
  }

  const cancel = (): void => {
    if (!connected || stoppableRequestId === null) return
    onCancel(stoppableRequestId)
  }

  return (
    <div className={`chat-bar${chatActive ? ' is-active' : ''}`}>
      {focusedResidentName && (
        <span className="chat-whisper-target">Whisper → {focusedResidentName}</span>
      )}
      {mentionCandidates.length > 0 && (
        <div className="chat-mention-menu" role="listbox" aria-label="Resident候補">
          {mentionCandidates.map((name, index) => (
            <button
              key={name}
              type="button"
              role="option"
              aria-selected={index === selectedMentionIndex}
              onPointerDown={(event) => event.preventDefault()}
              onClick={() => completeMention(name)}
            >
              @{name}
            </button>
          ))}
        </div>
      )}
      <textarea
        ref={textareaRef}
        aria-label={connected
          ? focusedResidentName ? `${focusedResidentName}へのWhisper入力` : 'メッセージ入力'
          : 'Core接続待ち'}
        placeholder={focusedResidentName ? `${focusedResidentName}へWhisper...` : 'メッセージ...'}
        rows={1}
        value={text}
        onFocus={() => setChatActive(true)}
        onCompositionStart={() => setIsComposing(true)}
        onCompositionEnd={() => setIsComposing(false)}
        onKeyDown={(event) => {
          if (event.nativeEvent.isComposing || isComposing) return
          if (mentionCandidates.length > 0) {
            if (event.key === 'ArrowDown') {
              event.preventDefault()
              setMentionIndex((current) => (current + 1) % mentionCandidates.length)
              return
            }
            if (event.key === 'ArrowUp') {
              event.preventDefault()
              setMentionIndex((current) => (current - 1 + mentionCandidates.length) % mentionCandidates.length)
              return
            }
            if (event.key === 'Enter' || event.key === 'Tab') {
              event.preventDefault()
              completeMention(mentionCandidates[selectedMentionIndex])
              return
            }
            if (event.key === 'Escape') {
              event.preventDefault()
              setMentionDismissed(true)
              return
            }
          }
          if (event.key !== 'Enter' || event.shiftKey) return
          event.preventDefault()
          send()
        }}
        onChange={(event) => {
          setText(event.currentTarget.value)
          setMentionDismissed(false)
          setSendError(null)
        }}
      />
      <button
        type="button"
        aria-label={stoppableRequestId === null ? '送信' : '応答を停止'}
        disabled={!connected || (stoppableRequestId === null && !text.trim())}
        onClick={stoppableRequestId === null ? send : cancel}
      >
        {stoppableRequestId === null ? '↑' : '■'}
      </button>
      {sendError && <p className="chat-input-error" role="alert">{sendError}</p>}
    </div>
  )
}
