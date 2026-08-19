/**
 * Bridge HTTP client: talks to the local voice-bridge service
 * (http://127.0.0.1:8765 by default, overridable via localStorage
 * `s2s.voice.bridge`).
 */

const DEFAULT_BRIDGE = 'http://127.0.0.1:8765'

/** Resolve the bridge base URL (localStorage override wins). */
export function bridgeBase(): string {
  try {
    return localStorage.getItem('s2s.voice.bridge')?.trim() || DEFAULT_BRIDGE
  } catch {
    return DEFAULT_BRIDGE
  }
}

/** Speech to text: raw 16 kHz mono PCM16 -> { text, language }. */
export async function stt(pcm16: ArrayBuffer): Promise<{ text: string; language?: string }> {
  const resp = await fetch(`${bridgeBase()}/api/stt`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/octet-stream',
      'X-Max-Audio-Sec': '30',
    },
    body: pcm16,
  })
  if (!resp.ok) {
    const body = await resp.text().catch(() => '')
    throw new Error(`voice bridge /api/stt failed: ${resp.status} ${body}`.trim())
  }
  return resp.json() as Promise<{ text: string; language?: string }>
}

/** Text to speech: { text } -> 16 kHz mono PCM16 WAV bytes. */
export async function tts(text: string, signal?: AbortSignal): Promise<ArrayBuffer> {
  const init: RequestInit = {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  }
  if (signal !== undefined) init.signal = signal
  const resp = await fetch(`${bridgeBase()}/api/tts`, init)
  if (!resp.ok) {
    const body = await resp.text().catch(() => '')
    throw new Error(`voice bridge /api/tts failed: ${resp.status} ${body}`.trim())
  }
  return resp.arrayBuffer()
}

/** Digital-human task state surfaced by the bridge. */
export interface DhStatus {
  enabled: boolean
  state: 'idle' | 'tts' | 'generating' | 'done' | 'error' | 'discarded' | string
  message: string
  progress: number
  video_file: string
  video_url: string
  /** 本回复已产出的小段视频（续接播放列表）。 */
  videos: { video_file: string; video_url: string }[]
  total_segments: number
  done_segments: number
  code: string
  text: string
  pending: number
  updated_at: number
}

/**
 * Digital human: submit a finished reply text so the bridge synthesizes it
 * with the same TTS voice and renders a lip-synced talking-head video (DUIX).
 * Resolves with the task code (null on failure); the latest submission
 * replaces any not-yet-started one in the bridge queue.
 */
export function dhSpeak(text: string): Promise<string | null> {
  const body = (text || '').trim()
  if (!body) return Promise.resolve(null)
  return fetch(`${bridgeBase()}/api/dh/speak`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text: body }),
  })
    .then(async (resp) => {
      if (!resp.ok) {
        const b = await resp.text().catch(() => '')
        throw new Error(`/api/dh/speak failed: ${resp.status} ${b}`.trim())
      }
      const json = (await resp.json()) as { code?: string }
      return json.code ?? null
    })
    .catch((err) => {
      console.error('[ui-voice] digital human submit failed:', err)
      return null
    })
}

/** Discard a submitted digital-human task (barge-in / new turn): its result
 *  will never be played. Safe to call with null (no-op). */
export function dhDiscard(code: string | null | undefined): void {
  if (!code) return
  void fetch(`${bridgeBase()}/api/dh/discard`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code }),
  }).catch((err) => console.error('[ui-voice] digital human discard failed:', err))
}

/** Poll the digital-human task state (companion window playback driver). */
export async function dhStatus(): Promise<DhStatus | null> {
  try {
    const resp = await fetch(`${bridgeBase()}/api/dh/status`)
    if (!resp.ok) return null
    return resp.json() as Promise<DhStatus>
  } catch {
    return null
  }
}

/**
 * Streaming silero VAD client for barge-in detection (the server-side VAD of
 * the original speech-to-speech project). While a reply is playing the mic
 * recorder pushes PCM16 chunks here; the bridge replies
 * `{ event: 'speech_start' }` only when a REAL human voice is detected — TTS
 * echo / music / ambient noise never trip it.
 */
export class VadStream {
  private ws: WebSocket | null = null
  private buffered: ArrayBuffer[] = []
  private closed = false
  private connected = false

  /** Whether the VAD socket is actually connected. The recorder falls back to
   *  RMS heuristics while this is false (e.g. an old bridge without /api/vad). */
  get available(): boolean {
    return this.connected
  }

  /**
   * @param onSpeechStart - fired once when silero VAD hears speech.
   */
  open(onSpeechStart: () => void): void {
    if (this.ws !== null) return
    this.closed = false
    const proto = bridgeBase().startsWith('https:') ? 'wss:' : 'ws:'
    const url = `${proto}//${bridgeBase().replace(/^https?:\/\//, '')}/api/vad`
    const ws = new WebSocket(url)
    this.ws = ws
    ws.binaryType = 'arraybuffer'
    ws.onopen = () => {
      if (this.ws !== ws) return
      this.connected = true
      for (const chunk of this.buffered.splice(0)) {
        ws.send(chunk)
      }
    }
    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(String(event.data)) as { event?: string }
        if (msg.event === 'speech_start') onSpeechStart()
      } catch {
        // ignore malformed frames
      }
    }
    ws.onclose = () => {
      if (this.ws === ws) this.ws = null
      this.connected = false
    }
  }

  /** Push one 16 kHz PCM16 chunk (no-op while the socket is down). */
  send(pcm16: ArrayBuffer): void {
    const ws = this.ws
    if (ws === null || this.closed) return
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(pcm16)
    } else if (ws.readyState === WebSocket.CONNECTING) {
      this.buffered.push(pcm16)
      if (this.buffered.length > 64) this.buffered.shift()
    }
  }

  close(): void {
    this.closed = true
    this.buffered = []
    const ws = this.ws
    this.ws = null
    if (ws !== null) {
      try { ws.close() } catch { /* already closed */ }
    }
  }
}
