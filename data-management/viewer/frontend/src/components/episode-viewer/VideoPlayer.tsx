/**
 * Video player component for episode camera feeds.
 *
 * Supports multiple cameras, custom playback controls, and keyboard shortcuts.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ReactPlayer from 'react-player'

import { ViewerDisplayControls } from '@/components/viewer-display'
import { buildCssFilter } from '@/lib/css-filters'
import { computeEffectiveFps } from '@/lib/playback-utils'
import { cn } from '@/lib/utils'
import { useEpisodeStore, usePlaybackControls, useViewerDisplay } from '@/stores'

import { CameraSelector } from './CameraSelector'
import { PlaybackControls } from './PlaybackControls'

interface VideoPlayerProps {
  /** Additional CSS classes */
  className?: string
}

/**
 * Video player with multi-camera support and synchronized playback.
 *
 * @example
 * ```tsx
 * <VideoPlayer className="w-full h-96" />
 * ```
 */
export function VideoPlayer({ className }: VideoPlayerProps) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const playerRef = useRef<any>(null)
  const currentEpisode = useEpisodeStore((state) => state.currentEpisode)
  const { currentFrame, isPlaying, playbackSpeed, setCurrentFrame, togglePlayback } =
    usePlaybackControls()

  const [selectedCamera, setSelectedCamera] = useState<string>('')
  const [duration, setDuration] = useState(0)
  const [isReady, setIsReady] = useState(false)
  const { displayAdjustment, isActive: displayActive } = useViewerDisplay()

  const displayFilter = useMemo(
    () => (displayActive ? buildCssFilter(displayAdjustment) : undefined),
    [displayAdjustment, displayActive],
  )

  // Get available cameras from episode data
  const cameras = Object.keys(currentEpisode?.videoUrls ?? {})
  const videoUrl = currentEpisode?.videoUrls[selectedCamera] ?? ''

  // Derive fps from actual video duration to handle metadata mismatches.
  const episodeFrameCount = currentEpisode?.meta.length ?? 0
  const fps = computeEffectiveFps(episodeFrameCount, duration, 30)

  // Select first camera by default
  useEffect(() => {
    if (cameras.length > 0 && !selectedCamera) {
      setSelectedCamera(cameras[0])
    }
  }, [cameras, selectedCamera])

  // Calculate current time from frame
  const currentTime = currentFrame / fps

  // Handle duration update
  const handleDuration = useCallback((dur: number) => {
    setDuration(dur)
  }, [])

  // Handle progress update - using any because react-player types are complex
  const handleProgress = useCallback(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (state: any) => {
      if (isPlaying) {
        const frame = Math.floor(state.playedSeconds * fps)
        setCurrentFrame(frame)
      }
    },
    [fps, isPlaying, setCurrentFrame],
  )

  // Handle ready state
  const handleReady = useCallback(() => {
    setIsReady(true)
  }, [])

  // Seek to frame when currentFrame changes externally
  useEffect(() => {
    if (playerRef.current && isReady && !isPlaying) {
      playerRef.current.seekTo(currentTime, 'seconds')
    }
  }, [currentFrame, currentTime, isReady, isPlaying])

  // When starting playback, ensure the player is at the correct position
  // to prevent restart-from-beginning when the video ended.
  useEffect(() => {
    if (playerRef.current && isReady && isPlaying) {
      const internalPlayer = playerRef.current.getInternalPlayer()
      if (internalPlayer && typeof internalPlayer.currentTime === 'number') {
        const targetTime = currentFrame / fps
        if (Math.abs(internalPlayer.currentTime - targetTime) > 0.5 / fps) {
          playerRef.current.seekTo(targetTime, 'seconds')
        }
      }
    }
    // Only trigger on play state transitions, not frame changes during playback
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isPlaying, isReady])

  // Keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Ignore if typing in an input
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) {
        return
      }

      switch (e.key) {
        case ' ':
          e.preventDefault()
          togglePlayback()
          break
        case 'ArrowLeft':
          e.preventDefault()
          setCurrentFrame(Math.max(0, currentFrame - 1))
          break
        case 'ArrowRight':
          e.preventDefault()
          setCurrentFrame(currentFrame + 1)
          break
        case 'ArrowUp':
          e.preventDefault()
          setCurrentFrame(Math.max(0, currentFrame - 10))
          break
        case 'ArrowDown':
          e.preventDefault()
          setCurrentFrame(currentFrame + 10)
          break
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [currentFrame, setCurrentFrame, togglePlayback])

  // Total frames
  const totalFrames = Math.floor(duration * fps)

  if (!currentEpisode) {
    return (
      <div className={cn('bg-muted flex items-center justify-center rounded-lg', className)}>
        <p className="text-muted-foreground">No episode selected</p>
      </div>
    )
  }

  return (
    <div className={cn('flex flex-col gap-2', className)}>
      {/* Camera selector */}
      <div className="flex items-center justify-between">
        <CameraSelector
          cameras={cameras}
          selectedCamera={selectedCamera}
          onSelectCamera={setSelectedCamera}
        />
        <span className="text-muted-foreground text-sm">
          Frame {currentFrame} / {totalFrames}
        </span>
      </div>

      {/* Viewer display settings */}
      <ViewerDisplayControls />

      {/* Video player */}
      <div
        className="relative aspect-video overflow-hidden rounded-lg bg-black"
        style={displayFilter ? { filter: displayFilter } : undefined}
      >
        {videoUrl ? (
          <ReactPlayer
            ref={playerRef}
            url={videoUrl}
            playing={isPlaying}
            playbackRate={playbackSpeed}
            width="100%"
            height="100%"
            onDuration={handleDuration}
            onProgress={handleProgress}
            onReady={handleReady}
            progressInterval={1000 / fps}
            controls={false}
          />
        ) : (
          <div className="absolute inset-0 flex items-center justify-center">
            <p className="text-white/60">No video available for this camera</p>
          </div>
        )}
      </div>

      {/* Playback controls */}
      <PlaybackControls
        currentFrame={currentFrame}
        totalFrames={totalFrames}
        duration={duration}
        fps={fps}
      />
    </div>
  )
}
