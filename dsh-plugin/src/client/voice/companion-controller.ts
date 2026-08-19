/**
 * CompanionController: shared visibility state for the companion window.
 *
 * Created once in apply and injected to both the CompanionWindow (renders the
 * overlay) and the CompanionToggle (flips it), so the toggle takes effect
 * immediately without a store. Persisted to `s2s.voice.companion`.
 */

const COMPANION_KEY = 's2s.voice.companion'

export class CompanionController {
  private listeners = new Set<() => void>()
  private interruptListeners = new Set<() => void>()
  private value: boolean

  constructor() {
    try {
      this.value = localStorage.getItem(COMPANION_KEY) !== '0'
    } catch {
      this.value = true
    }
  }

  get visible(): boolean {
    return this.value
  }

  set visible(next: boolean) {
    if (this.value === next) return
    this.value = next
    try {
      localStorage.setItem(COMPANION_KEY, next ? '1' : '0')
    } catch {
      // ignore
    }
    for (const listener of this.listeners) listener()
  }

  /** Subscribe to visibility changes; returns the unsubscribe function. */
  subscribe(listener: () => void): () => void {
    this.listeners.add(listener)
    return () => {
      this.listeners.delete(listener)
    }
  }

  /**
   * 用户占用麦克风/说话（interruptReply 触发）：通知女友窗停止数字人视频
   * 播放。只停播放，不涉及桥接的生成任务与磁盘文件（生成/存取不受影响）。
   */
  notifyMicInterrupt(): void {
    for (const listener of this.interruptListeners) listener()
  }

  /** 订阅"麦克风占用/用户说话"事件；返回退订函数。 */
  subscribeInterrupt(listener: () => void): () => void {
    this.interruptListeners.add(listener)
    return () => {
      this.interruptListeners.delete(listener)
    }
  }
}
