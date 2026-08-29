export type AnalyserListener = (analyser: AnalyserNode | null) => void

export class AudioService {
  private context: AudioContext | null = null
  private gain: GainNode | null = null
  private source: AudioBufferSourceNode | null = null
  private analyser: AnalyserNode | null = null
  private analyserListener: AnalyserListener | null = null
  private generation = 0
  private volume = 1

  constructor(private readonly createContext: () => AudioContext = () => new AudioContext()) {}

  async resume(): Promise<void> {
    const context = this.ensureContext()
    if (context.state === 'suspended') {
      await context.resume()
    }
  }

  setVolume(volume: number): void {
    this.volume = Math.min(1, Math.max(0, volume))
    if (this.gain) {
      this.gain.gain.value = this.volume
    }
  }

  async play(bytes: Uint8Array, onAnalyser?: AnalyserListener): Promise<void> {
    this.stop()
    const generation = this.generation
    const context = this.ensureContext()
    if (context.state === 'suspended') {
      await context.resume()
    }

    const copy = new ArrayBuffer(bytes.byteLength)
    new Uint8Array(copy).set(bytes)
    const audioBuffer = await context.decodeAudioData(copy)
    if (generation !== this.generation) {
      return
    }

    const source = context.createBufferSource()
    const analyser = context.createAnalyser()
    analyser.fftSize = 2048
    source.buffer = audioBuffer
    source.connect(analyser)
    analyser.connect(this.ensureGain())

    this.source = source
    this.analyser = analyser
    this.analyserListener = onAnalyser ?? null
    this.analyserListener?.(analyser)

    await new Promise<void>((resolve) => {
      source.onended = () => {
        if (this.source === source) {
          this.source = null
          this.analyser = null
          this.analyserListener?.(null)
          this.analyserListener = null
        }
        resolve()
      }
      source.start()
    })
  }

  stop(): void {
    this.generation += 1
    const source = this.source
    this.source = null
    this.analyser = null
    this.analyserListener?.(null)
    this.analyserListener = null
    if (source) {
      try {
        source.stop()
      } catch {
        // Already stopped/ended is equivalent to stopped for presentation.
      }
    }
  }

  dispose(): void {
    this.stop()
    const context = this.context
    this.context = null
    this.gain = null
    if (context) {
      void context.close()
    }
  }

  private ensureContext(): AudioContext {
    if (!this.context) {
      this.context = this.createContext()
    }
    return this.context
  }

  private ensureGain(): GainNode {
    if (!this.gain) {
      const context = this.ensureContext()
      this.gain = context.createGain()
      this.gain.gain.value = this.volume
      this.gain.connect(context.destination)
    }
    return this.gain
  }
}
