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
  // Sticky flag: this session ever contained a tool-call node → it is an
  // agent working session. Only plain chat replies are voiced to QQ; agent
  // tool-call intermediates / interim text are not (matches the DUIX skip).
  const seenToolCallRef = useRef(false)
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

  // New settled assistant reply -> push text to the bridge (it voices it to QQ).
  // Skips entirely when the QQ push toggle is off. Agent working sessions
  // (any tool-call seen) are skipped: only plain chat replies reach QQ.
  useEffect(() => {
    if (!readQqPush()) return
    for (const node of snapshot.chat.nodes.values()) {
      if (node.kind === 'tool-call') seenToolCallRef.current = true
    }
    let maxAnchor = 0
    let newest: { anchor: number; text: string } | null = null
    for (const node of snapshot.chat.nodes.values()) {
      if (node.kind !== 'assistant-step') continue
      const data = assistantData(node)
      if (data === undefined || data.status !== 'settled') continue
      if (node.anchorSeq > maxAnchor) {
        maxAnchor = node.anchorSeq
        newest = { anchor: node.anchorSeq, text: cleanReplyText(nodeText(data), 100000) }
      }
    }
    if (seenToolCallRef.current) return // agent working session — no QQ voice
    if (newest !== null && newest.anchor > lastReplyAnchorRef.current) {
      lastReplyAnchorRef.current = newest.anchor
      const text = newest.text.trim()
      if (text !== '') {
        const ws = wsRef.current
        if (ws !== null && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: 'reply', text }))
        }
      }
    }
  }, [snapshot])

  return null
})
