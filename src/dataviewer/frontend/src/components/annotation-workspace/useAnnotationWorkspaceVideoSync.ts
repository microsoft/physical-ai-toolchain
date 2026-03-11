import { type SyntheticEvent, useCallback, useEffect, useRef, useState } from 'react'

import {
  clampFrameToPlaybackRange,
  computeEffectiveFps,
  computeSyncAction,
  resolvePlaybackTick,
  shouldRecoverPlaybackAfterDesync,
  shouldRecoverStalledPlayback,
  shouldRestartPlaybackAfterLoop,
} from '@/lib/playback-utils'
import { getOriginalIndex } from '@/stores/edit-store-frame-utils'
import type { FrameInsertion } from '@/types/episode-edit'

const PLAYBACK_RECOVERY_COOLDOWN_MS = 300

function drawVideoToCanvas(video: HTMLVideoElement, canvas: HTMLCanvasElement): boolean {
  if (!video.videoWidth || !video.videoHeight) return false
  const ctx = canvas.getContext('2d')
  if (!ctx) return false
  if (canvas.width !== video.videoWidth || canvas.height !== video.videoHeight) {
    canvas.width = video.videoWidth
    canvas.height = video.videoHeight
  }
  ctx.drawImage(video, 0, 0)
  return true
}

function drawBitmapToCanvas(bitmap: ImageBitmap, canvas: HTMLCanvasElement): boolean {
  const ctx = canvas.getContext('2d')
  if (!ctx) return false
  if (bitmap.width === 0 || bitmap.height === 0) return false
  if (canvas.width !== bitmap.width || canvas.height !== bitmap.height) {
    canvas.width = bitmap.width
    canvas.height = bitmap.height
  }
  try {
    ctx.drawImage(bitmap, 0, 0)
  } catch {
    // Bitmap was closed between cache swap and draw — safe to skip
    return false
  }
  return true
}

interface UseAnnotationWorkspaceVideoSyncOptions {
  currentFrame: number
  totalFrames: number
  originalFrameIndex: number | null
  activePlaybackRange: [number, number] | null
  playbackRangeStart: number
  playbackRangeEnd: number
  isPlaying: boolean
  playbackSpeed: number
  autoPlay: boolean
  autoLoop: boolean
  shouldLoopPlaybackRange: boolean
  datasetFps: number
  insertedFrames: Map<number, FrameInsertion>
  removedFrames: Set<number>
  videoSrc: string | null
  frameCache?: Map<number, ImageBitmap>
  frameCacheReady?: boolean
  onSetCurrentFrame: (frame: number) => void
  onTogglePlayback: () => void
  onSetFrameWithinPlaybackRange: (frame: number) => void
  onRecordEvent: (channel: string, type: string, data?: Record<string, unknown>) => void
}

export function useAnnotationWorkspaceVideoSync({
  currentFrame,
  totalFrames,
  originalFrameIndex,
  activePlaybackRange,
  playbackRangeStart,
  playbackRangeEnd,
  isPlaying,
  playbackSpeed,
  autoPlay,
  autoLoop,
  shouldLoopPlaybackRange,
  datasetFps,
  insertedFrames,
  removedFrames,
  videoSrc,
  frameCache,
  frameCacheReady = false,
  onSetCurrentFrame,
  onTogglePlayback,
  onSetFrameWithinPlaybackRange,
  onRecordEvent,
}: UseAnnotationWorkspaceVideoSyncOptions) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const displayCanvasRef = useRef<HTMLCanvasElement>(null)
  const currentFrameRef = useRef(0)
  const originalFrameIndexRef = useRef<number | null>(null)
  const playbackSpeedRef = useRef(playbackSpeed)
  const shouldAutoPlayOnMetadataLoadRef = useRef(false)
  const skipNextPlaybackSyncRef = useRef(false)
  const playbackRetryTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const lastPlaybackRecoveryAtRef = useRef(0)
  const rvfcActiveRef = useRef(false)
  const frameCacheRef = useRef<Map<number, ImageBitmap> | undefined>(undefined)
  const frameCacheReadyRef = useRef(false)
  const [videoDuration, setVideoDuration] = useState(0)

  useEffect(() => {
    return () => {
      if (playbackRetryTimeoutRef.current) {
        clearTimeout(playbackRetryTimeoutRef.current)
      }
    }
  }, [])

  currentFrameRef.current = currentFrame
  originalFrameIndexRef.current = originalFrameIndex
  playbackSpeedRef.current = playbackSpeed
  frameCacheRef.current = frameCache
  frameCacheReadyRef.current = frameCacheReady

  const fps = computeEffectiveFps(totalFrames, videoDuration, datasetFps)

  const ensureVideoPlaybackAtTime = useCallback((video: HTMLVideoElement, targetTime: number) => {
    const playbackStartTime = Number.isFinite(video.duration)
      ? Math.max(0, Math.min(targetTime + 0.001, Math.max(video.duration - 0.001, 0)))
      : Math.max(0, targetTime + 0.001)

    if (playbackRetryTimeoutRef.current) {
      clearTimeout(playbackRetryTimeoutRef.current)
      playbackRetryTimeoutRef.current = null
    }

    video.pause()
    video.currentTime = playbackStartTime
    video.playbackRate = playbackSpeedRef.current
    video.play().catch(() => {})
  }, [fps])

  const seekVideoFrame = useCallback((frame: number, range: [number, number] | null, constrainToRange = true) => {
    const nextFrame = constrainToRange
      ? clampFrameToPlaybackRange(frame, totalFrames, range)
      : Math.max(0, Math.min(frame, Math.max(totalFrames - 1, 0)))

    onSetCurrentFrame(nextFrame)

    const video = videoRef.current
    if (!video) {
      return nextFrame
    }

    const targetOriginalFrame = getOriginalIndex(nextFrame, insertedFrames, removedFrames)
    const targetTime = (targetOriginalFrame ?? nextFrame) / fps

    if (Math.abs(video.currentTime - targetTime) > 0.5 / fps) {
      video.currentTime = targetTime
    }

    if (isPlaying) {
      ensureVideoPlaybackAtTime(video, targetTime)
    }

    return nextFrame
  }, [ensureVideoPlaybackAtTime, fps, insertedFrames, isPlaying, onSetCurrentFrame, removedFrames, totalFrames])

  const handleResumePlayback = useCallback((nextFrame: number) => {
    requestAnimationFrame(() => {
      const video = videoRef.current
      if (!video) {
        return
      }

      const targetOriginalFrame = getOriginalIndex(nextFrame, insertedFrames, removedFrames)
      const targetTime = (targetOriginalFrame ?? nextFrame) / fps

      ensureVideoPlaybackAtTime(video, targetTime)
    })
  }, [ensureVideoPlaybackAtTime, fps, insertedFrames, removedFrames])

  useEffect(() => {
    shouldAutoPlayOnMetadataLoadRef.current = autoPlay
  }, [autoPlay, totalFrames, videoSrc])

  useEffect(() => {
    if (!videoSrc && shouldAutoPlayOnMetadataLoadRef.current && !isPlaying) {
      shouldAutoPlayOnMetadataLoadRef.current = false
      onTogglePlayback()
    }
  }, [isPlaying, onTogglePlayback, videoSrc])

  const syncVideoElementPlayback = useCallback((video: HTMLVideoElement) => {
    const action = computeSyncAction(
      isPlaying,
      playbackSpeedRef.current,
      currentFrameRef.current,
      totalFrames,
      originalFrameIndexRef.current,
      fps,
      video.currentTime,
      playbackRangeStart,
      playbackRangeEnd,
    )

    onRecordEvent('playback', 'sync-action', {
      action: action.kind,
      currentFrame: currentFrameRef.current,
      playbackRangeStart,
      playbackRangeEnd,
      isPlaying,
      autoLoop,
      shouldLoopPlaybackRange,
      videoCurrentTime: Number(video.currentTime.toFixed(3)),
    })

    switch (action.kind) {
      case 'restart':
        onSetFrameWithinPlaybackRange(playbackRangeStart)
        ensureVideoPlaybackAtTime(video, playbackRangeStart / fps)
        break
      case 'seek-and-play':
        ensureVideoPlaybackAtTime(video, action.seekTo)
        break
      case 'play':
        ensureVideoPlaybackAtTime(video, video.currentTime)
        break
      case 'pause':
        video.pause()
        break
    }
  }, [autoLoop, ensureVideoPlaybackAtTime, fps, isPlaying, onRecordEvent, onSetFrameWithinPlaybackRange, playbackRangeEnd, playbackRangeStart, shouldLoopPlaybackRange, totalFrames])

  const syncVideoElementPlaybackRef = useRef(syncVideoElementPlayback)
  syncVideoElementPlaybackRef.current = syncVideoElementPlayback

  const handleLoadedMetadata = useCallback((event: SyntheticEvent<HTMLVideoElement>) => {
    const video = event.currentTarget

    setVideoDuration(video.duration)
    onRecordEvent('playback', 'loaded-metadata', {
      duration: Number(video.duration.toFixed(3)),
      isPlaying,
      shouldAutoPlayOnMetadataLoad: shouldAutoPlayOnMetadataLoadRef.current,
    })

    if (isPlaying) {
      skipNextPlaybackSyncRef.current = true
      syncVideoElementPlaybackRef.current(video)
      return
    }

    if (shouldAutoPlayOnMetadataLoadRef.current) {
      shouldAutoPlayOnMetadataLoadRef.current = false
      onTogglePlayback()
    }
  }, [isPlaying, onRecordEvent, onTogglePlayback])

  useEffect(() => {
    const video = videoRef.current
    if (!video || !videoSrc) {
      return
    }

    if (skipNextPlaybackSyncRef.current) {
      skipNextPlaybackSyncRef.current = false
      return
    }

    syncVideoElementPlaybackRef.current(video)
  }, [isPlaying, videoSrc])

  useEffect(() => {
    if (!isPlaying) {
      if (playbackRetryTimeoutRef.current) {
        clearTimeout(playbackRetryTimeoutRef.current)
        playbackRetryTimeoutRef.current = null
      }
      const video = videoRef.current
      if (video) {
        video.pause()
      }
      return
    }

    let rafId: number
    let disposed = false
    let lastFrame = -1
    let lastTimestamp: number | null = null
    let virtualTime = currentFrameRef.current / fps
    let lastAdvancingVideoTime = -1
    let lastAdvancingVideoTimeAt = Date.now()

    const tick = (timestamp: number) => {
      if (disposed) return

      const video = videoRef.current
      const cache = frameCacheRef.current
      const cacheReady = frameCacheReadyRef.current && cache && cache.size > 0

      if (cacheReady) {
        // Cache-driven playback: virtual time clock + cached frame draw
        if (video && !video.paused) video.pause()

        if (lastTimestamp !== null) {
          const expectedFrame = Math.floor(virtualTime * fps)
          if (Math.abs(currentFrameRef.current - expectedFrame) > 1) {
            virtualTime = currentFrameRef.current / fps
          }

          virtualTime += ((timestamp - lastTimestamp) / 1000) * playbackSpeedRef.current
          const nextFrame = Math.floor(virtualTime * fps)
          const resolved = resolvePlaybackTick(nextFrame, totalFrames, activePlaybackRange, shouldLoopPlaybackRange)

          if (resolved.shouldStop) {
            onSetCurrentFrame(resolved.frame)
            onTogglePlayback()
            return
          }

          if (resolved.frame !== lastFrame) {
            lastFrame = resolved.frame
            onSetCurrentFrame(resolved.frame)
            const canvas = displayCanvasRef.current
            const bitmap = cache.get(resolved.frame)
            if (canvas && bitmap) drawBitmapToCanvas(bitmap, canvas)
          }

          if (resolved.frame < nextFrame) {
            virtualTime = resolved.frame / fps
          }
        }

        lastTimestamp = timestamp
      } else if (video) {
        const nextFrame = Math.floor(video.currentTime * fps)
        const resolved = resolvePlaybackTick(nextFrame, totalFrames, activePlaybackRange, shouldLoopPlaybackRange)
        const now = Date.now()

        if (video.currentTime !== lastAdvancingVideoTime) {
          lastAdvancingVideoTime = video.currentTime
          lastAdvancingVideoTimeAt = now
        }

        if (shouldRecoverPlaybackAfterDesync(
          isPlaying,
          video.paused,
          now - lastPlaybackRecoveryAtRef.current,
          PLAYBACK_RECOVERY_COOLDOWN_MS,
        ) || shouldRecoverStalledPlayback(
          isPlaying,
          video.paused,
          video.currentTime,
          lastAdvancingVideoTime,
          now - lastAdvancingVideoTimeAt,
          PLAYBACK_RECOVERY_COOLDOWN_MS,
        )) {
          lastPlaybackRecoveryAtRef.current = now
          lastAdvancingVideoTime = -1
          lastAdvancingVideoTimeAt = now
          onRecordEvent('playback', 'desync-recover', {
            currentFrame: resolved.frame,
            nextFrame,
            videoCurrentTime: Number(video.currentTime.toFixed(3)),
            playbackRangeStart,
            playbackRangeEnd,
            autoLoop,
            shouldLoopPlaybackRange,
          })
          ensureVideoPlaybackAtTime(video, resolved.frame / fps)
        }

        if (resolved.frame !== lastFrame) {
          lastFrame = resolved.frame
          onSetCurrentFrame(resolved.frame)
        }

        if (resolved.shouldStop) {
          if (isPlaying) {
            onTogglePlayback()
          }

          video.currentTime = resolved.frame / fps
          video.pause()
          return
        }

        if (resolved.frame !== nextFrame) {
          const didLoop = shouldRestartPlaybackAfterLoop(
            nextFrame,
            resolved.frame,
            activePlaybackRange,
            shouldLoopPlaybackRange,
          )

          if (didLoop) {
            onRecordEvent('playback', 'range-loop', {
              rangeStart: playbackRangeStart,
              rangeEnd: playbackRangeEnd,
              reportedFrame: nextFrame,
              resolvedFrame: resolved.frame,
              autoLoop,
              shouldLoopPlaybackRange,
            })
          }

          video.currentTime = resolved.frame / fps
        }

        if (!rvfcActiveRef.current) {
          const canvas = displayCanvasRef.current
          if (canvas) drawVideoToCanvas(video, canvas)
        }
      } else if (!videoSrc) {
        if (lastTimestamp !== null) {
          const expectedFrame = Math.floor(virtualTime * fps)
          if (Math.abs(currentFrameRef.current - expectedFrame) > 1) {
            virtualTime = currentFrameRef.current / fps
          }

          virtualTime += ((timestamp - lastTimestamp) / 1000) * playbackSpeedRef.current
          const nextFrame = Math.floor(virtualTime * fps)
          const resolved = resolvePlaybackTick(nextFrame, totalFrames, activePlaybackRange, shouldLoopPlaybackRange)

          if (resolved.shouldStop) {
            onSetCurrentFrame(resolved.frame)
            onTogglePlayback()
            return
          }

          if (resolved.frame !== lastFrame) {
            lastFrame = resolved.frame
            onSetCurrentFrame(resolved.frame)
          }

          if (resolved.frame < nextFrame) {
            virtualTime = resolved.frame / fps
          }
        }

        lastTimestamp = timestamp
      }

      rafId = requestAnimationFrame(tick)
    }

    rafId = requestAnimationFrame(tick)
    return () => {
      disposed = true
      cancelAnimationFrame(rafId)
      if (playbackRetryTimeoutRef.current) {
        clearTimeout(playbackRetryTimeoutRef.current)
        playbackRetryTimeoutRef.current = null
      }
    }
  }, [activePlaybackRange, autoLoop, ensureVideoPlaybackAtTime, fps, isPlaying, onRecordEvent, onSetCurrentFrame, onTogglePlayback, playbackRangeEnd, playbackRangeStart, shouldLoopPlaybackRange, totalFrames, videoSrc])

  useEffect(() => {
    const video = videoRef.current
    if (video && isPlaying) {
      video.playbackRate = playbackSpeed
    }
  }, [isPlaying, playbackSpeed])

  useEffect(() => {
    if (frameCacheReady) return
    const video = videoRef.current
    if (!video || isPlaying) {
      return
    }

    const targetTime = (originalFrameIndex ?? currentFrame) / fps
    if (Math.abs(video.currentTime - targetTime) > 0.5 / fps) {
      video.currentTime = targetTime
    }
  }, [currentFrame, fps, frameCacheReady, isPlaying, originalFrameIndex])

  const handleVideoEnded = useCallback(() => {
    onRecordEvent('playback', 'video-ended', {
      playbackRangeStart,
      playbackRangeEnd,
      autoLoop,
      shouldLoopPlaybackRange,
    })

    if (shouldLoopPlaybackRange) {
      const video = videoRef.current
      onSetFrameWithinPlaybackRange(playbackRangeStart)

      if (video) {
        ensureVideoPlaybackAtTime(video, playbackRangeStart / fps)
      }

      return
    }

    if (isPlaying) {
      onTogglePlayback()
    }

    onSetFrameWithinPlaybackRange(playbackRangeEnd)
  }, [autoLoop, ensureVideoPlaybackAtTime, fps, isPlaying, onRecordEvent, onSetFrameWithinPlaybackRange, onTogglePlayback, playbackRangeEnd, playbackRangeStart, shouldLoopPlaybackRange])

  // RVFC-based forward-only canvas rendering during playback (disabled when cache is ready)
  useEffect(() => {
    if (frameCacheReady) {
      rvfcActiveRef.current = false
      return
    }

    const video = videoRef.current
    const canvas = displayCanvasRef.current
    if (!video || !canvas || !videoSrc || !isPlaying) {
      rvfcActiveRef.current = false
      return
    }

    if (!('requestVideoFrameCallback' in video)) {
      rvfcActiveRef.current = false
      return
    }

    rvfcActiveRef.current = true
    let lastForwardTime = -1
    let disposed = false

    const handleSeeking = () => { lastForwardTime = -1 }
    video.addEventListener('seeking', handleSeeking)

    const onVideoFrame = (_now: number, metadata: { mediaTime: number }) => {
      if (disposed) return
      const c = displayCanvasRef.current
      if (c && metadata.mediaTime > lastForwardTime) {
        drawVideoToCanvas(video, c)
        lastForwardTime = metadata.mediaTime
      }
      ;(video as HTMLVideoElement & { requestVideoFrameCallback: (cb: typeof onVideoFrame) => number }).requestVideoFrameCallback(onVideoFrame)
    }

    ;(video as HTMLVideoElement & { requestVideoFrameCallback: (cb: typeof onVideoFrame) => number }).requestVideoFrameCallback(onVideoFrame)

    return () => {
      disposed = true
      rvfcActiveRef.current = false
      video.removeEventListener('seeking', handleSeeking)
    }
  }, [frameCacheReady, isPlaying, videoSrc])

  // Draw cached frame when paused and frame changes
  useEffect(() => {
    if (!frameCacheReady || !frameCache || isPlaying) return
    const canvas = displayCanvasRef.current
    if (!canvas) return
    const bitmap = frameCache.get(currentFrame)
    if (bitmap) drawBitmapToCanvas(bitmap, canvas)
  }, [currentFrame, frameCache, frameCacheReady, isPlaying])

  // Draw to canvas when paused (seeked/loadeddata events) — fallback when cache not ready
  useEffect(() => {
    if (frameCacheReady) return
    const video = videoRef.current
    const canvas = displayCanvasRef.current
    if (!video || !canvas || !videoSrc || isPlaying) return

    const draw = () => drawVideoToCanvas(video, canvas)

    video.addEventListener('seeked', draw)
    video.addEventListener('loadeddata', draw)
    if (video.readyState >= 2) draw()

    return () => {
      video.removeEventListener('seeked', draw)
      video.removeEventListener('loadeddata', draw)
    }
  }, [frameCacheReady, isPlaying, videoSrc])

  return {
    displayCanvasRef,
    handleLoadedMetadata,
    handleResumePlayback,
    handleVideoEnded,
    seekVideoFrame,
    videoRef,
  }
}
