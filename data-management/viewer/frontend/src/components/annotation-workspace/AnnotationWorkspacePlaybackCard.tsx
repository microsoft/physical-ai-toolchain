import { Loader2, Pause, Play, Repeat, RotateCcw, SkipBack, SkipForward } from 'lucide-react'
import {
  type RefObject,
  type SyntheticEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'

import { CameraSelector } from '@/components/episode-viewer'
import { PlaybackControlStrip } from '@/components/playback/PlaybackControlStrip'
import { SpeedControl } from '@/components/playback/SpeedControl'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { ViewerDisplayControls } from '@/components/viewer-display'
import { cn } from '@/lib/utils'

interface AnnotationWorkspacePlaybackCardProps {
  compact?: boolean
  canvasRef: RefObject<HTMLCanvasElement | null>
  videoRef: RefObject<HTMLVideoElement | null>
  videoSrc: string | null
  /**
   * Map of camera name -> video URL for every camera in the current episode.
   * Used to pre-mount a `<video>` element per camera so switching is instant
   * (parallel preload + persistent decoder pipelines).
   */
  videoUrls?: Record<string, string>
  onVideoEnded: () => void
  onLoadedMetadata: (event: SyntheticEvent<HTMLVideoElement>) => void
  displayFilter?: string
  isInsertedFrame: boolean
  interpolatedImageUrl: string | null
  currentFrame: number
  totalFrames: number
  resizeOutput: { width: number; height: number } | null
  frameImageUrl: string | null
  cameras: string[]
  selectedCamera: string | null
  onSelectCamera: (camera: string) => void
  isPlaying: boolean
  onTogglePlayback: () => void
  onStepFrame: (delta: number) => void
  playbackSpeed: number
  onSetPlaybackSpeed: (speed: number) => void
  autoPlay: boolean
  onSetAutoPlay: (enabled: boolean) => void
  autoLoop: boolean
  onSetAutoLoop: (enabled: boolean) => void
  playbackRangeStart: number
  playbackRangeEnd: number
  onSetFrameWithinPlaybackRange: (frame: number) => number
  playbackRangeHighlight: { left: string; width: string } | null
  playbackRangeLabel: string | null
}

export function AnnotationWorkspacePlaybackCard({
  compact = false,
  canvasRef,
  videoRef,
  videoSrc,
  videoUrls,
  onVideoEnded,
  onLoadedMetadata,
  displayFilter,
  isInsertedFrame,
  interpolatedImageUrl,
  currentFrame,
  totalFrames,
  resizeOutput,
  frameImageUrl,
  cameras,
  selectedCamera,
  onSelectCamera,
  isPlaying,
  onTogglePlayback,
  onStepFrame,
  playbackSpeed,
  onSetPlaybackSpeed,
  autoPlay,
  onSetAutoPlay,
  autoLoop,
  onSetAutoLoop,
  playbackRangeStart,
  playbackRangeEnd,
  onSetFrameWithinPlaybackRange,
  playbackRangeHighlight,
  playbackRangeLabel,
}: AnnotationWorkspacePlaybackCardProps) {
  // Extract episode base path from frameImageUrl to detect episode switches
  const episodeBase = useMemo(() => {
    if (!frameImageUrl) return null
    const match = frameImageUrl.match(/^(.*\/frames\/)/)
    return match ? match[1] : frameImageUrl
  }, [frameImageUrl])

  const [imageLoaded, setImageLoaded] = useState(false)
  const [videoLoaded, setVideoLoaded] = useState(false)
  const [showVideoLoading, setShowVideoLoading] = useState(false)

  useEffect(() => {
    if (!videoSrc) {
      setVideoLoaded(false)
      setShowVideoLoading(false)
      return
    }

    // With every camera's <video> pre-mounted, the new active video is
    // typically already loaded (readyState >= HAVE_METADATA) when the user
    // switches cameras. In that case, skip the loading flicker entirely.
    const activeVideo = videoRef.current
    if (activeVideo && activeVideo.readyState >= 1) {
      setVideoLoaded(true)
      setShowVideoLoading(false)
      return
    }

    setVideoLoaded(false)
    setShowVideoLoading(false)
    const timer = setTimeout(() => {
      setShowVideoLoading(true)
    }, 200)

    return () => clearTimeout(timer)
  }, [videoSrc, videoRef])

  const handleVideoLoadedMetadata = useCallback(
    (event: SyntheticEvent<HTMLVideoElement>) => {
      setVideoLoaded(true)
      setShowVideoLoading(false)
      onLoadedMetadata(event)
    },
    [onLoadedMetadata],
  )

  useEffect(() => {
    if (!videoSrc && frameImageUrl) {
      setImageLoaded(false)
    }
  }, [episodeBase, videoSrc])

  // Build the list of videos to mount. Pre-mounting every camera's <video>
  // (with preload="auto") keeps each camera's decode pipeline warm so
  // switching cameras is effectively instant — no fresh HTTP fetch, no
  // decoder cold start. Inactive videos are kept mounted but hidden and
  // never receive play() calls, so they sit on their first frame at zero
  // CPU cost.
  const videoEntries = useMemo<Array<{ camera: string; url: string }>>(() => {
    if (!videoUrls) {
      return videoSrc && selectedCamera ? [{ camera: selectedCamera, url: videoSrc }] : []
    }
    return Object.entries(videoUrls)
      .filter(([, url]) => Boolean(url))
      .map(([camera, url]) => ({ camera, url }))
  }, [selectedCamera, videoSrc, videoUrls])

  // Pause inactive videos when the user switches cameras. We don't restore
  // playback on the previous camera; the active video resumes from the
  // current frame via useAnnotationWorkspaceVideoSync.
  const previousActiveCameraRef = useRef<string | null>(null)
  useEffect(() => {
    const previous = previousActiveCameraRef.current
    if (previous && previous !== selectedCamera) {
      const inactive = document.querySelector<HTMLVideoElement>(
        `video[data-camera="${CSS.escape(previous)}"]`,
      )
      if (inactive && !inactive.paused) {
        inactive.pause()
      }
    }
    previousActiveCameraRef.current = selectedCamera ?? null
  }, [selectedCamera])

  const hasAnyVideo = videoEntries.length > 0
  return (
    <Card className={compact ? 'mx-auto h-full min-h-0 w-full max-w-[44rem]' : 'shrink-0'}>
      <CardContent className={compact ? 'flex h-full min-h-0 flex-col p-3' : 'p-4'}>
        <div className="flex items-center justify-between gap-2">
          <CameraSelector
            cameras={cameras}
            selectedCamera={selectedCamera ?? ''}
            onSelectCamera={onSelectCamera}
          />
          <ViewerDisplayControls />
        </div>
        <div
          data-testid={compact ? 'trajectory-compact-media-frame' : undefined}
          className={
            compact
              ? 'relative mx-auto mt-2 flex aspect-video max-h-[18rem] min-h-0 w-full max-w-[40rem] items-center justify-center overflow-hidden rounded-lg bg-black'
              : 'relative mt-2 flex aspect-video items-center justify-center overflow-hidden rounded-lg bg-black'
          }
        >
          <canvas ref={canvasRef} className="hidden" />

          {hasAnyVideo ? (
            videoEntries.map(({ camera, url }) => {
              const isActive = camera === selectedCamera
              return (
                <video
                  key={camera}
                  ref={isActive ? videoRef : null}
                  data-camera={camera}
                  src={url}
                  onEnded={isActive ? onVideoEnded : undefined}
                  onLoadedMetadata={isActive ? handleVideoLoadedMetadata : undefined}
                  muted
                  playsInline
                  preload="auto"
                  className={cn(
                    'absolute inset-0 m-auto max-h-full max-w-full object-contain',
                    isActive
                      ? 'z-10 opacity-100'
                      : 'pointer-events-none z-0 opacity-0',
                  )}
                  style={isActive && displayFilter ? { filter: displayFilter } : undefined}
                  aria-hidden={!isActive}
                />
              )
            })
          ) : isInsertedFrame && interpolatedImageUrl ? (
            <img
              src={interpolatedImageUrl}
              alt={`Interpolated frame ${currentFrame}`}
              className="max-h-full max-w-full object-contain"
              style={displayFilter ? { filter: displayFilter } : undefined}
            />
          ) : frameImageUrl ? (
            <img
              src={frameImageUrl}
              alt={`Frame ${currentFrame}`}
              className="max-h-full max-w-full object-contain"
              style={displayFilter ? { filter: displayFilter } : undefined}
              onLoad={() => setImageLoaded(true)}
            />
          ) : (
            <span className="text-white">
              Frame {currentFrame + 1} of {totalFrames}
            </span>
          )}

          {isInsertedFrame && (
            <div className="absolute top-2 left-2 rounded-sm bg-blue-500/80 px-2 py-1 text-xs text-white">
              Interpolated Frame
            </div>
          )}

          {resizeOutput && (
            <div className="absolute top-2 right-2 rounded-sm bg-green-600/80 px-2 py-1 text-xs text-white">
              Output: {resizeOutput.width} × {resizeOutput.height}
            </div>
          )}

          {videoSrc && !videoLoaded && showVideoLoading && (
            <div className="absolute inset-0 z-10 flex flex-col items-center justify-center bg-black/30">
              <Loader2 className="h-8 w-8 animate-spin text-white" />
              <p className="mt-2 text-sm text-white">Loading video…</p>
            </div>
          )}

          {!videoSrc && frameImageUrl && !imageLoaded && (
            <div className="absolute inset-0 z-10 flex flex-col items-center justify-center bg-black/30">
              <Loader2 className="h-8 w-8 animate-spin text-white" />
              <p className="mt-2 text-sm text-white">Loading episode…</p>
            </div>
          )}
        </div>

        <div data-keep-playback-selection="true">
          <PlaybackControlStrip
            currentFrame={currentFrame}
            totalFrames={totalFrames}
            className={compact ? 'mt-2' : 'mt-3'}
            controls={
              compact
                ? renderCompactControls({
                  isPlaying,
                  onTogglePlayback,
                  onStepFrame,
                  playbackSpeed,
                  onSetPlaybackSpeed,
                  autoPlay,
                  onSetAutoPlay,
                  autoLoop,
                  onSetAutoLoop,
                  playbackRangeStart,
                  onSetFrameWithinPlaybackRange,
                })
                : renderDefaultControls({
                  isPlaying,
                  onTogglePlayback,
                  onStepFrame,
                  playbackSpeed,
                  onSetPlaybackSpeed,
                  autoPlay,
                  onSetAutoPlay,
                  autoLoop,
                  onSetAutoLoop,
                  playbackRangeStart,
                  onSetFrameWithinPlaybackRange,
                })
            }
            slider={
              <div className="space-y-1">
                <div className="relative">
                  {playbackRangeHighlight && (
                    <div className="bg-muted/60 pointer-events-none absolute inset-y-1 right-0 left-0 rounded-sm">
                      <div
                        className="bg-primary/20 absolute inset-y-0 rounded-sm"
                        style={playbackRangeHighlight}
                      />
                    </div>
                  )}
                  <input
                    type="range"
                    min={playbackRangeStart}
                    max={playbackRangeEnd}
                    value={currentFrame}
                    onChange={(event) =>
                      onSetFrameWithinPlaybackRange(parseInt(event.target.value, 10))
                    }
                    className="relative z-10 w-full"
                  />
                </div>
                {playbackRangeLabel && (
                  <p className="text-muted-foreground text-xs">
                    {playbackRangeLabel}: frames {playbackRangeStart} to {playbackRangeEnd}
                  </p>
                )}
              </div>
            }
          />
        </div>
      </CardContent>
    </Card>
  )
}

interface PlaybackControlsProps {
  isPlaying: boolean
  onTogglePlayback: () => void
  onStepFrame: (delta: number) => void
  playbackSpeed: number
  onSetPlaybackSpeed: (speed: number) => void
  autoPlay: boolean
  onSetAutoPlay: (enabled: boolean) => void
  autoLoop: boolean
  onSetAutoLoop: (enabled: boolean) => void
  playbackRangeStart: number
  onSetFrameWithinPlaybackRange: (frame: number) => number
}

function renderCompactControls({
  isPlaying,
  onTogglePlayback,
  onStepFrame,
  playbackSpeed,
  onSetPlaybackSpeed,
  autoPlay,
  onSetAutoPlay,
  autoLoop,
  onSetAutoLoop,
  playbackRangeStart,
  onSetFrameWithinPlaybackRange,
}: PlaybackControlsProps) {
  return (
    <div
      data-testid="trajectory-compact-controls"
      className="flex w-full items-center justify-between gap-2"
    >
      <div className="flex shrink-0 items-center gap-1">
        <Button
          size="icon"
          onClick={onTogglePlayback}
          aria-label={isPlaying ? 'Pause playback' : 'Play playback'}
          title={isPlaying ? 'Pause playback' : 'Play playback'}
          className="h-8 w-8"
        >
          {isPlaying ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
        </Button>
        <Button
          size="icon"
          variant="outline"
          onClick={() => onStepFrame(-1)}
          disabled={isPlaying}
          aria-label="Previous frame"
          title="Previous frame"
          className="h-8 w-8"
        >
          <SkipBack className="h-4 w-4" />
        </Button>
        <Button
          size="icon"
          variant="outline"
          onClick={() => onStepFrame(1)}
          disabled={isPlaying}
          aria-label="Next frame"
          title="Next frame"
          className="h-8 w-8"
        >
          <SkipForward className="h-4 w-4" />
        </Button>
        <Button
          size="icon"
          variant="outline"
          onClick={() => onSetFrameWithinPlaybackRange(playbackRangeStart)}
          aria-label="Reset playback"
          title="Reset playback"
          className="h-8 w-8"
        >
          <RotateCcw className="h-4 w-4" />
        </Button>
      </div>
      <div className="flex shrink-0 items-center gap-1">
        <SpeedControl speed={playbackSpeed} onSpeedChange={onSetPlaybackSpeed} compact />
        <Button
          size="icon"
          variant={autoPlay ? 'default' : 'outline'}
          onClick={() => onSetAutoPlay(!autoPlay)}
          aria-label="Toggle auto-play"
          title={autoPlay ? 'Auto-play on (click to disable)' : 'Auto-play off (click to enable)'}
          className="h-8 w-8"
        >
          <Play className="h-3.5 w-3.5" />
        </Button>
        <Button
          size="icon"
          variant={autoLoop ? 'default' : 'outline'}
          onClick={() => onSetAutoLoop(!autoLoop)}
          aria-label="Toggle loop playback"
          title={autoLoop ? 'Loop on (click to disable)' : 'Loop off (click to enable)'}
          className="h-8 w-8"
        >
          <Repeat className="h-3.5 w-3.5" />
        </Button>
      </div>
    </div>
  )
}

function renderDefaultControls({
  isPlaying,
  onTogglePlayback,
  onStepFrame,
  playbackSpeed,
  onSetPlaybackSpeed,
  autoPlay,
  onSetAutoPlay,
  autoLoop,
  onSetAutoLoop,
  playbackRangeStart,
  onSetFrameWithinPlaybackRange,
}: PlaybackControlsProps) {
  return (
    <>
      <Button size="sm" onClick={onTogglePlayback} className="min-w-[5rem] gap-1">
        {isPlaying ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
        {isPlaying ? 'Pause' : 'Play'}
      </Button>
      <Button
        size="sm"
        variant="outline"
        onClick={() => onStepFrame(-1)}
        disabled={isPlaying}
        title="Previous frame"
      >
        <SkipBack className="h-4 w-4" />
      </Button>
      <Button
        size="sm"
        variant="outline"
        onClick={() => onStepFrame(1)}
        disabled={isPlaying}
        title="Next frame"
      >
        <SkipForward className="h-4 w-4" />
      </Button>
      <Button
        size="sm"
        variant="outline"
        onClick={() => onSetFrameWithinPlaybackRange(playbackRangeStart)}
      >
        <RotateCcw className="h-4 w-4" />
      </Button>
      <div className="flex flex-wrap items-center gap-2">
        <SpeedControl speed={playbackSpeed} onSpeedChange={onSetPlaybackSpeed} />
      </div>
      <div className="flex flex-wrap items-center gap-1">
        <Button
          size="sm"
          variant={autoPlay ? 'default' : 'outline'}
          onClick={() => onSetAutoPlay(!autoPlay)}
          className="px-2"
          title={autoPlay ? 'Auto-play on (click to disable)' : 'Auto-play off (click to enable)'}
        >
          <Play className="mr-1 h-3 w-3" />
          Auto
        </Button>
        <Button
          size="sm"
          variant={autoLoop ? 'default' : 'outline'}
          onClick={() => onSetAutoLoop(!autoLoop)}
          className="px-2"
          title={autoLoop ? 'Loop on (click to disable)' : 'Loop off (click to enable)'}
        >
          <Repeat className="mr-1 h-3 w-3" />
          Loop
        </Button>
      </div>
    </>
  )
}
