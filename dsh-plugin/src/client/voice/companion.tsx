/**
 * CompanionWindow: the AI-girlfriend's live panel, shown as a small standalone
 * framed card on the right of the viewport. Inside it the same hf-realtime-voice
 * video pipeline runs:
 *
 *  - Background: two stacked <video> layers crossfade (0.8s) through the
 *    `bg-images` videos (advance on `ended`, plus one hop per assistant reply);
 *    static-image fallback when the folder has no video.
 *  - Foreground: while the ReplySpeaker plays, a framed `task-videos` clip
 *    crossfades in over the background and loops; the digital-human video (has
 *    audio) overlays it when rendering.
 *
 * Layout/robustness notes:
 *  - The card is ALWAYS mounted (the video elements never unmount). When the
 *    companion is toggled off, or there is no media / no digital-human task, it
 *    is hidden with inline `display:none` instead of `return null`. Returning
 *    null would unmount the <video>s, and on re-mount the background would not
 *    be re-wired (its "wired" refs persist), leaving an empty frame after a
 *    single toggle.
 *  - The video layers are shown/hidden with inline `style.opacity` (NOT a
 *    hashed class), so they always paint regardless of css-modules hashing.
 *  - Whenever the media list changes (idle/persona switch or files added), the
 *    background is reset and the new idle group is shown immediately.
 *
 * Drag: the inner-edge handle resizes width (persisted); double-click flips it
 * to the left side. `s2s.voice.companion` ('1'/'0', default on) hides it.
 */
import { memo, useCallback, useEffect, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import type { PropsLocale, PropsRuntime } from '@deepseek-ai/dsh-client-ui-slots'
// Type-only: pulls ui-conversation's SlotMap merge for PropsRuntime resolution.
import type {} from '@deepseek-ai/dsh-client-ui-conversation/client'
import { bridgeBase, dhStatus, dhDiscard, tts, type DhStatus } from '../bridge.ts'
import { readDigitalHuman } from '../DigitalHumanToggle.tsx'
import type { VoiceInjected } from '../contract.ts'
import css from './CompanionWindow.module.css'

const WIDTH_KEY = 's2s.voice.companionW'
const SIDE_KEY = 's2s.voice.companionSide'

const MIN_WIDTH = 180
const MAX_WIDTH = 480
const DEFAULT_WIDTH = 260

function readWidth(): number {
  try {
    const value = Number.parseFloat(localStorage.getItem(WIDTH_KEY) ?? '')
    if (Number.isFinite(value) && value >= MIN_WIDTH && value <= MAX_WIDTH) return value
  } catch {
    // fall through to default
  }
  return DEFAULT_WIDTH
}

function readSide(): 'left' | 'right' {
  try {
    return localStorage.getItem(SIDE_KEY) === 'left' ? 'left' : 'right'
  } catch {
    return 'right'
  }
}

/** The right-fixed sidebar's panel element (better-sidebar & alike), excluding the bottom panel. */
const SIDEBAR_PANEL_SEL = '[data-dsh-panel]:not([data-dsh-bottom-panel])'

/**
 * 右侧固定插件（dsh-better-sidebar 等）展开时占用的右侧宽度（px）。
 * 卡片据此避让到插件左缘，而不是被遮挡。
 */
function useSidebarInset(): number {
  const [inset, setInset] = useState(0)
  useEffect(() => {
    const measure = () => {
      if (document.body.hasAttribute('data-dsh-sidebar-collapsed')) {
        setInset(0)
        return
      }
      const panel = document.querySelector<HTMLElement>(SIDEBAR_PANEL_SEL)
      if (!panel) {
        setInset(0)
        return
      }
      const rect = panel.getBoundingClientRect()
      const w = window.innerWidth
      if (rect.left > 0 && rect.left < w && rect.right >= w - 1) {
        setInset(Math.round(w - rect.left))
      } else {
        setInset(0)
      }
    }
    measure()
    const mo = new MutationObserver(measure)
    mo.observe(document.body, { attributes: true, attributeFilter: ['data-dsh-sidebar-collapsed', 'data-dsh-sidebar-dragging'] })
    let ro: ResizeObserver | null = null
    const bind = () => {
      ro?.disconnect()
      const panel = document.querySelector<HTMLElement>(SIDEBAR_PANEL_SEL)
      if (panel) {
        ro = new ResizeObserver(measure)
        ro.observe(panel)
      }
    }
    bind()
    const iv = window.setInterval(() => {
      if (document.querySelector(SIDEBAR_PANEL_SEL) && ro === null) bind()
    }, 2000)
    return () => {
      mo.disconnect()
      ro?.disconnect()
      window.clearInterval(iv)
    }
  }, [])
  return inset
}

/** A `bg-images` entry in the bridge media list. */
interface BgMedia {
  name: string
  type: 'image' | 'video' | string
}

/** Full props: framework runtime share + `voice` locale seat + injected face. */
export type CompanionWindowProps =
  PropsRuntime<'conversation.input.left'> & PropsLocale<'voice'> & VoiceInjected

/** Inline base for every video layer: absolute, fill the card, crossfade via opacity. */
const VIDEO_STYLE: CSSProperties = {
  position: 'absolute',
  inset: 0,
  width: '100%',
  height: '100%',
  objectFit: 'cover',
  transition: 'opacity 0.8s ease',
  opacity: 0,
}

/**
 * @param props - framework runtime + locale + injected speaker face.
 */
export const CompanionWindow = memo(function CompanionWindow({ speaker, companion }: CompanionWindowProps) {
  const [visible, setVisible] = useState<boolean>(companion.visible)
  const [widthPx, setWidthPx] = useState<number>(readWidth)
  const [side, setSide] = useState<'left' | 'right'>(readSide)
  const [speaking, setSpeaking] = useState<boolean>(speaker.speaking)

  // Background media list (from bg-images) + the task-video list (task-videos).
  const [bgMedia, setBgMedia] = useState<BgMedia[]>([])
  const [taskVideos, setTaskVideos] = useState<string[]>([])
  const [bgImageUrl, setBgImageUrl] = useState('')

  // Digital human: bridge task state + the video currently being played.
  const [dh, setDh] = useState<DhStatus | null>(null)
  const [dhPlaying, setDhPlaying] = useState(false)
  const dhPlayingRef = useRef(false)
  const setDhPlayingBoth = useCallback((v: boolean) => {
    dhPlayingRef.current = v
    setDhPlaying(v)
  }, [])

  // Background crossfade layers.
  const bgA = useRef<HTMLVideoElement | null>(null)
  const bgB = useRef<HTMLVideoElement | null>(null)
  const bgActive = useRef(true)
  const bgTransitioning = useRef(false)
  const bgWired = useRef(false)
  const bgIndexRef = useRef(-1)
  const bgMediaRef = useRef<BgMedia[]>([])

  // Foreground task/avatar frame.
  const taskRef = useRef<HTMLVideoElement | null>(null)
  const taskIndexRef = useRef(-1)
  const taskPlayingRef = useRef(false)
  const taskPendingStopRef = useRef(false)
  const wasSpeakingRef = useRef(false)

  // Digital-human frame.
  const dhRef = useRef<HTMLVideoElement | null>(null)
  const handledDhRef = useRef(new Set<string>())
  const waitingDhRef = useRef<{ code: string; at: number } | null>(null)
  const dhQueueRef = useRef<string[]>([])
  const dhQueueCodeRef = useRef('')
  const dhQueuePlayedRef = useRef(0)
  const dragRef = useRef<{ startX: number; startWidth: number; current: number } | null>(null)

  // ── Background crossfade (ported from hf-realtime-voice main.js) ──────────
  const showBgMedia = useCallback((index: number) => {
    const list = bgMediaRef.current
    const entry = list[index]
    if (!entry) return
    const a = bgA.current
    const b = bgB.current
    if (!a || !b) return
    const src = `${bridgeBase()}/media/bg-images/${encodeURIComponent(entry.name)}`
    if (entry.type === 'video') {
      setBgImageUrl('')
      if (!bgWired.current) {
        // First ever background: show it without a crossfade.
        bgWired.current = true
        const vid = bgActive.current ? a : b
        vid.src = src
        vid.style.opacity = '1'
        vid.load()
        void vid.play().catch(() => {})
        return
      }
      // Cross-fade into the spare layer once it can play (avoids a black frame).
      const from = bgActive.current ? a : b
      const to = bgActive.current ? b : a
      bgTransitioning.current = true
      to.src = src
      to.load()
      const startFade = () => {
        to.removeEventListener('canplay', startFade)
        bgActive.current = !bgActive.current
        from.style.opacity = '0'
        to.style.opacity = '1'
        void to.play().catch(() => {})
        // Release the now-hidden layer's decoder so only ONE video decodes at a
        // time (two full-res streams playing concurrently is what caused the
        // page lag; the hidden layer is reused on the next crossfade).
        window.setTimeout(() => { bgTransitioning.current = false; from.pause() }, 900)
      }
      to.addEventListener('canplay', startFade)
      window.setTimeout(() => { if (bgTransitioning.current) startFade() }, 3000)
      return
    }
    // Image fallback: hide the video layers, show a static background image.
    a.style.opacity = '0'
    b.style.opacity = '0'
    a.pause()
    b.pause()
    bgActive.current = true
    setBgImageUrl(`url("${src}")`)
  }, [])

  const advanceBgVideo = useCallback((): number => {
    const list = bgMediaRef.current
    if (list.length < 2) return -1
    let next = bgIndexRef.current
    for (let step = 0; step < list.length; step++) {
      next = (next + 1) % list.length
      if (list[next].type === 'video') {
        bgIndexRef.current = next
        showBgMedia(next)
        return next
      }
    }
    return -1
  }, [showBgMedia])

  const onBgVideoEnded = useCallback((event: React.SyntheticEvent<HTMLVideoElement>) => {
    if (bgTransitioning.current) return
    const visibleEl = bgActive.current ? bgA.current : bgB.current
    if (event.currentTarget !== visibleEl) return
    advanceBgVideo()
  }, [advanceBgVideo])

  /** Advance the background one video per assistant reply. */
  const rotateBgReply = useCallback(() => {
    const list = bgMediaRef.current
    if (list.some(m => m.type === 'video')) {
      advanceBgVideo()
      return
    }
    if (list.length < 2) return
    bgIndexRef.current = (bgIndexRef.current + 1) % list.length
    showBgMedia(bgIndexRef.current)
  }, [advanceBgVideo, showBgMedia])

  /** Show the FIRST media of the CURRENT list (used on load and on any media list
   *  change, e.g. an idle/persona switch) so the new idle group appears at once. */
  const resetBg = useCallback(() => {
    bgWired.current = false
    bgActive.current = true
    bgTransitioning.current = false
    bgIndexRef.current = 0
    const a = bgA.current
    const b = bgB.current
    if (a) {
      a.pause()
      a.style.opacity = '0'
    }
    if (b) {
      b.pause()
      b.style.opacity = '0'
    }
    if (bgMediaRef.current.length > 0) showBgMedia(0)
  }, [showBgMedia])

  // Load media lists from the bridge on mount, then re-poll every 30 s. Only
  // list CHANGES update state; a change resets the background so a new idle
  // group (persona switch) is shown immediately instead of waiting for `ended`.
  const mediaJsonRef = useRef('')
  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const base = bridgeBase()
        const [bg, task] = await Promise.all([
          fetch(`${base}/api/media/bg-images`).then(r => r.json() as Promise<{ media: BgMedia[] }>),
          fetch(`${base}/api/media/task-videos`).then(r => r.json() as Promise<{ videos: string[] }>),
        ])
        if (cancelled) return
        const media = Array.isArray(bg.media) ? bg.media : []
        const videos = Array.isArray(task.videos) ? task.videos : []
        const json = JSON.stringify([media, videos])
        if (json === mediaJsonRef.current) return
        mediaJsonRef.current = json
        bgMediaRef.current = media
        setBgMedia(media)
        setTaskVideos(videos.map(name => `${base}/media/task-videos/${encodeURIComponent(name)}`))
        resetBg()
      } catch (err) {
        console.error('[ui-voice] companion media list failed:', err)
      }
    }
    void load()
    const timer = window.setInterval(load, 30000)
    const onPersonaChange = () => { void load() }
    window.addEventListener('dsh-voice:persona', onPersonaChange)
    return () => {
      cancelled = true
      window.clearInterval(timer)
      window.removeEventListener('dsh-voice:persona', onPersonaChange)
    }
  }, [resetBg])

  // Follow the shared companion visibility (the toggle flips it live).
  useEffect(() => {
    return companion.subscribe(() => setVisible(companion.visible))
  }, [companion])

  // Follow the speaker's speaking state.
  useEffect(() => {
    return speaker.subscribe(() => setSpeaking(speaker.speaking))
  }, [speaker])

  // ── Foreground task frame: show while speaking, loop, play out on end ─────
  const hideTaskFrame = useCallback(() => {
    const vid = taskRef.current
    if (vid !== null) {
      vid.pause()
      vid.currentTime = 0
      vid.style.opacity = '0'
    }
    taskPlayingRef.current = false
    taskPendingStopRef.current = false
  }, [])

  const onTaskEnded = useCallback(() => {
    if (taskPendingStopRef.current) {
      hideTaskFrame()
      return
    }
    // Reply still going: loop so the animation never freezes on a black frame.
    const vid = taskRef.current
    if (vid !== null) {
      vid.currentTime = 0
      void vid.play().catch(() => {})
    }
  }, [hideTaskFrame])

  useEffect(() => {
    const vid = taskRef.current
    if (speaking && !wasSpeakingRef.current) {
      // New reply started.
      if (taskVideos.length > 0) {
        if (taskPlayingRef.current) vid?.pause()
        taskPendingStopRef.current = false
        taskPlayingRef.current = true
        taskIndexRef.current = (taskIndexRef.current + 1) % taskVideos.length
        const src = taskVideos[taskIndexRef.current]
        if (vid !== null) {
          vid.src = src
          vid.style.opacity = '1'
          vid.load()
          const play = () => {
            vid.removeEventListener('canplay', play)
            void vid.play().catch(() => {})
          }
          vid.addEventListener('canplay', play)
          window.setTimeout(() => {
            if (taskPlayingRef.current && vid.paused) void vid.play().catch(() => {})
          }, 3000)
        }
      }
      // One background hop per reply (original: showNextBgMedia).
      rotateBgReply()
    } else if (!speaking && wasSpeakingRef.current) {
      if (taskPlayingRef.current) {
        taskPendingStopRef.current = true
        window.setTimeout(() => { if (taskPendingStopRef.current) hideTaskFrame() }, 30000)
      } else {
        hideTaskFrame()
      }
      if (taskRef.current === null) hideTaskFrame()
    }
    wasSpeakingRef.current = speaking
  }, [speaking, taskVideos, rotateBgReply, hideTaskFrame])

  // The digital-human video, once playing, owns the foreground: hide any task
  // frame so the two never compete.
  useEffect(() => {
    if (dhPlaying) hideTaskFrame()
  }, [dhPlaying, hideTaskFrame])

  // Digital human: poll the bridge task state every 4 s and drive playback.
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
      vid.style.opacity = '0'
    }
    setDhPlayingBoth(false)
  }, [setDhPlayingBoth])

  // 用户占用麦克风/说话（interruptReply）：停止数字人视频播放。
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
    vid.style.opacity = '1'
    vid.src = next
    void vid.play().catch(() => {})
    setDhPlayingBoth(true)
  }, [setDhPlayingBoth])

  const driveDh = (status: DhStatus): void => {
    const code = status.code
    if (!code) return
    if (!readDigitalHuman()) {
      dhQueueRef.current = []
      stopDh()
      return
    }
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
      const timeoutMs = 60000 + (status.total_segments || 1) * 25000
      if (waitingDhRef.current === null || waitingDhRef.current.code !== code) {
        waitingDhRef.current = { code, at: now }
      } else if (now - waitingDhRef.current.at > timeoutMs) {
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
  // overlaps the live TTS.
  useEffect(() => {
    if (!speaking || !dhPlaying) return
    const vid = dhRef.current
    if (vid !== null) {
      vid.pause()
      vid.currentTime = 0
      vid.style.opacity = '0'
    }
    setDhPlayingBoth(false)
  }, [speaking, dhPlaying, setDhPlayingBoth])

  const onDhEnded = useCallback(() => {
    dhQueuePlayedRef.current += 1
    if (dhQueuePlayedRef.current < dhQueueRef.current.length) {
      playNextDh()
    } else {
      setDhPlayingBoth(false)
      const vid = dhRef.current
      if (vid !== null) vid.style.opacity = '0'
    }
  }, [playNextDh, setDhPlayingBoth])

  // Drag: resize width on move (persist the live value), flip side on double-click.
  const beginDrag = useCallback((clientX: number) => {
    dragRef.current = { startX: clientX, startWidth: widthPx, current: widthPx }
    const onMove = (move: PointerEvent) => {
      const drag = dragRef.current
      if (drag === null) return
      const delta = move.clientX - drag.startX
      drag.current = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, drag.startWidth + (side === 'right' ? -delta : delta)))
      setWidthPx(drag.current)
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
  }, [widthPx, side])

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

  // 右侧固定插件展开时，贴到它的左缘而不是被遮挡。
  const sidebarInset = useSidebarInset()

  const hasBgVideos = bgMedia.some(m => m.type === 'video')
  const hasBgImages = bgMedia.some(m => m.type === 'image')
  const nothing = !hasBgVideos && !hasBgImages && taskVideos.length === 0 && !dhPlaying && !dhBusy
  // Always render the card (videos never unmount); hide it with display:none when
  // the companion is toggled off or there is nothing to show.
  const shown = visible && !nothing

  // Portrait framing: height scales with width (capped to the viewport).
  const heightPx = Math.round(Math.min(Math.max(widthPx * 1.5, 260), Math.min(560, window.innerHeight * 0.7)))

  return (
    <div
      className={side === 'right' ? css.card : `${css.card} ${css.left}`}
      style={{
        position: 'fixed',
        top: '50%',
        transform: 'translateY(-50%)',
        width: `${widthPx}px`,
        height: `${heightPx}px`,
        right: side === 'right' ? (sidebarInset ? sidebarInset + 12 : 12) : undefined,
        left: side === 'left' ? 12 : undefined,
        zIndex: 9999,
        pointerEvents: 'none',
        display: shown ? undefined : 'none',
        backgroundColor: '#0b0c10',
        backgroundImage: bgImageUrl || undefined,
        backgroundSize: 'cover',
        backgroundPosition: 'center',
        border: '1px solid rgba(255,255,255,0.14)',
        borderRadius: 22,
        boxShadow: '0 28px 70px -28px rgba(0,0,0,0.78), 0 0 0 1px rgba(255,255,255,0.04), inset 0 1px 0 rgba(255,255,255,0.06)',
        overflow: 'hidden',
      }}
      aria-hidden="true"
    >
      {/* Background crossfade layers (idle loop) */}
      <video ref={bgA} style={VIDEO_STYLE} muted playsInline preload="auto" onEnded={onBgVideoEnded} />
      <video ref={bgB} style={VIDEO_STYLE} muted playsInline preload="auto" onEnded={onBgVideoEnded} />
      {/* Foreground task/avatar frame (load on demand) */}
      <video ref={taskRef} style={VIDEO_STYLE} muted playsInline preload="metadata" onEnded={onTaskEnded} />
      {/* Digital-human frame (load on demand, carries the TTS audio) */}
      <video ref={dhRef} style={VIDEO_STYLE} playsInline preload="metadata" onEnded={onDhEnded} />
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
