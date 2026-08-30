/**
 * BusyToggle: composer tool-row switch for what a voice sentence does while
 * the agent's turn is still running (reply streaming).
 *
 *  - ON  (default, `s2s.voice.interrupt` = '1'): 插话模式 — the sentence
 *    steers the running turn (interrupts it) and is answered immediately.
 *  - OFF (`s2s.voice.interrupt` = '0'): 排队模式 — the sentence is queued and
 *    auto-sends once the current turn finishes (continuous conversation; the
 *    running reply is never yanked mid-generation).
 *
 * The mic barge-in (stopping playback when the user starts speaking, an echo
 * guard) applies in both modes; this switch only picks the delivery mode.
 */
import { memo, useCallback, useState } from 'react'
import type { PropsLocale, PropsRuntime } from '@deepseek-ai/dsh-client-ui-slots'
// Type-only: pulls ui-conversation's SlotMap merge for PropsRuntime resolution.
import type {} from '@deepseek-ai/dsh-client-ui-conversation/client'
import type { VoiceInjected } from './contract.ts'
import css from './BusyToggle.module.css'

const INTERRUPT_KEY = 's2s.voice.interrupt'

function readInterrupt(): boolean {
  try {
    return localStorage.getItem(INTERRUPT_KEY) !== '0'
  } catch {
    return true
  }
}

/** Full toggle props: framework runtime share + `voice` locale seat + injected face. */
export type BusyToggleProps = PropsRuntime<'conversation.input.left'> & PropsLocale<'voice'> & VoiceInjected

/** Bolt glyph (inline, follows currentColor): interrupt when lit, queue when dim. */
function BoltIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
    </svg>
  )
}

/**
 * @param props - framework runtime + locale + injected sendText (delivery mode).
 */
export const BusyToggle = memo(function BusyToggle({ t }: BusyToggleProps) {
  const [on, setOn] = useState<boolean>(readInterrupt)

  const toggle = useCallback(() => {
    setOn((previous) => {
      const next = !previous
      try {
        localStorage.setItem(INTERRUPT_KEY, next ? '1' : '0')
      } catch {
        // persistence unavailable — state still flips for this session
      }
      return next
    })
  }, [])

  return (
    <button
      type="button"
      className={on ? css.boltOn : css.boltOff}
      title={on ? t('interrupt.onHint') : t('interrupt.offHint')}
      aria-label={on ? t('interrupt.onHint') : t('interrupt.offHint')}
      aria-pressed={on}
      onClick={toggle}
    >
      <BoltIcon />
    </button>
  )
})
