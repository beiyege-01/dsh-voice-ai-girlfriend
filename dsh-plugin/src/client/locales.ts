/** `voice` namespace dictionaries. */

/** Simplified Chinese dictionary (the key-set source of truth). */
export const zh = {
  'mic.title': '语音输入',
  'mic.listening': '正在聆听…再点一下停止',
  'mic.transcribing': '识别中…',
  'mic.error': '语音输入不可用',
  'toggle.onHint': '开启语音朗读',
  'toggle.offHint': '关闭语音朗读',
  'companion.onHint': '显示女友窗',
  'companion.offHint': '隐藏女友窗',
  'interrupt.onHint': '插话模式：说话打断当前回复并立即发送（点击切换为排队）',
  'interrupt.offHint': '排队模式：当前回复结束后自动发送，连续对话（点击切换为插话）',
  'qqpush.onHint': '开启 QQ 推送（回复自动发到 QQ）',
  'qqpush.offHint': '关闭 QQ 推送（回复不再发到 QQ）',
  'dh.onHint': '开启数字人（回复等数字人视频生成后 TTS+画面同步播放）',
  'dh.offHint': '关闭数字人（去掉视频生成，直接用接近即时的语音朗读回复）',
} satisfies Record<string, string>

/** The voice namespace key union. */
export type VoiceKey = keyof typeof zh

/** English dictionary, checked complete against the zh key set. */
export const en = {
  'mic.title': 'Voice input',
  'mic.listening': 'Listening… click again to stop',
  'mic.transcribing': 'Transcribing…',
  'mic.error': 'Voice input unavailable',
  'toggle.onHint': 'Turn on voice reading',
  'toggle.offHint': 'Turn off voice reading',
  'companion.onHint': 'Show companion window',
  'companion.offHint': 'Hide companion window',
  'interrupt.onHint': 'Interrupt mode: speaking cuts the reply and sends immediately (click for queue)',
  'interrupt.offHint': 'Queue mode: auto-sends after the current reply, for continuous conversation (click for interrupt)',
  'qqpush.onHint': 'Turn on QQ push (replies sent to QQ)',
  'qqpush.offHint': 'Turn off QQ push (replies no longer sent to QQ)',
  'dh.onHint': 'Turn on digital human (reply waits for the lip-synced video, then plays TTS + video together)',
  'dh.offHint': 'Turn off digital human (no video generation, near-instant voice reading replies)',
} satisfies Record<VoiceKey, string>
