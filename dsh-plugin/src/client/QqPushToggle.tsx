/**
 * QqPushToggle: composer tool-row switch for automatic QQ reply push.
 *
 * When ON (default), every settled reply is voiced to the configured QQ.
 * When OFF, the QQBridge skips pushing (your PC chat stays silent to QQ).
 * Persisted in localStorage `s2s.voice.qqPush` ('1'/'0', default on).
 */
import { memo, useCallback, useState } from 'react'
import type { PropsLocale, PropsRuntime } from '@deepseek-ai/dsh-client-ui-slots'
// Type-only: pulls ui-conversation's SlotMap merge for PropsRuntime resolution.
import type {} from '@deepseek-ai/dsh-client-ui-conversation/client'
import type { VoiceInjected } from './contract.ts'
import css from './QqPushToggle.module.css'

const QQ_PUSH_KEY = 's2s.voice.qqPush'

export function readQqPush(): boolean {
  try {
    return localStorage.getItem(QQ_PUSH_KEY) !== '0'
  } catch {
    return true
  }
}

/** Full toggle props: framework runtime share + `voice` locale seat + injected face. */
export type QqPushToggleProps =
  PropsRuntime<'conversation.input.left'> & PropsLocale<'voice'> & VoiceInjected

/** Chat-bubble glyph (inline, follows currentColor). */
function BubbleIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />
    </svg>
  )
}

/**
 * @param props - framework runtime + locale seats.
 */
export const QqPushToggle = memo(function QqPushToggle({ t }: QqPushToggleProps) {
  const [on, setOn] = useState<boolean>(readQqPush)

  const toggle = useCallback(() => {
    setOn((previous) => {
      const next = !previous
      try {
        localStorage.setItem(QQ_PUSH_KEY, next ? '1' : '0')
      } catch {
        // persistence unavailable — state still flips for this session
      }
      return next
    })
  }, [])

  return (
    <button
      type="button"
      className={on ? css.bubbleOn : css.bubbleOff}
      title={on ? t('qqpush.offHint') : t('qqpush.onHint')}
      aria-label={on ? t('qqpush.offHint') : t('qqpush.onHint')}
      aria-pressed={on}
      onClick={toggle}
    >
      <BubbleIcon />
    </button>
  )
})
