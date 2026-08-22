/**
 * PersonaToggle: three independent preset selectors for the voice stack —
 * TTS voice, digital-human avatar, and companion idle animation group.
 *
 * Each is a labelled capsule (icon + current value) that cycles through the
 * bridge's preset list on click. Visually distinct from the icon-only toggle
 * buttons: it has its own filled pill background, a short text label, and a
 * tooltip explaining what it switches and what the next value will be.
 *
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

/** Short label: strip the "avatar" prefix and extension for the pill text. */
function avatarLabel(name: string): string {
  return name.replace(/^avatar/, '').replace(/\.mp4$/i, '').trim() || '默认'
}

/** Small SVG glyphs (inline, follow currentColor). */
function VoiceIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 2a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z" />
      <path d="M19 10v1a7 7 0 0 1-14 0v-1" />
      <line x1="12" y1="18" x2="12" y2="22" />
    </svg>
  )
}

function AvatarIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
      <circle cx="12" cy="7" r="4" />
    </svg>
  )
}

function IdleIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="2" y="4" width="20" height="16" rx="2" />
      <path d="M2 9h20" />
      <circle cx="6" cy="13" r="1.2" />
      <circle cx="10" cy="13" r="1.2" />
    </svg>
  )
}

type Kind = 'voice' | 'avatar' | 'idle'

const KIND_META: Record<Kind, { key: keyof PersonaState['current']; icon: () => JSX.Element; hintKey: 'persona.voiceHint' | 'persona.avatarHint' | 'persona.idleHint' }> = {
  voice: { key: 'voice', icon: VoiceIcon, hintKey: 'persona.voiceHint' },
  avatar: { key: 'avatar', icon: AvatarIcon, hintKey: 'persona.avatarHint' },
  idle: { key: 'idle', icon: IdleIcon, hintKey: 'persona.idleHint' },
}

export const PersonaToggle = memo(function PersonaToggle({ t }: PropsRuntime<'conversation.input.dock'> & PropsLocale<'voice'> & VoiceInjected) {
  const [state, setState] = useState<PersonaState | null>(null)
  const [applied, setApplied] = useState(false)
  const appliedRef = useRef(false)
  const aliveRef = useRef(true)

  useEffect(() => () => { aliveRef.current = false }, [])

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
      .then(() => fetch(`${bridgeBase()}/api/persona/list`).then(r => r.json() as Promise<PersonaState>))
      .then((s) => { if (aliveRef.current) setState(s) })
      .catch(err => console.error('[ui-voice] persona restore failed:', err))
  }, [applied, state])

  const cycle = useCallback((kind: Kind) => {
    if (state === null) return
    const meta = KIND_META[kind]
    const list = kind === 'voice' ? state.voices : kind === 'avatar' ? state.avatars : state.idles
    if (list.length === 0) return
    const current = state.current[meta.key]
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
      .then((s) => {
        if (!aliveRef.current) return
        setState(s)
        // 通知 companion 立即刷新待机动画（不等 30s 轮询）。
        window.dispatchEvent(new CustomEvent('dsh-voice:persona', { detail: { kind, name: next.name } }))
      })
      .catch(err => console.error(`[ui-voice] persona ${kind} switch failed:`, err))
  }, [state])

  if (state === null) return null

  const currentVoice = state.current.voice
  const currentAvatar = state.current.avatar
  const currentIdle = state.current.idle
  const voiceLabel = state.voices.find(v => v.name === currentVoice)?.label ?? currentVoice
  const avatarLabelText = avatarLabel(currentAvatar)
  const idleLabel = state.idles.find(i => i.name === currentIdle)?.label ?? currentIdle

  const pill = (kind: Kind, label: string, current: string) => {
    const meta = KIND_META[kind]
    const Icon = meta.icon
    const nextName = (() => {
      const list = kind === 'voice' ? state.voices : kind === 'avatar' ? state.avatars : state.idles
      const idx = list.findIndex(x => x.name === current)
      const next = list[(idx + 1) % list.length]
      return next?.label ?? ''
    })()
    const hint = t(meta.hintKey)
    return (
      <button
        type="button"
        className={css.pill}
        title={`${hint}：当前「${label}」，点击切换到「${nextName}」`}
        aria-label={`${hint}：${label}`}
        onClick={() => cycle(kind)}
      >
        <span className={css.icon}><Icon /></span>
        <span className={css.label}>{label}</span>
      </button>
    )
  }

  return (
    <>
      {pill('voice', voiceLabel, currentVoice)}
      {pill('avatar', avatarLabelText, currentAvatar)}
      {pill('idle', idleLabel, currentIdle)}
    </>
  )
})
