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
