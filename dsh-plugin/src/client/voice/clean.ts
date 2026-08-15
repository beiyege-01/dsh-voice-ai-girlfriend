/**
 * Clean assistant reply text for TTS reading.
 *
 * The reply body is markdown (headings, emphasis, lists, inline code, fenced
 * code blocks — including ```dsh-ui fences with JSON) plus URLs. Reading it
 * verbatim is noisy and can generate minutes of audio. This strips the
 * decorations, collapses whitespace, and caps the length at a sentence
 * boundary so a reading stays short and audible.
 */

const FENCED_BLOCK = /```[\s\S]*?```/g
const INLINE_CODE = /`[^`\n]*`/g
const URL = /https?:\/\/[^\s)\]]+/g
const HEADING = /^#{1,6}\s+/gm
const QUOTE = /^>\s?/gm
const LIST_MARK = /^[-*+]\s+/gm
const EMPHASIS = /[*_~]{1,3}/g
const WHITESPACE = /\s+/g

/**
 * @param text - raw assistant markdown text.
 * @param maxChars - soft cap; truncation lands on a Chinese/English sentence
 *   boundary when one exists past the half-way point.
 * @returns clean, single-line, capped text ('' when nothing remains).
 */
export function cleanReplyText(text: string, maxChars = 400): string {
  let out = text
    .replace(FENCED_BLOCK, ' ')
    .replace(INLINE_CODE, ' ')
    .replace(URL, ' ')
    .replace(HEADING, '')
    .replace(QUOTE, '')
    .replace(LIST_MARK, '')
    .replace(EMPHASIS, '')
    .replace(WHITESPACE, ' ')
    .trim()
  if (out.length <= maxChars) return out
  const cut = out.slice(0, maxChars)
  const boundary = Math.max(
    cut.lastIndexOf('。'),
    cut.lastIndexOf('！'),
    cut.lastIndexOf('？'),
    cut.lastIndexOf('.'),
    cut.lastIndexOf('!'),
    cut.lastIndexOf('?'),
  )
  return boundary > maxChars * 0.5
    ? `${cut.slice(0, boundary + 1)}…`
    : `${cut}…`
}
