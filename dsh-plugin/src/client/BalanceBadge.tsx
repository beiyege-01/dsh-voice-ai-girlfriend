/**
 * BalanceBadge (stats-line variant): no longer renders its own row — it feeds
 * the DeepSeek balance into the composer stats line as an extra group (e.g.
 * "3 轮 · 5 步 · 工具 2.1s · ¥45.10"). The row elides when overlong and the
 * hover tooltip reveals the full line.
 *
 * Cost: fetches /api/balance on mount, then every 10 minutes (the bridge
 * caches the upstream answer for 10 minutes, so the official API is hit at
 * most once per 10 minutes). Renders null.
 */
import { memo, useEffect, useRef } from 'react'
import type { PropsLocale, PropsRuntime } from '@deepseek-ai/dsh-client-ui-slots'
// Type-only: pulls ui-conversation's SlotMap merge for PropsRuntime resolution.
import type {} from '@deepseek-ai/dsh-client-ui-conversation/client'
import type { VoiceInjected } from './contract.ts'
import { bridgeBase } from './bridge.ts'

/**
 * Stats-row extra registry — convention-based window global shared with
 * ui-conversation's stats-extras.ts (cross-plugin value imports are forbidden,
 * so both sides address the same key without importing each other).
 */
type StatsExtra = () => string | null
interface WindowWithExtras {
  __dshStatsExtras?: Set<StatsExtra>
}
function statsRegistry(): Set<StatsExtra> {
  const win = window as WindowWithExtras
  return win.__dshStatsExtras ??= new Set<StatsExtra>()
}

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
 * @param props - framework runtime + locale seats (renders nothing).
 */
export const BalanceBadge = memo(function BalanceBadge(_props: BalanceBadgeProps) {
  const labelRef = useRef<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const resp = await fetch(`${bridgeBase()}/api/balance`)
        if (!resp.ok) return
        const json = (await resp.json()) as BalanceResp
        if (!cancelled) labelRef.current = formatBalance(json)
      } catch {
        // keep the previous value; retry on the next tick
      }
    }
    void load()
    const timer = window.setInterval(load, REFRESH_MS)
    const extra: StatsExtra = () => {
      const value = labelRef.current
      return value === null ? null : `余额 ${value}`
    }
    statsRegistry().add(extra)
    return () => {
      cancelled = true
      window.clearInterval(timer)
      statsRegistry().delete(extra)
    }
  }, [])

  return null
})
