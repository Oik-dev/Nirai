import { useEffect, useRef } from 'react'
import type { SceneRuntime } from '../runtime/SceneRuntime'

interface ResidentSpeechBubbleProps {
  readonly runtime: SceneRuntime | null
  readonly residentName: string
  readonly text: string
}

const MAX_BUBBLE_TEXT_LENGTH = 96

export function formatResidentBubbleText(text: string): string {
  const compact = text.replace(/\s+/g, ' ').trim()
  if (compact.length <= MAX_BUBBLE_TEXT_LENGTH) return compact
  return `${compact.slice(0, MAX_BUBBLE_TEXT_LENGTH - 1)}…`
}

export function ResidentSpeechBubble({
  runtime,
  residentName,
  text
}: ResidentSpeechBubbleProps): JSX.Element {
  const bubbleRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!runtime) return
    let frameId = 0

    const updatePosition = (): void => {
      const element = bubbleRef.current
      if (!element) return
      const anchor = runtime.getResidentScreenAnchor(residentName)
      if (!anchor || !anchor.visible) {
        element.style.visibility = 'hidden'
      } else {
        element.style.visibility = 'visible'
        element.style.transform = `translate3d(${anchor.x}px, ${anchor.y}px, 0) translate(-50%, calc(-100% - 1.1rem))`
      }
      frameId = window.requestAnimationFrame(updatePosition)
    }

    frameId = window.requestAnimationFrame(updatePosition)
    return () => window.cancelAnimationFrame(frameId)
  }, [residentName, runtime])

  return (
    <div
      ref={bubbleRef}
      className="resident-speech-bubble"
      role="status"
      aria-live="polite"
      title={text}
    >
      {formatResidentBubbleText(text)}
    </div>
  )
}
