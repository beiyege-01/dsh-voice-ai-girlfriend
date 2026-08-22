/**
 * CompanionWindow: reproduces the original hf-realtime-voice right-side
 * animation in DSH — a full-height column on the right (default 55vw):
 *
 *  - Idle: loops `bg-images` videos, advancing to the next on `ended`.
 *  - Speaking: while the ReplySpeaker is playing, cross-fades in a
 *    `task-videos` video (looping), then fades back to idle.
 *  - Draggable: an inner-edge handle resizes the column (240px–70vw,
 *    persisted) and double-clicking it flips the column to the left edge.
 *  - Toggle: `s2s.voice.companion` ('1'/'0', default on) hides it entirely.
 *
 * pointer-events:none on the column so chat interaction is never blocked;
 * only the drag handle is interactive.
 */
import { memo, useCallback, useEffect, useRef, useState } from 'react'
import type { PropsLocale, PropsRuntime } from '@deepseek-ai/dsh-client-ui-slots'
// Type-only: pulls ui-conversation's SlotMap merge for PropsRuntime resolution.
import type {} from '@deepseek-ai/dsh-client-ui-conversation/client'
import { bridgeBase, dhStatus, dhDiscard, tts, type DhStatus } from '../bridge.ts'
import { readDigitalHuman } from '../DigitalHumanToggle.tsx'
import type { VoiceInjected } from '../contract.ts'
import css from './CompanionWindow.module.css'

const WIDTH_KEY = 's2s.voice.companionW'
const SIDE_KEY = 's2s.voice.companionSide'

const MIN_WIDTH_VW = Math.max(10, 240 / window.innerWidth * 100) // ~240px
const MAX_WIDTH_VW = 70

function readWidth(): number {
  try {
    const value = Number.parseFloat(localStorage.getItem(WIDTH_KEY) ?? '')
    if (Number.isFinite(value) && value >= MIN_WIDTH_VW && value <= MAX_WIDTH_VW) return value
  } catch {
    // fall through to default
  }
  return 55
}

function readSide(): 'left' | 'right' {
  try {
    return localStorage.getItem(SIDE_KEY) === 'left' ? 'left' : 'right'
  } catch {
    return 'right'
  }
}

/** Full props: framework runtime share + `voice` locale seat + injected face. */
export type CompanionWindowProps =
  PropsRuntime<'conversation.input.left'> & PropsLocale<'voice'> & VoiceInjected

/**
 * @param props - framework runtime + locale + injected speaker face.
 */
export const CompanionWindow = memo(function CompanionWindow({ speaker, companion }: CompanionWindowProps) {
  const [visible, setVisible] = useState<boolean>(companion.visible)
  const [widthVw, setWidthVw] = useState<number>(readWidth)
  const [side, setSide] = useState<'left' | 'right'>(readSide)
  const [speaking, setSpeaking] = useState<boolean>(speaker.speaking)
  const [bgVideos, setBgVideos] = useState<string[]>([])
  const [taskVideos, setTaskVideos] = useState<string[]>([])
  const [bgIndex, setBgIndex] = useState(0)
  const [taskIndex, setTaskIndex] = useState(0)
  // Digital human: bridge task state + the video currently being played.
  const [dh, setDh] = useState<DhStatus | null>(null)
  const [dhPlaying, setDhPlaying] = useState(false)
  // Mirror of dhPlaying for the mount-time poll closure: driveDh is captured
  // by the interval effect ([] deps), so reading the state variable there
  // would always see the initial value — every poll would think playback is
  // idle and restart the current segment (the "loops a segment" bug).
  const dhPlayingRef = useRef(false)
  const setDhPlayingBoth = useCallback((v: boolean) => {
    dhPlayingRef.current = v
    setDhPlaying(v)
  }, [])
  const idleRef = useRef<HTMLVideoElement | null>(null)
  const speakRef = useRef<HTMLVideoElement | null>(null)
  const dhRef = useRef<HTMLVideoElement | null>(null)
  // Codes already handled (played / fallback-spoken / stale / discarded).
  const handledDhRef = useRef(new Set<string>())
  // When we started waiting for the current generation (code + timestamp).
  const waitingDhRef = useRef<{ code: string; at: number } | null>(null)
  // Segmented video playlist: pending absolute URLs, owner task code, played count.
  const dhQueueRef = useRef<string[]>([])
  const dhQueueCodeRef = useRef('')
  const dhQueuePlayedRef = useRef(0)
  const dragRef = useRef<{ startX: number; startWidth: number; current: number } | null>(null)

  // Follow the shared companion visibility (the toggle flips it live).
  useEffect(() => {
    return companion.subscribe(() => setVisible(companion.visible))
  }, [companion])

  // Load media lists from the bridge on mount, then re-poll every 30 s so
  // videos dropped into the folders are picked up without a page refresh.
  // Only list CHANGES update state (the playing video is not restarted when
  // nothing changed).
  const mediaJsonRef = useRef('')
  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const base = bridgeBase()
        const [bg, task] = await Promise.all([
          fetch(`${base}/api/media/bg-images`).then(r => r.json() as Promise<{ media: { name: string; type: string }[] }>),
          fetch(`${base}/api/media/task-videos`).then(r => r.json() as Promise<{ videos: string[] }>),
        ])
        if (cancelled) return
        const json = JSON.stringify([bg.media, task.videos])
        if (json === mediaJsonRef.current) return
        mediaJsonRef.current = json
        setBgVideos(bg.media.filter(m => m.type === 'video').map(m => `${base}/media/bg-images/${encodeURIComponent(m.name)}`))
        setTaskVideos(task.videos.map(name => `${base}/media/task-videos/${encodeURIComponent(name)}`))
      } catch (err) {
        console.error('[ui-voice] companion media list failed:', err)
      }
    }
    void load()
    const timer = window.setInterval(load, 30000)
    // 外部通知（PersonaToggle 切换待机动画时触发）：立即重新拉取，不等 30s 轮询。
    const onPersonaChange = () => { void load() }
    window.addEventListener('dsh-voice:persona', onPersonaChange)
    return () => {
      cancelled = true
      window.clearInterval(timer)
      window.removeEventListener('dsh-voice:persona', onPersonaChange)
    }
  }, [])

  // Follow the speaker's speaking state.
  useEffect(() => {
    return speaker.subscribe(() => setSpeaking(speaker.speaking))
  }, [speaker])

  // Idle layer: play bgVideos[bgIndex]; advance on ended.
  useEffect(() => {
    const vid = idleRef.current
    const src = bgVideos[bgIndex % bgVideos.length]
    if (vid === null || src === undefined) return
    vid.src = src
    void vid.play().catch(() => {})
  }, [bgIndex, bgVideos])

  // Rotate the speaking clip once per new reply (each speaking start).
  const wasSpeakingRef = useRef(false)
  useEffect(() => {
    if (speaking && !wasSpeakingRef.current && taskVideos.length > 0) {
      setTaskIndex(i => (i + 1) % taskVideos.length)
    }
    wasSpeakingRef.current = speaking
  }, [speaking, taskVideos.length])

  // Speaking layer: play taskVideos[taskIndex] while speaking; stop otherwise.
  useEffect(() => {
    const vid = speakRef.current
    const src = taskVideos[taskIndex % taskVideos.length]
    if (vid === null || src === undefined) return
    if (speaking) {
      vid.src = src
      void vid.play().catch(() => {})
    } else {
      vid.pause()
      vid.currentTime = 0
    }
  }, [speaking, taskIndex, taskVideos])

  // Digital human: poll the bridge task state every 4 s and drive playback.
  //  - done + no pending newer task -> play the video (it carries the TTS
  //    audio muxed by DUIX, so video + voice start together, in sync)
  //  - error / discarded / stale (newer task pending) -> never play
  //  - error with no pending -> fall back to plain TTS so the reply is heard
  //  - waiting too long (>90s) -> give up on the video, discard + speak TTS
  useEffect(() => {
    let cancelled = false
    const poll = async () => {
      const status = await dhStatus()
      if (cancelled || status === null) return
      setDh((prev) => {
        if (prev === null) return status
        if (
          prev.state === status.state &&
          prev.video_url === status.video_url &&
          prev.progress === status.progress &&
          prev.message === status.message
        ) return prev
        return status
      })
      driveDh(status)
    }
    void poll()
    const timer = window.setInterval(poll, 4000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [])

  const stopDh = useCallback(() => {
    const vid = dhRef.current
    if (vid !== null) {
      vid.pause()
      vid.currentTime = 0
    }
    setDhPlayingBoth(false)
  }, [setDhPlayingBoth])

  // 用户占用麦克风/说话（interruptReply）：停止数字人视频播放。
  // 只停播放并作废当前任务的播放队列（防止轮询续播），不调用 dhDiscard、
  // 不删文件——桥接的生成与存取逻辑完全不受影响。
  useEffect(() => {
    return companion.subscribeInterrupt(() => {
      stopDh()
      dhQueueRef.current = []
      dhQueuePlayedRef.current = 0
      if (dhQueueCodeRef.current !== '') {
        handledDhRef.current.add(dhQueueCodeRef.current)
      }
    })
  }, [companion, stopDh])

  /** Play the next queued segment video (or stop when the playlist is done). */
  const playNextDh = useCallback(() => {
    const vid = dhRef.current
    const next = dhQueueRef.current[dhQueuePlayedRef.current]
    if (vid === null || next === undefined) {
      setDhPlayingBoth(false)
      return
    }
    vid.src = next
    void vid.play().catch(() => {})
    setDhPlayingBoth(true)
  }, [setDhPlayingBoth])

  const driveDh = (status: DhStatus): void => {
    const code = status.code
    if (!code) return
    // 数字人开关关闭：不生成/不播放任何数字人视频
    if (!readDigitalHuman()) {
      dhQueueRef.current = []
      stopDh()
      return
    }
    // 新任务：重置播放队列（旧的未播视频作废）
    if (code !== dhQueueCodeRef.current) {
      dhQueueCodeRef.current = code
      dhQueueRef.current = []
      dhQueuePlayedRef.current = 0
      handledDhRef.current.delete(code)
      stopDh()
    }
    if (handledDhRef.current.has(code)) return
    if (status.state === 'discarded') {
      handledDhRef.current.add(code)
      waitingDhRef.current = null
      dhQueueRef.current = []
      stopDh()
      return
    }
    if (status.state === 'error' && status.pending === 0) {
      handledDhRef.current.add(code)
      waitingDhRef.current = null
      dhQueueRef.current = []
      stopDh()
      if (status.text) {
        void tts(status.text)
          .then(wav => speaker.speak(wav))
          .catch(err => console.error('[ui-voice] DH fallback TTS failed:', err))
      }
      return
    }
    if (status.state === 'tts' || status.state === 'generating') {
      const now = Date.now()
      // 等待超时按段数动态计算（每段 ~25s + 60s 基础）：多段任务（5-8 段
      // 需 1-3 分钟）不能被固定 90s 误判为"太慢"而回退 TTS（那就是"只有
      // 声音、没有视频"的根因）。只有 DUIX 真卡死（远超应有时间）才回退。
      const timeoutMs = 60000 + (status.total_segments || 1) * 25000
      if (waitingDhRef.current === null || waitingDhRef.current.code !== code) {
        waitingDhRef.current = { code, at: now }
      } else if (now - waitingDhRef.current.at > timeoutMs) {
        // Generation taking too long: stop waiting, speak the reply directly.
        handledDhRef.current.add(code)
        waitingDhRef.current = null
        dhQueueRef.current = []
        dhDiscard(code)
        stopDh()
        if (status.text) {
          void tts(status.text)
            .then(wav => speaker.speak(wav))
            .catch(err => console.error('[ui-voice] DH timeout TTS failed:', err))
        }
        return
      }
    }
    if (status.state === 'generating' || status.state === 'done') {
      // 注意：不因 pending>0 跳过播放——连续对话里排队几乎是常态，跳过会
      // 导致"生成了却不播"。当前任务该播就播；更晚的任务真正开始时
      // （code 变化）会重置播放队列并接管。
      // 把新产出的小段视频追加进播放队列（去重），有位置就接着播
      const base = bridgeBase()
      for (const v of status.videos ?? []) {
        const url = `${base}${v.video_url}`
        if (!dhQueueRef.current.includes(url)) dhQueueRef.current.push(url)
      }
      if (status.state === 'done') {
        handledDhRef.current.add(code)
        waitingDhRef.current = null
      }
      if (!dhPlayingRef.current && dhQueuePlayedRef.current < dhQueueRef.current.length) {
        playNextDh()
      }
    }
  }

  // A new reply takes over: stop the digital human video so its audio never
  // overlaps the live TTS (a fresh reply generates its own video later).
  useEffect(() => {
    if (!speaking || !dhPlaying) return
    const vid = dhRef.current
    if (vid !== null) {
      vid.pause()
      vid.currentTime = 0
    }
    setDhPlayingBoth(false)
  }, [speaking, dhPlaying, setDhPlayingBoth])

  const onDhEnded = useCallback(() => {
    dhQueuePlayedRef.current += 1
    if (dhQueuePlayedRef.current < dhQueueRef.current.length) {
      playNextDh() // 一段播完马上续接下一段
    } else {
      setDhPlayingBoth(false)
    }
  }, [playNextDh, setDhPlayingBoth])

  const onIdleEnded = useCallback(() => {
    if (bgVideos.length > 1) setBgIndex(i => (i + 1) % bgVideos.length)
  }, [bgVideos.length])

  const onSpeakEnded = useCallback(() => {
    // Keep looping the speaking clip while the reply is still playing.
    const vid = speakRef.current
    if (vid !== null && speaking) {
      vid.currentTime = 0
      void vid.play().catch(() => {})
    }
  }, [speaking])

  // Drag: resize on move (persist the live value), flip side on double-click.
  const beginDrag = useCallback((clientX: number) => {
    dragRef.current = { startX: clientX, startWidth: widthVw, current: widthVw }
    const onMove = (move: PointerEvent) => {
      const drag = dragRef.current
      if (drag === null) return
      const deltaVw = ((move.clientX - drag.startX) / window.innerWidth) * 100
      drag.current = Math.min(MAX_WIDTH_VW, Math.max(MIN_WIDTH_VW, drag.startWidth + (side === 'right' ? -deltaVw : deltaVw)))
      setWidthVw(drag.current)
    }
    const onUp = () => {
      const drag = dragRef.current
      dragRef.current = null
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      if (drag !== null) {
        try {
          localStorage.setItem(WIDTH_KEY, String(drag.current))
        } catch {
          // ignore
        }
      }
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
  }, [widthVw, side])

  const flipSide = useCallback(() => {
    setSide((previous) => {
      const next = previous === 'right' ? 'left' : 'right'
      try {
        localStorage.setItem(SIDE_KEY, next)
      } catch {
        // ignore
      }
      return next
    })
  }, [])

  const dhBusy = dh !== null && (dh.state === 'tts' || dh.state === 'generating')
  if (!visible || (bgVideos.length === 0 && taskVideos.length === 0 && !dhPlaying && !dhBusy)) return null

  return (
    <div
      className={side === 'right' ? css.companion : `${css.companion} ${css.left}`}
      style={{ width: `${widthVw}vw`, right: side === 'right' ? 0 : undefined, left: side === 'left' ? 0 : undefined }}
      aria-hidden="true"
    >
      {bgVideos.length > 0 && (
        <video ref={idleRef} className={speaking || dhPlaying ? `${css.video} ${css.hidden}` : css.video} muted playsInline preload="auto" onEnded={onIdleEnded} />
      )}
      <video ref={dhRef} className={dhPlaying ? css.video : `${css.video} ${css.hidden}`} playsInline preload="auto" onEnded={onDhEnded} />
      {taskVideos.length > 0 && (
        <video ref={speakRef} className={speaking ? css.video : `${css.video} ${css.hidden}`} muted playsInline preload="auto" onEnded={onSpeakEnded} />
      )}
      {dhBusy && readDigitalHuman() && (
        <div className={css.dhCaption}>
          {dh.state === 'tts' ? '语音合成中…' : dh.message || '数字人生成中…'}
        </div>
      )}
      <div
        className={css.handle}
        onPointerDown={(event) => {
          event.preventDefault()
          beginDrag(event.clientX)
        }}
        onDoubleClick={flipSide}
        title="拖动调宽,双击换边"
      />
    </div>
  )
})
