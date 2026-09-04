/**
 * dsh 0.1.3 chat-view accessor.
 *
 * rc.8 exposed the chat nodes as a top-level `snapshot.chat.nodes` Map on the
 * session snapshot. dsh 0.1.3 moves the chat view into a `useChat` selector
 * (ui-chat): subscribing to it ACTIVATES the chat target, and the returned
 * ChatSnapshot's `nodes` is a `ChatNodeStore` whose `.values()` returns
 * `readonly ChatConversationViewNode[]` (kind `assistant-step` etc.). This
 * helper reads that shape defensively.
 */
export interface VoiceChatNode {
  kind: string
  key: string
  anchorSeq: number
  data: unknown
}

/** Read the settled chat nodes off a `chat` view snapshot (from `useChat`). */
export function chatNodes(chat: { nodes?: { values?: () => readonly VoiceChatNode[] } } | undefined): readonly VoiceChatNode[] {
  return chat?.nodes?.values?.() ?? []
}
