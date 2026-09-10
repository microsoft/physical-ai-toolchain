import {
  Loader2,
  Maximize2,
  Minimize2,
  Pause,
  Play,
  Repeat,
  RotateCcw,
  SkipBack,
  SkipForward,
} from 'lucide-react'
import {
  type RefObject,
  type SyntheticEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'

import {
  CameraSelector,
  type EndEffectorTrajectory,
  EndEffectorTrajectoryPlot,
} from '@/components/episode-viewer'
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
  selectedCameras?: string[]
  onSelectionChange?: (cameras: string[]) => void
  endEffectorTrajectories?: readonly EndEffectorTrajectory[]
  videoWindows?: Record<string, [number, number]>
  datasetFps?: number
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
  selectedCameras,
  onSelectionChange,
  endEffectorTrajectories = [],
  videoWindows = {},
  datasetFps = 30,
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
  const [showEndEffectorView, setShowEndEffectorView] = useState(false)
  const [isMediaExpanded, setIsMediaExpanded] = useState(compact)

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
  const displayedCameras = selectedCameras?.length
    ? selectedCameras
    : selectedCamera
      ? [selectedCamera]
      : []
  const displayedEntries = videoEntries.filter(({ camera }) => displayedCameras.includes(camera))
  const syncSelectedVideos = useCallback(() => {
    if (!selectedCamera) return
    const primary = videoRef.current
    const primaryStart = videoWindows[selectedCamera]?.[0] ?? 0
    const episodeTime = primary
      ? Math.max(0, primary.currentTime - primaryStart)
      : currentFrame / datasetFps

    for (const camera of displayedCameras) {
      if (camera === selectedCamera) continue
      const video = document.querySelector<HTMLVideoElement>(
        `video[data-camera="${CSS.escape(camera)}"]`,
      )
      if (!video) continue
      const start = videoWindows[camera]?.[0] ?? 0
      const end = videoWindows[camera]?.[1]
      const target = end === undefined ? start + episodeTime : Math.min(start + episodeTime, end)
      if (Math.abs(video.currentTime - target) > 0.5 / datasetFps) {
        video.currentTime = target
      }
      video.playbackRate = playbackSpeed
      if (isPlaying) {
        void video.play().catch(() => {})
      } else {
        video.pause()
      }
    }
  }, [
    currentFrame,
    datasetFps,
    displayedCameras,
    isPlaying,
    playbackSpeed,
    selectedCamera,
    videoWindows,
  ])

  useEffect(() => {
    syncSelectedVideos()
  }, [syncSelectedVideos])

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

  const hasAnyVideo = displayedEntries.length > 0
  const mediaViewCount = hasAnyVideo ? displayedEntries.length : 1
  const visibleViewCount = mediaViewCount + (showEndEffectorView ? 1 : 0)
  return (
    <Card
      data-testid="trajectory-playback-card"
      className={cn(
        compact ? 'mx-auto h-full min-h-0 w-full' : 'shrink-0',
        compact && !isMediaExpanded && 'max-w-[44rem]',
      )}
    >
      <CardContent className={compact ? 'flex h-full min-h-0 flex-col p-3' : 'p-4'}>
        <div className="flex items-center justify-between gap-2">
          <CameraSelector
            cameras={cameras}
            selectedCamera={selectedCamera ?? ''}
            onSelectCamera={(camera) => {
              onSelectCamera(camera)
            }}
            selectedCameras={displayedCameras}
            onSelectionChange={(nextCameras) => {
              onSelectionChange?.(nextCameras)
            }}
            endEffectorViewSelected={showEndEffectorView}
            onEndEffectorViewSelectionChange={setShowEndEffectorView}
          />
          <div className="flex items-center gap-1.5">
            {compact && (
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => setIsMediaExpanded((expanded) => !expanded)}
                aria-label={isMediaExpanded ? 'Compact media' : 'Expand media'}
                title={isMediaExpanded ? 'Compact media' : 'Expand media'}
                className="gap-1.5"
              >
                {isMediaExpanded ? (
                  <Minimize2 className="h-3.5 w-3.5" />
                ) : (
                  <Maximize2 className="h-3.5 w-3.5" />
                )}
                <span className="hidden sm:inline">{isMediaExpanded ? 'Compact' : 'Expand'}</span>
              </Button>
            )}
            <ViewerDisplayControls />
          </div>
        </div>
        <div
          data-testid={compact ? 'trajectory-compact-media-frame' : 'trajectory-camera-grid'}
          className={cn(
            'relative mt-2 grid min-h-0 w-full gap-1 overflow-hidden rounded-lg bg-black',
            visibleViewCount === 1 && 'grid-cols-1',
            visibleViewCount === 2 && 'grid-cols-2',
            visibleViewCount === 3 && 'grid-cols-3',
            visibleViewCount >= 4 && 'grid-cols-4',
            compact && 'mx-auto',
            compact && isMediaExpanded && 'max-h-[34rem]',
            compact && isMediaExpanded && visibleViewCount > 1 && 'max-w-none',
            compact && isMediaExpanded && visibleViewCount === 1 && 'max-w-[60rem]',
            compact && !isMediaExpanded && 'max-h-[18rem] max-w-[40rem]',
            showEndEffectorView && 'bg-muted/20',
          )}
          data-camera-count={displayedEntries.length}
          data-view-count={visibleViewCount}
        >
          <canvas ref={canvasRef} className="hidden" />

          {hasAnyVideo ? (
            displayedEntries.map(({ camera, url }) => {
              const isActive = camera === selectedCamera
              return (
                <div key={camera} className="relative aspect-video min-w-0 bg-black">
                  <video
                    ref={isActive ? videoRef : null}
                    data-camera={camera}
                    src={url}
                    onEnded={isActive ? onVideoEnded : undefined}
                    onLoadedMetadata={(event) => {
                      if (isActive) handleVideoLoadedMetadata(event)
                    }}
                    muted
                    playsInline
                    preload="auto"
                    className="h-full w-full object-contain"
                    style={displayFilter ? { filter: displayFilter } : undefined}
                    aria-label={`${formatCameraLabel(camera)} camera view`}
                  />
                  <span className="absolute bottom-1 left-1 rounded-sm bg-black/65 px-1.5 py-0.5 text-xs text-white">
                    {formatCameraLabel(camera)}
                  </span>
                </div>
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

          {showEndEffectorView && (
            <EndEffectorTrajectoryPlot
              trajectories={endEffectorTrajectories}
              currentSampleIndex={currentFrame}
              showHeader={false}
              className="h-full w-full"
              frameClassName="aspect-video h-full border-0"
            />
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

function formatCameraLabel(camera: string): string {
  return camera
    .replace(/^observation\.images\./, '')
    .replaceAll('_', ' ')
    .replace(/\b\w/g, (character) => character.toUpperCase())
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
