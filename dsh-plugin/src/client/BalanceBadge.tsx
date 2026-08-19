/**
 * BalanceBadge: composer tool-row chip showing the DeepSeek account balance.
 *
 * Zero-polling: fetches /api/balance once on mount and again on click. The
 * bridge caches the upstream answer for 10 minutes, so repeat views cost
 * nothing. On failure it shows a retry chip instead of spam.
 */
import { memo, useCallback, useEffect, useState } from 'react'
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

/** Full props: framework runtime share (composer.dock stats area) + locale seat. */
export type BalanceBadgeProps =
  PropsRuntime<'conversation.composer.dock'> & PropsLocale<'voice'> & VoiceInjected

/**
 * @param props - framework runtime + locale seats.
 */
export const BalanceBadge = memo(function BalanceBadge({ t }: BalanceBadgeProps) {
  const [label, setLabel] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)

  const load = useCallback(async () => {
    setFailed(false)
    try {
      const resp = await fetch(`${bridgeBase()}/api/balance`)
      if (!resp.ok) throw new Error(`balance ${resp.status}`)
      const json = (await resp.json()) as BalanceResp
      const text = formatBalance(json)
      setLabel(text)
      if (text === null) setFailed(true)
    } catch {
      setFailed(true)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  if (label === null && !failed) {
    return <span className={css.chip} title={t('balance.loading')}>…</span>
  }
  return (
    <button
      type="button"
      className={css.chip}
      title={failed ? t('balance.errHint') : t('balance.hint')}
      aria-label={failed ? t('balance.errHint') : t('balance.hint')}
      onClick={load}
    >
      {failed ? '¥--' : label}
    </button>
  )
})
