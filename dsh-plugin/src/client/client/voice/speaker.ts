/**
 * ReplySpeaker: plays synthesized reply WAVs through an AudioContext as a
 * FIFO queue (sentence streaming). `speak()` enqueues; the drain plays one
 * clip at a time and `onended` advances to the next, so sentence N+1 starts
 * the moment N finishes (gaps between clips are tiny). `speaking` stays true
 * while anything is queued or playing, so the companion window and the mic
 * echo-guard follow the whole reply, not individual clips.
 *
 * Interruption: `stop()` halts the current clip and clears the queue; a
 * generation counter discards clips that were queued but not yet started.
 */
export class ReplySpeaker {
  private ctx: AudioContext | null = null
  private queue: ArrayBuffer[] = []
  private playing: AudioBufferSourceNode | null = null
  private drainRunning = false
  private gen = 0
  private disposed = false
  private listeners = new Set<() => void>()

  /** True while a reply is being read (playing or waiting in the queue). */
  get speaking(): boolean {
    return this.playing !== null || this.queue.length > 0
  }

  /** Subscribe to speaking-state changes; returns the unsubscribe function. */
  subscribe(listener: () => void): () => void {
    this.listeners.add(listener)
    return () => {
      this.listeners.delete(listener)
    }
  }

  private emit(): void {
    for (const listener of this.listeners) listener()
  }

  /** Enqueue one clip for playback (order preserved; plays after earlier clips). */
  speak(wav: ArrayBuffer): void {
    if (this.disposed) return
    this.queue.push(wav)
    this.emit()
    void this.drain()
  }

  private async drain(): Promise<void> {
    if (this.drainRunning) return
    this.drainRunning = true
    try {
      const ctx = this.ctx ?? (this.ctx = new AudioContext())
      if (ctx.state === 'suspended') {
        try {
          await ctx.resume()
        } catch {
          // best-effort
        }
      }
      while (this.queue.length > 0 && !this.disposed) {
        const wav = this.queue.shift()!
        await this.playOne(ctx, wav)
      }
    } finally {
      this.drainRunning = false
      this.emit()
    }
  }

  private playOne(ctx: AudioContext, wav: ArrayBuffer): Promise<void> {
    const gen = this.gen
    return new Promise((resolve) => {
      void ctx.decodeAudioData(wav)
        .then((buffer) => {
          if (this.disposed || gen !== this.gen) {
            resolve()
            return
          }
          const source = ctx.createBufferSource()
          source.buffer = buffer
          source.connect(ctx.destination)
          this.playing = source
          this.emit()
          source.onended = () => {
            if (this.playing === source) {
              this.playing = null
              this.emit()
            }
            resolve()
          }
          source.start()
        })
        .catch(() => resolve())
    })
  }

  /** Stop the current clip immediately and clear the queued ones. */
  stop(): void {
    this.gen += 1
    if (this.playing !== null) {
      try {
        this.playing.stop()
      } catch {
        // already stopped
      }
      this.playing = null
    }
    this.queue = []
    this.emit()
  }

  /** Release the AudioContext (plugin teardown). */
  dispose(): void {
    this.disposed = true
    this.stop()
    if (this.ctx !== null) {
      void this.ctx.close().catch(() => {})
      this.ctx = null
    }
    this.listeners.clear()
  }
}
