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
import { memo, useEffect, useRef } from 'react'
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
    .filter((block) => block.kind === 'text')
    .map((block) => block.text)
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
  const snapshot = useSession((s) => s)

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
  // DH mode (wait-for-video instead of immediate TTS): resolved once from the
  // bridge status (`enabled` + companion visible). null = not resolved yet.
  const dhModeRef = useRef<boolean | null>(null)
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

  // Unmount: stop playback and release any in-flight TTS.
  useEffect(() => () => {
    speaker.stop()
    _registerTtsAbort(null)
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
      void dhStatus().then((s) => {
        if (s !== null) dhModeRef.current = s.enabled === true
      })
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
      // Submit each settled, fully-streamed reply's full text to the bridge.
      // No sentence TTS here — the video carries the audio; the companion
      // window plays it (TTS + talking-head together) when generation ends.
      for (const node of snapshot.chat.nodes.values()) {
        if (node.kind !== 'assistant-step') continue
        if (node.anchorSeq <= baselineRef.current) continue
        if (node.anchorSeq === skipAnchorRef.current) continue
        if (dhSentRef.current.has(node.key)) continue
        const data = assistantData(node)
        if (data === undefined || data.status !== 'settled') continue
        const text = cleanReplyText(nodeText(data), 100000)
        if (text.trim().length < 2) continue
        dhSentRef.current.add(node.key)
        lastDhReplyAnchorRef.current = node.anchorSeq
        void dhSpeak(text).then((code) => {
          if (code !== null) lastDhCodeRef.current = code
        })
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
  }, [snapshot, speaker, _registerTtsAbort])

  return null
})
