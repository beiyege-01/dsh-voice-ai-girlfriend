/**
 * QQBridge: hidden component (renders null) that bridges the conversation to
 * QQ via the bridge's /api/qq/ws WebSocket + NapCat:
 *
 *  - inbound: a QQ private message arrives -> bridge pushes
 *    { type: 'qq_message', text } -> we inject it via sendText (same
 *    steer/queue delivery as voice input).
 *  - outbound: when a new assistant reply settles, we push its text back to
 *    the bridge ({ type: 'reply' }), which synthesizes TTS voice and sends
 *    it to the configured QQ.
 *
 * Enabled only when the bridge config has `qq.enabled`; the WS simply fails
 * to connect otherwise (silent, no UI).
 */
import { memo, useEffect, useRef } from 'react'
import type { PropsLocale, PropsRuntime } from '@deepseek-ai/dsh-client-ui-slots'
// Type-only: pulls ui-conversation's SlotMap merge for PropsRuntime resolution.
import type {} from '@deepseek-ai/dsh-client-ui-conversation/client'
import type { AssistantChatData } from '@deepseek-ai/dsh-client-ui-conversation/client'
import { bridgeBase } from '../bridge.ts'
import type { VoiceInjected } from '../contract.ts'
import { readQqPush } from '../QqPushToggle.tsx'
import { cleanReplyText } from './clean.ts'

/** Full props: framework runtime share + `voice` locale seat + injected face. */
export type QQBridgeProps = PropsRuntime<'conversation.input.left'> & PropsLocale<'voice'> & VoiceInjected

function qqWsUrl(): string {
  const base = bridgeBase()
  const proto = base.startsWith('https:') ? 'wss:' : 'ws:'
  return `${proto}//${base.replace(/^https?:\/\//, '')}/api/qq/ws`
}

function assistantData(node: { kind: string; data: unknown }): AssistantChatData | undefined {
  if (node.kind !== 'assistant-step') return undefined
  return node.data as AssistantChatData
}

function nodeText(data: AssistantChatData): string {
  return data.blocks
    .filter(block => block.kind === 'text')
    .map(block => block.text)
    .join('\n')
}

/**
 * @param props - framework runtime + locale + injected sendText.
 */
export const QQBridge = memo(function QQBridge({ useSession, sendText }: QQBridgeProps) {
  const wsRef = useRef<WebSocket | null>(null)
  const lastReplyAnchorRef = useRef(0)
  // Last-segment debounce per user turn (see reply-listener): only a turn
  // that stays quiet for LAST_DEBOUNCE_MS submits its final segment.
  const qqLastDebounceRef = useRef(new Map<number, { timer: ReturnType<typeof setTimeout>; anchor: number }>())
  const LAST_DEBOUNCE_MS = 4000
  const snapshot = useSession(s => s)

  // WS connect with auto-reconnect (3s). Only one tab should run this (the
  // bridge itself replaces stale connections).
  useEffect(() => {
    let closed = false
    let ws: WebSocket | null = null
    const connect = () => {
      if (closed) return
      ws = new WebSocket(qqWsUrl())
      ws.onopen = () => { wsRef.current = ws }
      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(String(event.data)) as { type?: string; text?: string }
          if (msg.type === 'qq_message' && typeof msg.text === 'string' && msg.text.trim() !== '') {
            void sendText(msg.text.trim()).catch((err) => {
              console.error('[ui-voice] qq inject failed:', err)
            })
          }
        } catch {
          // malformed frame — ignore
        }
      }
      ws.onclose = () => {
        if (wsRef.current === ws) wsRef.current = null
        if (!closed) setTimeout(connect, 3000)
      }
    }
    connect()
    return () => {
      closed = true
      if (ws !== null) { try { ws.close() } catch { /* already closed */ } }
    }
  }, [sendText])

  // New settled assistant replies -> push text to the bridge (it voices them
  // to QQ). Skips entirely when the QQ push toggle is off. Per USER turn,
  // deliver only the FIRST and the LAST settled assistant texts: the first
  // goes immediately, the last after a 4s quiet debounce (an intermediate
  // text never reaches QQ because a newer settled text keeps resetting the
  // timer). Agent tool-call chatter between A and B is never voiced.
  useEffect(() => {
    if (!readQqPush()) return
    const userAnchors: number[] = []
    for (const node of snapshot.chat.nodes.values()) {
      if (node.kind === 'user') userAnchors.push(node.anchorSeq)
    }
    userAnchors.sort((a, b) => a - b)
    type TurnPick = { anchor: number; text: string }
    const perUserTurn = new Map<number, { first: TurnPick; last: TurnPick }>()
    for (const node of snapshot.chat.nodes.values()) {
      if (node.kind !== 'assistant-step') continue
      const data = assistantData(node)
      if (data === undefined || data.status !== 'settled') continue
      const text = cleanReplyText(nodeText(data), 100000).trim()
      if (text.length < 2) continue
      let ua = -1
      for (const u of userAnchors) {
        if (u < node.anchorSeq) ua = u
        else break
      }
      const pick: TurnPick = { anchor: node.anchorSeq, text }
      const slot = perUserTurn.get(ua)
      if (slot === undefined) {
        perUserTurn.set(ua, { first: pick, last: pick })
      } else {
        if (node.anchorSeq < slot.first.anchor) slot.first = pick
        if (node.anchorSeq > slot.last.anchor) slot.last = pick
      }
    }
    const ws = wsRef.current
    const open = ws !== null && ws.readyState === WebSocket.OPEN
    const sendQq = (pick: TurnPick): void => {
      if (pick.anchor <= lastReplyAnchorRef.current) return
      lastReplyAnchorRef.current = pick.anchor
      if (open) ws.send(JSON.stringify({ type: 'reply', text: pick.text }))
    }
    for (const [ua, { first, last }] of perUserTurn) {
      sendQq(first)
      if (first.anchor === last.anchor) {
        const existing = qqLastDebounceRef.current.get(ua)
        if (existing !== undefined) {
          clearTimeout(existing.timer)
          qqLastDebounceRef.current.delete(ua)
        }
        continue
      }
      const existing = qqLastDebounceRef.current.get(ua)
      if (existing !== undefined) clearTimeout(existing.timer)
      const timer = setTimeout(() => {
        qqLastDebounceRef.current.delete(ua)
        sendQq(last)
      }, LAST_DEBOUNCE_MS)
      qqLastDebounceRef.current.set(ua, { timer, anchor: last.anchor })
    }
  }, [snapshot])

  // Unmount: clear QQ debounce timers.
  useEffect(() => () => {
    for (const { timer } of qqLastDebounceRef.current.values()) clearTimeout(timer)
    qqLastDebounceRef.current.clear()
  }, [])

  return null
})
