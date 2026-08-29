import { useState } from 'react'
import { useAudioStore } from '../stores/audioStore'

interface VolumeControlProps {
  readonly onVolumeChange?: (volume: number) => void
}

export function VolumeControl({ onVolumeChange }: VolumeControlProps): JSX.Element {
  const [open, setOpen] = useState(false)
  const volume = useAudioStore((state) => state.volume)
  const setVolume = useAudioStore((state) => state.setVolume)

  const changeVolume = (next: number): void => {
    const clamped = Math.min(100, Math.max(0, Math.round(next)))
    setVolume(clamped)
    onVolumeChange?.(clamped)
  }

  return (
    <div
      className={`volume-control${open ? ' is-open' : ''}`}
      onWheel={(event) => {
        event.preventDefault()
        changeVolume(volume + (event.deltaY < 0 ? 5 : -5))
      }}
    >
      {open && (
        <div className="volume-slider-wrap">
          <output>{volume}</output>
          <input
            aria-label="全体音量"
            type="range"
            min={0}
            max={100}
            step={1}
            value={volume}
            onChange={(event) => changeVolume(Number(event.currentTarget.value))}
          />
        </div>
      )}
      <button
        type="button"
        aria-label="音量"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
      >
        <svg className="volume-icon" viewBox="0 0 24 24" aria-hidden="true">
          <path d="M4 10v4h4l5 4V6l-5 4H4Z" />
          {volume === 0 ? (
            <path d="m17 9 4 6m0-6-4 6" />
          ) : (
            <path d="M17 9.5c1.2 1.2 1.2 3.8 0 5" />
          )}
        </svg>
      </button>
    </div>
  )
}
