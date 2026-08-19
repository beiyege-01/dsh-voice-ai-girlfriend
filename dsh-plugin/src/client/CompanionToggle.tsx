/**
 * CompanionToggle: composer tool-row switch for the companion animation
 * window (shows/hides the right-side video column). State lives in the shared
 * CompanionController (persisted `s2s.voice.companion`, default on).
 */
import { memo, useCallback, useEffect, useState } from 'react'
import type { PropsLocale, PropsRuntime } from '@deepseek-ai/dsh-client-ui-slots'
// Type-only: pulls ui-conversation's SlotMap merge for PropsRuntime resolution.
import type {} from '@deepseek-ai/dsh-client-ui-conversation/client'
import type { VoiceInjected } from './contract.ts'
import css from './CompanionToggle.module.css'

/** Full toggle props: framework runtime share + `voice` locale seat + injected face. */
export type CompanionToggleProps =
  PropsRuntime<'conversation.input.left'> & PropsLocale<'voice'> & VoiceInjected

/** Girl-in-window glyph: a rounded window frame holding a person's silhouette
 *  on the RIGHT side of the window (the companion column sits on the right).
 *  Inline, follows currentColor. */
function DisplayIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="2.5" y="3" width="19" height="14.5" rx="2.5" />
      <line x1="8" y1="21" x2="16" y2="21" />
      <line x1="12" y1="17.5" x2="12" y2="21" />
      <circle cx="13.5" cy="9" r="2.1" />
      <path d="M10.7 14.6a3.1 3.1 0 0 1 5.6 0" />
    </svg>
  )
}

/**
 * @param props - framework runtime + locale + injected companion controller.
 */
export const CompanionToggle = memo(function CompanionToggle({ t, companion }: CompanionToggleProps) {
  const [on, setOn] = useState<boolean>(companion.visible)

  useEffect(() => companion.subscribe(() => setOn(companion.visible)), [companion])

  const toggle = useCallback(() => {
    companion.visible = !companion.visible
  }, [companion])

  return (
    <button
      type="button"
      className={on ? css.displayOn : css.displayOff}
      title={on ? t('companion.offHint') : t('companion.onHint')}
      aria-label={on ? t('companion.offHint') : t('companion.onHint')}
      aria-pressed={on}
      onClick={toggle}
    >
      <DisplayIcon />
      </button>
  )
})
