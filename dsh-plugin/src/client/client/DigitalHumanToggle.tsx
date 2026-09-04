/**
 * DigitalHumanToggle: composer tool-row switch for the digital-human (DUIX)
 * talking-head replies. ON (default): replies go to the bridge's video
 * pipeline; OFF: fall back to near-instant sentence TTS.
 */
import { memo, useCallback, useState } from 'react'
import type { PropsLocale, PropsRuntime } from '@deepseek-ai/dsh-client-ui-slots'
// Type-only: pulls ui-conversation's SlotMap merge for PropsRuntime resolution.
import type {} from '@deepseek-ai/dsh-client-ui-conversation/client'
import type { VoiceInjected } from './contract.ts'
import { chipStyle, chipKey } from './chip.ts'
import chips from './chips.css'

const DIGITAL_HUMAN_KEY = 's2s.voice.digitalHuman'

export function readDigitalHuman(): boolean {
  try {
    return localStorage.getItem(DIGITAL_HUMAN_KEY) !== '0'
  } catch {
    return true
  }
}

/** Full toggle props: framework runtime share + `voice` locale seat + injected face. */
export type DigitalHumanToggleProps =
  PropsRuntime<'conversation.input.left'> & PropsLocale<'voice'> & VoiceInjected

/** Talking-head glyph (inline, follows currentColor). */
function DigitalHumanIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="7" r="3.5" />
      <path d="M5.5 20a6.5 6.5 0 0 1 13 0" />
      <path d="M3 3.5 4.6 5M21 3.5 19.4 5M2 10H1M23 10h-1" />
    </svg>
  )
}

export const DigitalHumanToggle = memo(function DigitalHumanToggle({ t }: DigitalHumanToggleProps) {
  const [on, setOn] = useState<boolean>(readDigitalHuman)

  const toggle = useCallback(() => {
    setOn((previous) => {
      const next = !previous
      try {
        localStorage.setItem(DIGITAL_HUMAN_KEY, next ? '1' : '0')
      } catch {
        // persistence unavailable — state still flips for this session
      }
      return next
    })
  }, [])

  return (
    <span
      role="button"
      tabIndex={0}
      className={[chips.chip, on ? chips.on : ''].join(' ')}
      title={on ? t('dh.offHint') : t('dh.onHint')}
      aria-label={on ? t('dh.offHint') : t('dh.onHint')}
      aria-pressed={on}
      style={chipStyle(on)}
      onClick={toggle}
      onKeyDown={chipKey}
    >
      <DigitalHumanIcon />
    </span>
  )
})
