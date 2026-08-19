/**
 * VoiceToggle: composer tool-row switch for reply TTS reading.
 *
 * Persists `s2s.voice.enabled` ('1'/'0', default on). The reply listener
 * re-reads it on every snapshot change, so the switch takes effect from the
 * next reply. The mic input stays available regardless (the toggle controls
 * reading only).
 */
import { memo, useCallback, useState } from 'react'
import type { PropsLocale, PropsRuntime } from '@deepseek-ai/dsh-client-ui-slots'
// Type-only: pulls ui-conversation's SlotMap merge for PropsRuntime resolution.
import type {} from '@deepseek-ai/dsh-client-ui-conversation/client'
import type { VoiceInjected } from './contract.ts'
import css from './VoiceToggle.module.css'

const VOICE_ENABLED_KEY = 's2s.voice.enabled'

function readEnabled(): boolean {
  try {
    return localStorage.getItem(VOICE_ENABLED_KEY) !== '0'
  } catch {
    return true
  }
}

/** Full toggle props: framework runtime share + `voice` locale seat + injected face. */
export type VoiceToggleProps = PropsRuntime<'conversation.input.left'> & PropsLocale<'voice'> & VoiceInjected

/** Speaker glyph with sound waves (inline, follows currentColor). */
function SpeakerIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
      <path d="M15.54 8.46a5 5 0 0 1 0 7.07" />
      <path d="M19.07 4.93a10 10 0 0 1 0 14.14" />
    </svg>
  )
}

/**
 * @param props - framework runtime + locale + injected speaker (interrupt).
 */
export const VoiceToggle = memo(function VoiceToggle({ t, speaker, abortTts }: VoiceToggleProps) {
  const [on, setOn] = useState<boolean>(readEnabled)

  const toggle = useCallback(() => {
    setOn((previous) => {
      const next = !previous
      try {
        localStorage.setItem(VOICE_ENABLED_KEY, next ? '1' : '0')
      } catch {
        // persistence unavailable — state still flips for this session
      }
      if (!next) {
        // Turning the reading OFF: interrupt any reply currently being read
        // AND abort the in-flight TTS request so the bridge stops
        // synthesizing instead of draining its queue.
        speaker.stop()
        abortTts()
      }
      return next
    })
  }, [speaker, abortTts])

  return (
    <button
      type="button"
      className={on ? css.speakerOn : css.speakerOff}
      title={on ? t('toggle.offHint') : t('toggle.onHint')}
      aria-label={on ? t('toggle.offHint') : t('toggle.onHint')}
      aria-pressed={on}
      onClick={toggle}
    >
      <SpeakerIcon />
      </button>
  )
})
