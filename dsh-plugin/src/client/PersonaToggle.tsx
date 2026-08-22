/**
 * PersonaToggle: three independent preset switchers for the voice stack —
 * TTS voice, digital-human avatar, and companion idle animation group.
 *
 * Each is a small toolbar button that cycles through the bridge's preset
 * list on click (voice → next voice, avatar → next avatar, idle → next idle).
 * Selections persist in localStorage (`s2s.voice.persona.*`) and are restored
 * by POSTing them to the bridge on mount. The bridge applies voice hot-swap
 * (next TTS uses the new ref), avatar (next DUIX submission uses the new
 * video), and idle (companion bg list switches immediately).
 */
import { memo, useCallback, useEffect, useRef, useState } from 'react'
import type { PropsLocale, PropsRuntime } from '@deepseek-ai/dsh-client-ui-slots'
// Type-only: pulls ui-conversation's SlotMap merge for PropsRuntime resolution.
import type {} from '@deepseek-ai/dsh-client-ui-conversation/client'
import { bridgeBase } from './bridge.ts'
import type { VoiceInjected } from './contract.ts'
import css from './PersonaToggle.module.css'

const PERSIST = {
  voice: 's2s.voice.persona.voice',
  avatar: 's2s.voice.persona.avatar',
  idle: 's2s.voice.persona.idle',
}

interface PersonaState {
  voices: { name: string; label: string }[]
  avatars: { name: string; label: string }[]
  idles: { name: string; label: string }[]
  current: { voice: string; avatar: string; idle: string }
}

function readSaved(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

function writeSaved(key: string, value: string): void {
  try {
    localStorage.setItem(key, value)
  } catch {
    // ignore
  }
}

/** Small SVG glyphs (inline, follow currentColor). */
function VoiceIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 2a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z" />
      <path d="M19 10v1a7 7 0 0 1-14 0v-1" />
      <line x1="12" y1="18" x2="12" y2="22" />
    </svg>
  )
}

function AvatarIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
      <circle cx="12" cy="7" r="4" />
    </svg>
  )
}

function IdleIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="2" y="4" width="20" height="16" rx="2" />
      <path d="M2 9h20" />
      <circle cx="6" cy="13" r="1.2" />
      <circle cx="10" cy="13" r="1.2" />
    </svg>
  )
}

export const PersonaToggle = memo(function PersonaToggle({ t }: PropsRuntime<'conversation.input.dock'> & PropsLocale<'voice'> & VoiceInjected) {
  const [state, setState] = useState<PersonaState | null>(null)
  const [applied, setApplied] = useState(false)
  const appliedRef = useRef(false)

  // Load the preset list once.
  useEffect(() => {
    let cancelled = false
    void fetch(`${bridgeBase()}/api/persona/list`)
      .then(r => r.json() as Promise<PersonaState>)
      .then((s) => {
        if (cancelled) return
        setState(s)
        setApplied(true)
      })
      .catch(err => console.error('[ui-voice] persona list failed:', err))
    return () => { cancelled = true }
  }, [])

  // Restore persisted selections on first load.
  useEffect(() => {
    if (!applied || appliedRef.current || state === null) return
    appliedRef.current = true
    const savedVoice = readSaved(PERSIST.voice)
    const savedAvatar = readSaved(PERSIST.avatar)
    const savedIdle = readSaved(PERSIST.idle)
    const patch: Record<string, string> = {}
    if (savedVoice !== null && state.voices.some(v => v.name === savedVoice)) patch.voice = savedVoice
    if (savedAvatar !== null && state.avatars.some(a => a.name === savedAvatar)) patch.avatar = savedAvatar
    if (savedIdle !== null && state.idles.some(i => i.name === savedIdle)) patch.idle = savedIdle
    if (Object.keys(patch).length === 0) return
    void fetch(`${bridgeBase()}/api/persona/set`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    })
      .then(() => {
        // Refresh the list so current reflects the restore.
        return fetch(`${bridgeBase()}/api/persona/list`).then(r => r.json() as Promise<PersonaState>)
      })
      .then((s) => { if (!cancelledRef.current) setState(s) })
      .catch(err => console.error('[ui-voice] persona restore failed:', err))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [applied, state])

  const cancelledRef = useRef(false)
  useEffect(() => () => { cancelledRef.current = true }, [])

  const cycle = useCallback((kind: 'voice' | 'avatar' | 'idle', current: string) => {
    if (state === null) return
    const list = kind === 'voice' ? state.voices : kind === 'avatar' ? state.avatars : state.idles
    if (list.length === 0) return
    const idx = list.findIndex(x => x.name === current)
    const next = list[(idx + 1) % list.length]
    if (next === undefined) return
    const patch = { [kind]: next.name }
    writeSaved(PERSIST[kind], next.name)
    void fetch(`${bridgeBase()}/api/persona/set`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    })
      .then(() => fetch(`${bridgeBase()}/api/persona/list`).then(r => r.json() as Promise<PersonaState>))
      .then((s) => { if (!cancelledRef.current) setState(s) })
      .catch(err => console.error(`[ui-voice] persona ${kind} switch failed:`, err))
  }, [state])

  if (state === null) return null

  const curVoice = state.voices.find(v => v.name === state.current.voice) ?? state.voices[0]
  const curAvatar = state.avatars.find(a => a.name === state.current.avatar) ?? state.avatars[0]
  const curIdle = state.idles.find(i => i.name === state.current.idle) ?? state.idles[0]

  return (
    <>
      <button
        type="button"
        className={css.personaBtn}
        title={`${t('persona.voiceHint')}: ${curVoice?.label ?? ''}（点击切换）`}
        aria-label={t('persona.voiceHint')}
        onClick={() => cycle('voice', state.current.voice)}
      >
        <VoiceIcon />
      </button>
      <button
        type="button"
        className={css.personaBtn}
        title={`${t('persona.avatarHint')}: ${curAvatar?.label ?? ''}（点击切换）`}
        aria-label={t('persona.avatarHint')}
        onClick={() => cycle('avatar', state.current.avatar)}
      >
        <AvatarIcon />
      </button>
      <button
        type="button"
        className={css.personaBtn}
        title={`${t('persona.idleHint')}: ${curIdle?.label ?? ''}（点击切换）`}
        aria-label={t('persona.idleHint')}
        onClick={() => cycle('idle', state.current.idle)}
      >
        <IdleIcon />
      </button>
    </>
  )
})
