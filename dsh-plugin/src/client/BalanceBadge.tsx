/**
 * BalanceBadge (composer stats pill): shows the DeepSeek API balance in the
 * composer's bottom stats row.
 *
 * dsh 0.2.x renders `conversation.composer.dock` as the official stats pill
 * row (activity / usage / …), so this entry renders its own compact pill
 * instead of feeding a harness-side stats-extras registry. That registry was a
 * local stats-line patch on the 0.1.3-era checkout; the official 0.2.x tree
 * has no such seam, which made the old "renders null + window global" version
 * invisible. A real pill works on every official build, needs no harness
 * patch, and is click-to-refresh.
 *
 * Cost: fetches /api/balance on mount, then every 10 minutes (the bridge
 * caches the upstream answer for 10 minutes, so the official API is hit at
 * most once per 10 minutes).
 */
import { memo, useCallback, useEffect, useRef, useState } from 'react'
import type { PropsLocale, PropsRuntime } from '@deepseek-ai/dsh-client-ui-slots'
// Type-only: pulls ui-conversation's SlotMap merge for PropsRuntime resolution.
import type {} from '@deepseek-ai/dsh-client-ui-conversation/client'
import type { VoiceInjected } from './contract.ts'
import { bridgeBase } from './bridge.ts'
import css from './BalanceBadge.module.css'

interface BalanceInfo {
  currency: string
  total_balance: string
  granted_balance: string
  topped_up_balance: string
}

interface BalanceResp {
  is_available?: boolean
  balance_infos?: BalanceInfo[]
}

/** Format the first (CNY/USD) balance as "¥xx.xx" / "$xx.xx". */
function formatBalance(body: BalanceResp): string | null {
  const info = body.balance_infos?.[0]
  if (info === undefined) return null
  const value = Number.parseFloat(info.total_balance)
  if (!Number.isFinite(value)) return null
  const symbol = info.currency === 'USD' ? '$' : '¥'
  return `${symbol}${value.toFixed(2)}`
}

const REFRESH_MS = 10 * 60 * 1000 // aligned with the bridge's 10-min cache

/** Full props: framework runtime share (composer.dock) + locale seat + face. */
export type BalanceBadgeProps =
  PropsRuntime<'conversation.composer.dock'> & PropsLocale<'voice'> & VoiceInjected

/**
 * @param props - framework runtime + locale seats.
 */
export const BalanceBadge = memo(function BalanceBadge(_props: BalanceBadgeProps) {
  const [label, setLabel] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const aliveRef = useRef(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const resp = await fetch(`${bridgeBase()}/api/balance`)
      if (!resp.ok) return
      const json = (await resp.json()) as BalanceResp
      if (aliveRef.current) setLabel(formatBalance(json))
    } catch {
      // Bridge offline: keep the previous value; the next tick retries.
    } finally {
      if (aliveRef.current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    aliveRef.current = true
    void load()
    const timer = window.setInterval(() => { void load() }, REFRESH_MS)
    return () => {
      aliveRef.current = false
      window.clearInterval(timer)
    }
  }, [load])

  // Nothing to show before the first successful fetch; stay out of the row.
  if (label === null) return null

  return (
    <button
      type="button"
      className={css.chip}
      title={`DeepSeek 余额 ${label}（点击刷新）`}
      aria-label={`DeepSeek 余额 ${label}，点击刷新`}
      onClick={() => { void load() }}
      disabled={loading}
    >
      余额 {label}
    </button>
  )
})
