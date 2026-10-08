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
import { recordDiagnosticEvent } from '@/lib/playback-diagnostics'
import { computeEffectiveFps } from '@/lib/playback-utils'

interface AnnotationWorkspacePlaybackCardProps {
  compact?: boolean
  canvasRef: RefObject<HTMLCanvasElement | null>
  videoRef: RefObject<HTMLVideoElement | null>
  videoSrc: string | null
  /**
   * Available video URLs; only selected cameras are mounted.
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
  previewUnavailable?: boolean
  frameImageUrl: string | null
  frameImageUrls?: Record<string, string>
  cameras: string[]
  selectedCamera: string | null
  selectedCameras?: string[]
  onSelectionChange?: (cameras: string[]) => void
  videoTimeWindows?: Record<string, [number, number]>
  originalFrameIndex?: number | null
  sourceFrameCount?: number
  datasetFps?: number
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
  previewUnavailable = false,
  frameImageUrl,
  frameImageUrls = {},
  cameras,
  selectedCamera,
  selectedCameras,
  onSelectionChange,
  videoTimeWindows,
  originalFrameIndex = currentFrame,
  sourceFrameCount = totalFrames,
  datasetFps = 30,
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

  const videoEntries = useMemo<Array<{ camera: string; url: string }>>(() => {
    if (!videoUrls) {
      return videoSrc && selectedCamera ? [{ camera: selectedCamera, url: videoSrc }] : []
    }
    return Object.entries(videoUrls)
      .filter(
        ([camera, url]) => Boolean(url) && (selectedCameras ?? [selectedCamera]).includes(camera),
      )
      .map(([camera, url]) => ({ camera, url }))
  }, [selectedCamera, selectedCameras, videoSrc, videoUrls])
  const videoElements = useRef(new Map<string, HTMLVideoElement>())
  const stopped = useRef(false)
  const [failedCameras, setFailedCameras] = useState<string[]>([])
  useEffect(() => {
    stopped.current = !isPlaying
  }, [isPlaying])
  const stopGroup = useCallback(() => {
    for (const video of videoElements.current.values()) video.pause()
    if (isPlaying && !stopped.current) {
      stopped.current = true
      onTogglePlayback()
    }
  }, [isPlaying, onTogglePlayback])
  const failCamera = useCallback(
    (camera: string) => {
      recordDiagnosticEvent('playback', 'camera-preview-unavailable', { camera, status: 'failed' })
      if (camera === selectedCamera) {
        setVideoLoaded(true)
        setShowVideoLoading(false)
      }
      setFailedCameras((previous) => (previous.includes(camera) ? previous : [...previous, camera]))
      stopGroup()
    },
    [selectedCamera, stopGroup],
  )
  const syncFollower = useCallback(
    (camera: string, video: HTMLVideoElement, metadataLoaded = false) => {
      if (camera === selectedCamera || (!metadataLoaded && video.readyState < 1)) return
      const window = videoTimeWindows?.[camera]
      const primaryWindow = selectedCamera ? videoTimeWindows?.[selectedCamera] : undefined
      const primaryFps = computeEffectiveFps(
        sourceFrameCount,
        primaryWindow ? primaryWindow[1] - primaryWindow[0] : (videoRef.current?.duration ?? 0),
        datasetFps,
      )
      const cameraFps = computeEffectiveFps(
        sourceFrameCount,
        window ? window[1] - window[0] : video.duration,
        datasetFps,
      )
      const target = (window?.[0] ?? 0) + (originalFrameIndex ?? currentFrame) / cameraFps
      const end = window?.[1] ?? video.duration
      const bounded = Number.isFinite(end) && end > 0 ? Math.min(target, end - 0.001) : target
      if (Math.abs(video.currentTime - bounded) > 0.5 / cameraFps) video.currentTime = bounded
      video.playbackRate = (playbackSpeed * primaryFps) / cameraFps
      if (isPlaying && !stopped.current && !document.hidden && originalFrameIndex !== null) {
        if (video.paused)
          void video.play().catch((cause: unknown) => {
            if (cause instanceof DOMException && cause.name === 'AbortError') return
            if (!stopped.current && videoElements.current.get(camera) === video) failCamera(camera)
          })
      } else video.pause()
    },
    [
      selectedCamera,
      videoTimeWindows,
      sourceFrameCount,
      videoRef,
      datasetFps,
      originalFrameIndex,
      currentFrame,
      playbackSpeed,
      isPlaying,
      failCamera,
    ],
  )
  useEffect(() => {
    for (const [camera, video] of videoElements.current) syncFollower(camera, video)
  }, [syncFollower, videoEntries])
  useEffect(() => {
    const onVisibility = () => {
      if (document.hidden) stopGroup()
    }
    document.addEventListener('visibilitychange', onVisibility)
    return () => document.removeEventListener('visibilitychange', onVisibility)
  }, [stopGroup])
  const videoRefs = useMemo(
    () =>
      Object.fromEntries(
        videoEntries.map(({ camera }) => [
          camera,
          (element: HTMLVideoElement | null) => {
            const previous = videoElements.current.get(camera)
            if (previous && previous !== element) {
              previous.pause()
              videoElements.current.delete(camera)
              if (videoRef.current === previous) videoRef.current = null
            }
            if (element) {
              videoElements.current.set(camera, element)
              if (camera === selectedCamera) videoRef.current = element
            }
          },
        ]),
      ),
    [selectedCamera, videoEntries, videoRef],
  )

  const hasAnyVideo = videoEntries.length > 0
  const displayCameras = selectedCameras ?? (selectedCamera ? [selectedCamera] : [])
  const frameEntries = displayCameras.filter(
    (camera) =>
      !videoEntries.some((entry) => entry.camera === camera) &&
      (hasAnyVideo || camera !== selectedCamera) &&
      frameImageUrls[camera],
  )
  const unavailableCameras = displayCameras.filter(
    (camera) =>
      !videoEntries.some((entry) => entry.camera === camera) &&
      !frameImageUrls[camera] &&
      !(camera === selectedCamera && (frameImageUrl || interpolatedImageUrl)),
  )
  return (
    <Card className={compact ? 'mx-auto h-full min-h-0 w-full max-w-[44rem]' : 'shrink-0'}>
      <CardContent className={compact ? 'flex h-full min-h-0 flex-col p-3' : 'p-4'}>
        <div className="flex items-center justify-between gap-2">
          <CameraSelector
            cameras={cameras}
            selectedCamera={selectedCamera ?? ''}
            onSelectCamera={onSelectCamera}
            selectedCameras={selectedCameras}
            onSelectionChange={onSelectionChange}
          />
          <ViewerDisplayControls />
        </div>
        <div
          data-testid={compact ? 'trajectory-compact-media-frame' : undefined}
          className={
            displayCameras.length > 1
              ? 'relative mt-2 grid min-h-0 grid-cols-1 gap-1 overflow-y-auto rounded-lg bg-black sm:grid-cols-2'
              : compact
                ? 'relative mx-auto mt-2 flex aspect-video max-h-[18rem] min-h-0 w-full max-w-[40rem] items-center justify-center overflow-hidden rounded-lg bg-black'
                : 'relative mt-2 flex aspect-video items-center justify-center overflow-hidden rounded-lg bg-black'
          }
        >
          <canvas ref={canvasRef} className="hidden" />

          {hasAnyVideo ? (
            videoEntries.map(({ camera, url }) => {
              const isActive = camera === selectedCamera
              return (
                <div key={camera} className="relative w-full min-w-0">
                  <video
                    key={camera}
                    ref={videoRefs[camera]}
                    data-camera={camera}
                    src={url}
                    onEnded={isActive ? onVideoEnded : stopGroup}
                    onError={() => failCamera(camera)}
                    onLoadedMetadata={(event) => {
                      setFailedCameras((previous) => previous.filter((failed) => failed !== camera))
                      if (isActive) handleVideoLoadedMetadata(event)
                      else syncFollower(camera, event.currentTarget, true)
                    }}
                    muted
                    playsInline
                    preload="metadata"
                    className="m-auto aspect-video max-h-full w-full min-w-0 object-contain"
                    style={displayFilter ? { filter: displayFilter } : undefined}
                    aria-label={camera}
                  />
                  {videoEntries.length > 1 && (
                    <span className="absolute top-1 left-1 max-w-full truncate bg-black/70 px-1 text-xs text-white">
                      {camera}
                    </span>
                  )}
                  {failedCameras.includes(camera) && (
                    <p
                      role="status"
                      className="absolute inset-0 flex items-center justify-center bg-black/80 p-2 text-sm text-white"
                    >
                      {camera}: video unavailable
                    </p>
                  )}
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
              onLoad={() => {
                setImageLoaded(true)
                setFailedCameras((previous) =>
                  previous.filter((camera) => camera !== selectedCamera),
                )
              }}
              onError={() => {
                setImageLoaded(true)
                if (selectedCamera) failCamera(selectedCamera)
              }}
            />
          ) : (
            <span className="text-white">
              Frame {currentFrame + 1} of {totalFrames}
            </span>
          )}

          {frameEntries.map((camera) => (
            <div key={camera} className="relative w-full min-w-0">
              <img
                src={frameImageUrls[camera]}
                alt={`${camera} frame ${currentFrame}`}
                className="aspect-video w-full object-contain"
                style={displayFilter ? { filter: displayFilter } : undefined}
                onLoad={() =>
                  setFailedCameras((previous) => previous.filter((failed) => failed !== camera))
                }
                onError={() => failCamera(camera)}
              />
              {failedCameras.includes(camera) && (
                <p role="status" className="text-white">
                  {camera}: preview unavailable
                </p>
              )}
            </div>
          ))}
          {unavailableCameras.map((camera) => (
            <div
              key={camera}
              role="status"
              className="flex aspect-video items-center justify-center p-2 text-sm text-white"
            >
              {camera}: preview unavailable
            </div>
          ))}
          {!hasAnyVideo && selectedCamera && failedCameras.includes(selectedCamera) && (
            <p
              role="status"
              className="absolute inset-0 flex items-center justify-center bg-black/80 text-white"
            >
              {selectedCamera}: preview unavailable
            </p>
          )}

          {isInsertedFrame && (
            <div className="absolute top-2 left-2 rounded-sm bg-blue-500/80 px-2 py-1 text-xs text-white">
              Interpolated Frame
            </div>
          )}

          {(resizeOutput || previewUnavailable || (isInsertedFrame && hasAnyVideo)) && (
            <div
              role="status"
              className="absolute right-2 bottom-2 rounded-sm bg-black/80 px-2 py-1 text-xs text-white"
            >
              Edited preview unavailable; original media
            </div>
          )}

          {videoSrc && !videoLoaded && showVideoLoading && (
            <div
              role="status"
              aria-live="polite"
              className="absolute inset-0 z-10 flex flex-col items-center justify-center bg-black/30"
            >
              <Loader2 className="h-8 w-8 animate-spin text-white" />
              <p className="mt-2 text-sm text-white">Loading video…</p>
            </div>
          )}

          {!videoSrc && frameImageUrl && !imageLoaded && (
            <div
              role="status"
              aria-live="polite"
              className="absolute inset-0 z-10 flex flex-col items-center justify-center bg-black/30"
            >
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
                    aria-label="Playback frame"
                    aria-valuetext={`Frame ${currentFrame + 1} of ${totalFrames}`}
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
          aria-label="Auto-play"
          aria-pressed={autoPlay}
          title={autoPlay ? 'Auto-play on (click to disable)' : 'Auto-play off (click to enable)'}
          className="h-8 w-8"
        >
          <Play className="h-3.5 w-3.5" />
        </Button>
        <Button
          size="icon"
          variant={autoLoop ? 'default' : 'outline'}
          onClick={() => onSetAutoLoop(!autoLoop)}
          aria-label="Loop playback"
          aria-pressed={autoLoop}
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
          aria-pressed={autoPlay}
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
          aria-pressed={autoLoop}
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
