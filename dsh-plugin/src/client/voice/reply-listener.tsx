/**
 * ReplySpeakerMount: hidden per-session component (renders null) that streams
 * assistant text to TTS sentence-by-sentence — mirroring the original
 * backend's LMOutputProcessor (per-sentence chunks) so long replies start
 * speaking while the rest are still being synthesized.
 *
 * Each assistant chat node's text is split into complete sentences; as new
 * complete sentences appear (the node streams via `assistant/chunk`
 * publications), they are fetched from the bridge /api/tts through a serial
 * chain and played back in order through the shared ReplySpeaker's FIFO
 * queue. The trailing partial sentence is not spoken until it completes.
 *
 * History replay protection: nodes are seeded on first sight (their current
 * complete sentences are marked spoken), so existing/old content never
 * replays. Barge-in (mic start) swallows the rest of the current reply.
 */
import { memo, useEffect, useRef, useState } from 'react'
import type { PropsLocale, PropsRuntime } from '@deepseek-ai/dsh-client-ui-slots'
// Type-only: pulls ui-conversation's SlotMap merge for PropsRuntime resolution.
import type {} from '@deepseek-ai/dsh-client-ui-conversation/client'
import type { AssistantChatData } from '@deepseek-ai/dsh-client-ui-conversation/client'
import { tts, dhSpeak, dhStatus, dhDiscard } from '../bridge.ts'
import { readDigitalHuman } from '../DigitalHumanToggle.tsx'
import type { VoiceInjected } from '../contract.ts'
import { cleanReplyText } from './clean.ts'
import { splitSentences } from './sentences.ts'

const VOICE_ENABLED_KEY = 's2s.voice.enabled'

function voiceEnabled(): boolean {
  try {
    return localStorage.getItem(VOICE_ENABLED_KEY) !== '0'
  } catch {
    return true
  }
}

/** Companion window visibility (digital-human video only matters when it shows). */
function companionVisible(): boolean {
  try {
    return localStorage.getItem('s2s.voice.companion') !== '0'
  } catch {
    return true
  }
}

/** Read the assistant row payload off a chat view node (kind `assistant-step`). */
function assistantData(node: { kind: string; data: unknown }): AssistantChatData | undefined {
  if (node.kind !== 'assistant-step') return undefined
  return node.data as AssistantChatData
}

/** Join the node's text blocks (reasoning/tool-call/image excluded). */
function nodeText(data: AssistantChatData): string {
  return data.blocks
    .filter(block => block.kind === 'text')
    .map(block => block.text)
    .join('\n')
}

/** Full props: framework runtime share + `voice` locale seat + injected face. */
export type ReplySpeakerMountProps =
  PropsRuntime<'conversation.input.left'> & PropsLocale<'voice'> & VoiceInjected

/**
 * @param props - framework runtime + locale + injected speaker/abort face.
 */
export const ReplySpeakerMount = memo(function ReplySpeakerMount({
  useSession,
  speaker,
  _registerTtsAbort,
  _registerInterruptHandler,
}: ReplySpeakerMountProps) {
  // Subscribe to the WHOLE snapshot (see T6: `s.chat.nodes` is a stable live
  // store whose reference never changes, so selecting it would never re-render
  // — the top-level snapshot object IS swapped on every publication).
  const snapshot = useSession(s => s)

  // Per-node complete sentences already spoken (node.key -> count).
  const spokenRef = useRef(new Map<string, number>())
  // History replay protection: baseline anchor. On mount the conversation
  // snapshot can be EMPTY (session history loads asynchronously after a
  // restart), so a one-shot seed there would miss the history and every old
  // reply would replay. Instead we wait until the first SETTLED assistant
  // node arrives, then set the baseline to the current max anchor — nothing
  // at or below it ever speaks. Live (running) nodes are never used for the
  // baseline, so a fresh reply in a brand-new session still speaks.
  const baselineRef = useRef<number | null>(null)
  // Serial TTS fetch chain: sentence N+1's fetch starts after N's resolves
  // (playback drains independently through the speaker queue — pipelined).
  const chainRef = useRef<Promise<void>>(Promise.resolve())
  // Barge-in: swallow the CURRENT reply only. We record the exact anchor of
  // the reply being interrupted (never a "<= max" line): if the interrupt
  // flag is consumed after a NEW reply already appeared in the snapshot, a
  // range-based skip would swallow that fresh reply too — the "new reply
  // never speaks" bug. Exact-anchor skip lets later replies play normally.
  const interruptRef = useRef(false)
  const skipAnchorRef = useRef(0)
  const skipUntilRef = useRef(0)
  // Digital-human: nodes whose finished reply was already handed to the bridge.
  const dhSentRef = useRef(new Set<string>())
  // Last-segment debounce per user turn: while a turn is still streaming, new
  // settled texts keep replacing the "last" candidate; only once no new text
  // arrives for LAST_DEBOUNCE_MS is the candidate confirmed and submitted.
  // key = user anchor, value = { timer, anchor } (the candidate's anchor).
  const dhLastDebounceRef = useRef(new Map<number, { timer: ReturnType<typeof setTimeout>; anchor: number }>())
  const LAST_DEBOUNCE_MS = 4000
  // DH mode (wait-for-video instead of immediate TTS): resolved once from the
  // bridge status (`enabled` + companion visible). null = not resolved yet.
  const dhModeRef = useRef<boolean | null>(null)
  // Whether the DH-mode resolution has completed (triggers re-render so the
  // reply that arrived during the pending window is reprocessed with the
  // correct dhMode — fixes the "TTS speaks first, then the video speaks the
  // same reply again" double-play race).
  const [dhResolved, setDhResolved] = useState(false)
  // Code of the most recently submitted digital-human task (for discard).
  const lastDhCodeRef = useRef<string | null>(null)
  // Anchor of the last DH-submitted reply node; a later user node means the
  // pending video is stale and should be discarded.
  const lastDhReplyAnchorRef = useRef(0)
  // Highest user-node anchor seen (new-turn detection).
  const lastUserAnchorRef = useRef(0)

  // Register the barge-in handler once (the mic calls interruptReply).
  useEffect(() => {
    _registerInterruptHandler(() => {
      interruptRef.current = true
    })
    return () => _registerInterruptHandler(null)
  }, [_registerInterruptHandler])

  // Unmount: stop playback, release any in-flight TTS, clear DH debounce timers.
  useEffect(() => () => {
    speaker.stop()
    _registerTtsAbort(null)
    for (const { timer } of dhLastDebounceRef.current.values()) clearTimeout(timer)
    dhLastDebounceRef.current.clear()
  }, [speaker, _registerTtsAbort])

  // Stream new complete sentences to TTS on every snapshot change. In digital
  // human mode the reply is NOT spoken immediately — its full text goes to the
  // bridge, which renders a lip-synced video (with the TTS audio embedded);
  // the companion window plays video + sound together once it is ready.
  useEffect(() => {
    if (!voiceEnabled()) return

    // Resolve the bridge DH availability once (config doesn't change at runtime);
    // companion visibility + the digital-human toggle are RE-CHECKED every
    // render so flipping the toggle mid-session takes effect immediately:
    // turning the toggle OFF falls back to the near-instant sentence TTS.
    if (dhModeRef.current === null) {
      // DH 模式尚未确认：先不朗读也不提交视频，等 dhStatus 返回（或 3s 超时
      // 兜底——桥接不可达时按非 DH 处理，回复仍会 TTS 朗读）。解析完成后
      // setDhResolved 触发重渲染，等待窗口期到达的回复会按正确的 dhMode 处理，
      // 避免「同一回复先被逐句 TTS 朗读、又被提交生成视频」的双重播放。
      let settled = false
      const finish = (enabled: boolean | null): void => {
        if (settled) return
        settled = true
        if (dhModeRef.current === null) {
          dhModeRef.current = enabled === true
        }
        setDhResolved(true)
      }
      void dhStatus().then((s) => finish(s?.enabled === true)).catch(() => finish(false))
      setTimeout(() => finish(false), 3000)
      return
    }
    const dhMode = dhModeRef.current === true && companionVisible() && readDigitalHuman()

    // Barge-in swallowed the CURRENT reply: remember its exact anchor so only
    // that reply's remaining sentences are skipped; replies that appear
    // later (or that already appeared) still speak. In DH mode, discard the
    // pending video task (the reply is being talked over).
    if (interruptRef.current) {
      if (dhMode) dhDiscard(lastDhCodeRef.current)
      let maxAnchor = 0
      for (const node of snapshot.chat.nodes.values()) {
        if (node.kind === 'assistant-step' && node.anchorSeq > maxAnchor) maxAnchor = node.anchorSeq
      }
      if (maxAnchor > 0) skipAnchorRef.current = maxAnchor
      interruptRef.current = false
      return
    }

    // First settled assistant node arrives: freeze the history baseline so
    // pre-existing replies never replay (page load, session revisit, history
    // pagination). Running nodes are live replies — they are NOT used here,
    // so a fresh reply in a new session still speaks.
    if (baselineRef.current === null) {
      let maxAnchor = 0
      let hasSettled = false
      for (const node of snapshot.chat.nodes.values()) {
        if (node.kind !== 'assistant-step') continue
        const data = assistantData(node)
        if (data === undefined) continue
        if (data.status === 'settled') hasSettled = true
        if (node.anchorSeq > maxAnchor) maxAnchor = node.anchorSeq
      }
      if (hasSettled && maxAnchor > 0) {
        baselineRef.current = maxAnchor
        skipUntilRef.current = maxAnchor
      }
      return
    }

    if (dhMode) {
      // New user turn after a DH-submitted reply: the pending video is stale.
      for (const node of snapshot.chat.nodes.values()) {
        if (node.kind !== 'user') continue
        if (node.anchorSeq <= lastUserAnchorRef.current) continue
        lastUserAnchorRef.current = node.anchorSeq
        if (lastDhReplyAnchorRef.current > 0 && node.anchorSeq > lastDhReplyAnchorRef.current) {
          dhDiscard(lastDhCodeRef.current)
        }
      }
      // Submit settled, fully-streamed replies' full text to the bridge.
      // No sentence TTS here — the video carries the audio; the companion
      // window plays it (TTS + talking-head together) when generation ends.
      // Per USER turn, deliver only the FIRST and the LAST settled assistant
      // texts. A user turn spans one user node to the next; an agent reply
      // (first answer A -> tool call -> final answer B) is ONE user turn, so
      // both A and B are spoken, while every intermediate text — tool-call
      // output, transitional chatter, mid-work updates — never spawns a video.
      // (Pure chat turns have first === last and deliver once.)
      const userAnchors: number[] = []
      for (const node of snapshot.chat.nodes.values()) {
        if (node.kind === 'user') userAnchors.push(node.anchorSeq)
      }
      userAnchors.sort((a, b) => a - b)
      type TurnPick = { node: { key: string; anchorSeq: number }; data: AssistantChatData; anchor: number }
      const perUserTurn = new Map<number, { first: TurnPick; last: TurnPick }>()
      for (const node of snapshot.chat.nodes.values()) {
        if (node.kind !== 'assistant-step') continue
        const data = assistantData(node)
        if (data === undefined || data.status !== 'settled') continue
        const text = cleanReplyText(nodeText(data), 100000)
        if (text.trim().length < 2) continue
        // The user turn this reply belongs to = nearest preceding user anchor.
        let ua = -1
        for (const u of userAnchors) {
          if (u < node.anchorSeq) ua = u
          else break
        }
        const pick: TurnPick = { node, data, anchor: node.anchorSeq }
        const slot = perUserTurn.get(ua)
        if (slot === undefined) {
          perUserTurn.set(ua, { first: pick, last: pick })
        } else {
          if (node.anchorSeq < slot.first.anchor) slot.first = pick
          if (node.anchorSeq > slot.last.anchor) slot.last = pick
        }
      }
      const submitDh = (pick: TurnPick): void => {
        const base = baselineRef.current
        if (base !== null && pick.anchor <= base) return
        if (pick.anchor === skipAnchorRef.current) return
        if (dhSentRef.current.has(pick.node.key)) return
        const text = cleanReplyText(nodeText(pick.data), 100000)
        if (text.trim().length < 2) return
        dhSentRef.current.add(pick.node.key)
        lastDhReplyAnchorRef.current = pick.anchor
        void dhSpeak(text).then((code) => {
          if (code !== null) lastDhCodeRef.current = code
        })
      }
      for (const [ua, { first, last }] of perUserTurn) {
        // FIRST segment: submit immediately (it is settled and will not change).
        submitDh(first)
        if (first.node.key === last.node.key) {
          // Pure chat turn (no tool-call churn): first === last, nothing else.
          const existing = dhLastDebounceRef.current.get(ua)
          if (existing !== undefined) {
            clearTimeout(existing.timer)
            dhLastDebounceRef.current.delete(ua)
          }
          continue
        }
        // LAST segment: debounce — replace the candidate (reset the timer) on
        // every new settled text so an intermediate text never gets submitted;
        // only a 4s-quiet turn submits its final segment.
        const existing = dhLastDebounceRef.current.get(ua)
        if (existing !== undefined) clearTimeout(existing.timer)
        const timer = setTimeout(() => {
          dhLastDebounceRef.current.delete(ua)
          submitDh(last)
        }, LAST_DEBOUNCE_MS)
        dhLastDebounceRef.current.set(ua, { timer, anchor: last.anchor })
      }
      return
    }

    // Non-DH mode: collect the complete sentences that are new (beyond each
    // node's spoken count), in (anchor, index) order. A SETTLED node also
    // flushes its trailing partial (the reply ended without a terminal
    // punctuation, like a credit line); running nodes wait for the partial.
    const jobs: { anchor: number; key: string; index: number; sentence: string }[] = []
    for (const node of snapshot.chat.nodes.values()) {
      if (node.kind !== 'assistant-step') continue
      if (node.anchorSeq <= skipUntilRef.current) continue
      if (node.anchorSeq === skipAnchorRef.current) continue
      const data = assistantData(node)
      if (data === undefined || data.status === 'interrupted') continue
      const { sentences, partial } = splitSentences(cleanReplyText(nodeText(data), 100000))
      const speakable = data.status === 'settled' && partial !== null
        ? [...sentences, partial]
        : sentences
      const spoken = spokenRef.current.get(node.key) ?? 0
      if (speakable.length > spoken) {
        for (let i = spoken; i < speakable.length; i++) {
          jobs.push({ anchor: node.anchorSeq, key: node.key, index: i, sentence: speakable[i]! })
        }
        spokenRef.current.set(node.key, speakable.length)
      }
    }
    if (jobs.length === 0) return
    jobs.sort((a, b) => (a.anchor - b.anchor) || (a.index - b.index))

    // Chain the fetches serially (order preserved; playback pipelines via the
    // speaker queue). Each step re-checks voice/interrupt so an abort stops
    // the rest of the chain.
    chainRef.current = jobs.reduce(
      (chain, job) => chain.then(() => {
        if (interruptRef.current || !voiceEnabled()) return
        const controller = new AbortController()
        _registerTtsAbort(controller)
        return tts(job.sentence, controller.signal)
          .then((wav) => {
            if (interruptRef.current || !voiceEnabled()) return
            speaker.speak(wav)
          })
          .catch((err) => {
            if ((err as Error | undefined)?.name !== 'AbortError') {
              console.error('[ui-voice] reply TTS failed:', err)
            }
          })
          .finally(() => _registerTtsAbort(null))
      }),
      chainRef.current,
    )
  }, [snapshot, speaker, _registerTtsAbort, dhResolved])

  return null
})
