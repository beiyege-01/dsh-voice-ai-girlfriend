/**
 * ui-voice slot contract: the registrant-side props composition for the
 * conversation-owned `conversation.input.left` slot, plus the injected face
 * the plugin apply provides (sendText bound to the session-scoped
 * conversation service).
 */

import type { CompanionController } from './voice/companion-controller.ts'
import type { ReplySpeaker } from './voice/speaker.ts'

/** Injected behavior face the voice components receive from the plugin apply. */
export interface VoiceInjected {
  /**
   * Send a recognized utterance into the current session as a user prompt
   * (equivalent to typing it). Rejects when no session scope or the
   * conversation service is unavailable.
   * @param text - recognized utterance text.
   */
  sendText: (text: string) => Promise<void>
  /** Plays synthesized reply audio; one shared instance per plugin fiber. */
  speaker: ReplySpeaker
  /** Shared companion-window visibility (rendered by the window, flipped by the toggle). */
  companion: CompanionController
  /**
   * Abort any TTS request currently in flight (the reply listener registers
   * its AbortController here; the voice toggle calls this when turned off so
   * the backend stops synthesizing instead of draining its queue).
   */
  abortTts: () => void
  /**
   * Internal wiring (plugin-private): the reply listener registers its
   * current TTS AbortController so `abortTts()` can cancel it.
   */
  _registerTtsAbort: (controller: AbortController | null) => void
  /**
   * Mic barge-in: stop playback, abort the in-flight TTS request, and ask the
   * reply listener to swallow the rest of the current reply (its remaining
   * sentences are not spoken; the next reply speaks normally).
   */
  interruptReply: () => void
  /**
   * Internal wiring (plugin-private): the reply listener registers a handler
   * invoked by `interruptReply()` to mark its current reply as interrupted.
   */
  _registerInterruptHandler: (handler: (() => void) | null) => void
}
