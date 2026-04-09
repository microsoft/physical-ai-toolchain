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
  videoStartTime: number
  insertedFrames: Map<number, FrameInsertion>
  removedFrames: Set<number>
  videoSrc: string | null
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
  videoStartTime,
  insertedFrames,
  removedFrames,
  videoSrc,
  onSetCurrentFrame,
  onTogglePlayback,
  onSetFrameWithinPlaybackRange,
  onRecordEvent,
}: UseAnnotationWorkspaceVideoSyncOptions) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const currentFrameRef = useRef(0)
  const originalFrameIndexRef = useRef<number | null>(null)
  const playbackSpeedRef = useRef(playbackSpeed)
  const shouldAutoPlayOnMetadataLoadRef = useRef(false)
  const skipNextPlaybackSyncRef = useRef(false)
  const playbackRetryTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const lastPlaybackRecoveryAtRef = useRef(0)
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

  const fps = computeEffectiveFps(totalFrames, videoDuration, datasetFps)

  // Frame ↔ video-time helpers accounting for concatenated-video offset
  const frameToVideoTime = useCallback(
    (frame: number) => videoStartTime + frame / fps,
    [fps, videoStartTime],
  )

  const ensureVideoPlaybackAtTime = useCallback(
    (video: HTMLVideoElement, targetTime: number) => {
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
    },
    [fps],
  )

  const seekVideoFrame = useCallback(
    (frame: number, range: [number, number] | null, constrainToRange = true) => {
      const nextFrame = constrainToRange
        ? clampFrameToPlaybackRange(frame, totalFrames, range)
        : Math.max(0, Math.min(frame, Math.max(totalFrames - 1, 0)))

      onSetCurrentFrame(nextFrame)

      const video = videoRef.current
      if (!video) {
        return nextFrame
      }

      const targetOriginalFrame = getOriginalIndex(nextFrame, insertedFrames, removedFrames)
      const targetTime = frameToVideoTime(targetOriginalFrame ?? nextFrame)

      if (Math.abs(video.currentTime - targetTime) > 0.5 / fps) {
        video.currentTime = targetTime
      }

      if (isPlaying) {
        ensureVideoPlaybackAtTime(video, targetTime)
      }

      return nextFrame
    },
    [
      ensureVideoPlaybackAtTime,
      fps,
      frameToVideoTime,
      insertedFrames,
      isPlaying,
      onSetCurrentFrame,
      removedFrames,
      totalFrames,
    ],
  )

  const handleResumePlayback = useCallback(
    (nextFrame: number) => {
      requestAnimationFrame(() => {
        const video = videoRef.current
        if (!video) {
          return
        }

        const targetOriginalFrame = getOriginalIndex(nextFrame, insertedFrames, removedFrames)
        const targetTime = frameToVideoTime(targetOriginalFrame ?? nextFrame)

        ensureVideoPlaybackAtTime(video, targetTime)
      })
    },
    [ensureVideoPlaybackAtTime, frameToVideoTime, insertedFrames, removedFrames],
  )

  useEffect(() => {
    shouldAutoPlayOnMetadataLoadRef.current = autoPlay
  }, [autoPlay, totalFrames, videoSrc])

  useEffect(() => {
    if (!videoSrc && shouldAutoPlayOnMetadataLoadRef.current && !isPlaying) {
      shouldAutoPlayOnMetadataLoadRef.current = false
      onTogglePlayback()
    }
  }, [isPlaying, onTogglePlayback, videoSrc])

  const syncVideoElementPlayback = useCallback(
    (video: HTMLVideoElement) => {
      const action = computeSyncAction(
        isPlaying,
        playbackSpeedRef.current,
        currentFrameRef.current,
        totalFrames,
        originalFrameIndexRef.current,
        fps,
        video.currentTime - videoStartTime,
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
          ensureVideoPlaybackAtTime(video, frameToVideoTime(playbackRangeStart))
          break
        case 'seek-and-play':
          ensureVideoPlaybackAtTime(video, videoStartTime + action.seekTo)
          break
        case 'play':
          ensureVideoPlaybackAtTime(video, video.currentTime)
          break
        case 'pause':
          video.pause()
          break
      }
    },
    [
      autoLoop,
      ensureVideoPlaybackAtTime,
      fps,
      frameToVideoTime,
      isPlaying,
      onRecordEvent,
      onSetFrameWithinPlaybackRange,
      playbackRangeEnd,
      playbackRangeStart,
      shouldLoopPlaybackRange,
      totalFrames,
      videoStartTime,
    ],
  )

  const syncVideoElementPlaybackRef = useRef(syncVideoElementPlayback)
  syncVideoElementPlaybackRef.current = syncVideoElementPlayback

  const handleLoadedMetadata = useCallback(
    (event: SyntheticEvent<HTMLVideoElement>) => {
      const video = event.currentTarget

      setVideoDuration(video.duration)
      onRecordEvent('playback', 'loaded-metadata', {
        duration: Number(video.duration.toFixed(3)),
        isPlaying,
        shouldAutoPlayOnMetadataLoad: shouldAutoPlayOnMetadataLoadRef.current,
        videoStartTime,
      })

      // Seek to the correct offset for concatenated videos
      if (videoStartTime > 0) {
        const targetFrame = currentFrameRef.current
        video.currentTime = videoStartTime + targetFrame / (datasetFps || 20)
      }

      if (isPlaying) {
        skipNextPlaybackSyncRef.current = true
        syncVideoElementPlaybackRef.current(video)
        return
      }

      if (shouldAutoPlayOnMetadataLoadRef.current) {
        shouldAutoPlayOnMetadataLoadRef.current = false
        onTogglePlayback()
      }
    },
    [datasetFps, isPlaying, onRecordEvent, onTogglePlayback, videoStartTime],
  )

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

    const videoTimeToFrameLocal = (time: number) => Math.floor((time - videoStartTime) * fps)

    let lastAdvancingVideoTime = -1
    let lastAdvancingVideoTimeAt = Date.now()

    const tick = (timestamp: number) => {
      if (disposed) return

      const video = videoRef.current

      if (video) {
        const nextFrame = videoTimeToFrameLocal(video.currentTime)
        const resolved = resolvePlaybackTick(
          nextFrame,
          totalFrames,
          activePlaybackRange,
          shouldLoopPlaybackRange,
        )
        const now = Date.now()

        if (video.currentTime !== lastAdvancingVideoTime) {
          lastAdvancingVideoTime = video.currentTime
          lastAdvancingVideoTimeAt = now
        }

        if (
          shouldRecoverPlaybackAfterDesync(
            isPlaying,
            video.paused,
            now - lastPlaybackRecoveryAtRef.current,
            PLAYBACK_RECOVERY_COOLDOWN_MS,
          ) ||
          shouldRecoverStalledPlayback(
            isPlaying,
            video.paused,
            video.currentTime,
            lastAdvancingVideoTime,
            now - lastAdvancingVideoTimeAt,
            PLAYBACK_RECOVERY_COOLDOWN_MS,
          )
        ) {
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
          ensureVideoPlaybackAtTime(video, videoStartTime + resolved.frame / fps)
        }

        if (resolved.frame !== lastFrame) {
          lastFrame = resolved.frame
          onSetCurrentFrame(resolved.frame)
        }

        if (resolved.shouldStop) {
          if (isPlaying) {
            onTogglePlayback()
          }

          video.currentTime = videoStartTime + resolved.frame / fps
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

          video.currentTime = videoStartTime + resolved.frame / fps
        }
      } else if (!videoSrc) {
        // Frame-only playback using a virtual time clock
        if (lastTimestamp !== null) {
          const expectedFrame = Math.floor(virtualTime * fps)
          if (Math.abs(currentFrameRef.current - expectedFrame) > 1) {
            virtualTime = currentFrameRef.current / fps
          }

          virtualTime += ((timestamp - lastTimestamp) / 1000) * playbackSpeedRef.current
          const nextFrame = Math.floor(virtualTime * fps)
          const resolved = resolvePlaybackTick(
            nextFrame,
            totalFrames,
            activePlaybackRange,
            shouldLoopPlaybackRange,
          )

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
  }, [
    activePlaybackRange,
    autoLoop,
    ensureVideoPlaybackAtTime,
    fps,
    isPlaying,
    onRecordEvent,
    onSetCurrentFrame,
    onTogglePlayback,
    playbackRangeEnd,
    playbackRangeStart,
    shouldLoopPlaybackRange,
    totalFrames,
    videoSrc,
    videoStartTime,
  ])

  useEffect(() => {
    const video = videoRef.current
    if (video && isPlaying) {
      video.playbackRate = playbackSpeed
    }
  }, [isPlaying, playbackSpeed])

  // Seek video when paused and frame changes
  useEffect(() => {
    const video = videoRef.current
    if (!video || isPlaying) {
      return
    }

    const targetTime = frameToVideoTime(originalFrameIndex ?? currentFrame)
    if (Math.abs(video.currentTime - targetTime) > 0.5 / fps) {
      video.currentTime = targetTime
    }
  }, [currentFrame, fps, frameToVideoTime, isPlaying, originalFrameIndex])

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
        ensureVideoPlaybackAtTime(video, frameToVideoTime(playbackRangeStart))
      }

      return
    }

    if (isPlaying) {
      onTogglePlayback()
    }

    onSetFrameWithinPlaybackRange(playbackRangeEnd)
  }, [
    autoLoop,
    ensureVideoPlaybackAtTime,
    fps,
    frameToVideoTime,
    isPlaying,
    onRecordEvent,
    onSetFrameWithinPlaybackRange,
    onTogglePlayback,
    playbackRangeEnd,
    playbackRangeStart,
    shouldLoopPlaybackRange,
  ])

  return {
    handleLoadedMetadata,
    handleResumePlayback,
    handleVideoEnded,
    seekVideoFrame,
    videoRef,
  }
}
