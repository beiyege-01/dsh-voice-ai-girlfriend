/**
 * Mic control for the composer tool row (`conversation.input.left`).
 *
 * Continuous listening mode: click once to start a MicRecorder (mic-capture
 * worklet + 1800 ms silence endpointing); every silence-endpointed utterance
 * is queued, sent to the bridge /api/stt, and injected into the conversation
 * via `sendText` — serially, one at a time. Listening stays active until the
 * user clicks again (which stops the recorder and drops queued utterances).
 * Starting capture barges in on any reply being read aloud.
 */
import { memo, useCallback, useEffect, useRef, useState } from 'react'
import type { PropsLocale, PropsRuntime } from '@deepseek-ai/dsh-client-ui-slots'
// Type-only: pulls ui-conversation's SlotMap merge for PropsRuntime resolution.
import type {} from '@deepseek-ai/dsh-client-ui-conversation/client'
import { stt, VadStream } from './bridge.ts'
import type { VoiceInjected } from './contract.ts'
import { MicRecorder } from './voice/recorder.ts'
import css from './MicButton.module.css'

/** Full mic-control props: framework runtime share + `voice` locale seat + injected sendText. */
export type MicButtonProps = PropsRuntime<'conversation.input.left'> & PropsLocale<'voice'> & VoiceInjected

type Phase = 'idle' | 'listening' | 'transcribing'

/** Mic glyph (inline, follows currentColor). */
function MicIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
      <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
      <line x1="12" y1="19" x2="12" y2="23" />
      <line x1="8" y1="23" x2="16" y2="23" />
    </svg>
  )
}

/**
 * @param props - framework runtime + locale seats + injected sendText/speaker.
 */
export const MicButton = memo(function MicButton({ t, sendText, speaker, interruptReply }: MicButtonProps) {
  const [phase, setPhase] = useState<Phase>('idle')
  const recorderRef = useRef<MicRecorder | null>(null)
  const queueRef = useRef<ArrayBuffer[]>([])
  const drainRunningRef = useRef(false)

  // Release the mic if the control unmounts mid-capture.
  useEffect(() => () => {
    recorderRef.current?.stop()
    recorderRef.current = null
  }, [])

  // Barge-in listening during reply playback: while the speaker is reading,
  // the recorder drops chunks (TTS echo never forms an utterance) but keeps
  // watching the level — sustained speech fires onSpeechInterrupt → the reply
  // is stopped and the user's ongoing speech becomes the next utterance.
  // The browser AEC (echoCancellation) keeps the TTS out of the mic signal.
  useEffect(() => {
    return speaker.subscribe(() => {
      const rec = recorderRef.current
      if (rec === null) return
      rec.setPaused(false)
      rec.setInterruptMode(speaker.speaking)
    })
  }, [speaker])

  // Drain queued utterances serially: STT -> send -> next (continuous mode).
  const drain = useCallback(async () => {
    if (drainRunningRef.current) return
    drainRunningRef.current = true
    try {
      while (queueRef.current.length > 0) {
        if (recorderRef.current === null) break // user stopped listening
        const pcm = queueRef.current.shift()!
        setPhase('transcribing')
        try {
          const { text } = await stt(pcm)
          if (text !== undefined && text.trim() !== '') {
            await sendText(text.trim())
          }
        } catch (err) {
          console.error('[ui-voice] stt/send failed:', err)
        }
      }
    } finally {
      drainRunningRef.current = false
      setPhase(recorderRef.current !== null ? 'listening' : 'idle')
    }
  }, [sendText])

  const toggle = useCallback(async () => {
    if (recorderRef.current !== null) {
      // Stop: end continuous listening, drop any queued utterances.
      recorderRef.current.stop()
      recorderRef.current = null
      queueRef.current = []
      setPhase('idle')
      return
    }

    // Barge-in: the user is about to speak — cut any reply being read aloud
    // AND swallow the rest of the current reply (its remaining sentences are
    // not spoken; the next reply speaks normally).
    interruptReply()

    const recorder = new MicRecorder({
      minSilenceMs: 1800,
      maxUtteranceMs: 30000,
      rmsThreshold: 0.01,
      // Noise gate (original project's worklet): -35 dBFS. Tighter than the
      // original's -45 so louder ambient hums (a desk fan measured ~ -40..
      // -30 dB) get faded out of the sent stream, while normal speech
      // (~ -26..-10 dB) passes untouched.
      noiseGateDb: -35,
      // Barge-in: bridge silero VAD (real human voice only). RMS values are
      // the fallback used when the bridge lacks the /api/vad endpoint.
      vad: new VadStream(),
      interruptThreshold: 0.06,
      interruptHoldMs: 250,
      interruptConfirmMs: 180,
      onSpeechInterrupt: () => {
        // The user started talking over the reply: stop it and swallow the
        // rest; the recorder (now accumulating) turns their speech into the
        // next utterance automatically.
        interruptReply()
      },
      onUtterance: (pcm) => {
        // Continuous listening: queue the utterance and drain serially.
        queueRef.current.push(pcm)
        void drain()
      },
    })
    recorderRef.current = recorder
    setPhase('listening')
    try {
      await recorder.start()
    } catch (err) {
      console.error('[ui-voice] mic start failed:', err)
      recorder.stop()
      recorderRef.current = null
      setPhase('idle')
    }
  }, [interruptReply, drain])

  const label = phase === 'idle'
    ? t('mic.title')
    : phase === 'listening' ? t('mic.listening') : t('mic.transcribing')
  const className = phase === 'idle'
    ? css.mic
    : phase === 'listening' ? `${css.mic} ${css.listening}` : `${css.mic} ${css.transcribing}`

  return (
    <button
      type="button"
      className={className}
      title={label}
      aria-label={label}
      onClick={toggle}
    >
      <MicIcon />
    </button>
  )
})
