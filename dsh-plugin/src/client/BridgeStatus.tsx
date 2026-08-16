/**
 * BridgeStatus: composer tool-row indicator that the voice bridge is
 * reachable. When the bridge is DOWN, a warning button appears; clicking it
 * opens the companion repo's deploy guide (this plugin alone is a hollow UI
 * — voice/QQ features need the local bridge + NapCat from the main repo).
 *
 * Hidden entirely while the bridge is up (or while checking).
 */
import { memo, useEffect, useState } from 'react'
import type { PropsLocale, PropsRuntime } from '@deepseek-ai/dsh-client-ui-slots'
// Type-only: pulls ui-conversation's SlotMap merge for PropsRuntime resolution.
import type {} from '@deepseek-ai/dsh-client-ui-conversation/client'
import { bridgeBase } from './bridge.ts'
import type { VoiceInjected } from './contract.ts'
import css from './BridgeStatus.module.css'

/** URL of the companion repo with the full deploy guide. */
const MAIN_REPO_URL = 'https://github.com/beiyege-01/dsh-voice-ai-girlfriend#readme'

/** Full props: framework runtime share + `voice` locale seat + injected face. */
export type BridgeStatusProps =
  PropsRuntime<'conversation.input.left'> & PropsLocale<'voice'> & VoiceInjected

/** Warning triangle glyph (inline, follows currentColor). */
function WarnIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
      <line x1="12" y1="9" x2="12" y2="13" />
      <line x1="12" y1="17" x2="12.01" y2="17" />
    </svg>
  )
}

/**
 * @param props - framework runtime + locale seats.
 */
export const BridgeStatus = memo(function BridgeStatus(_props: BridgeStatusProps) {
  const [ok, setOk] = useState<boolean | null>(null)

  useEffect(() => {
    let cancelled = false
    const check = async () => {
      try {
        const controller = new AbortController()
        const timer = setTimeout(() => controller.abort(), 3000)
        try {
          const resp = await fetch(`${bridgeBase()}/api/health`, { signal: controller.signal })
          if (!cancelled) setOk(resp.ok)
        } finally {
          clearTimeout(timer)
        }
      } catch {
        if (!cancelled) setOk(false)
      }
    }
    void check()
    const timer = window.setInterval(check, 30000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [])

  // Show nothing while up or while the first check is pending.
  if (ok !== false) return null

  return (
    <button
      type="button"
      className={css.warn}
      title="未检测到 voice bridge —— 语音/QQ 功能不可用。点按查看部署指南"
      aria-label="未检测到 voice bridge —— 点按查看部署指南"
      onClick={() => window.open(MAIN_REPO_URL, '_blank', 'noopener')}
    >
      <WarnIcon />
    </button>
  )
})
