/**
 * Detection timeline with click-to-navigate.
 */

import { useMemo } from 'react'

import type { DetectionResult } from '@/types/detection'

interface DetectionTimelineProps {
  detectionsPerFrame: DetectionResult[]
  totalFrames: number
  currentFrame: number
  onFrameClick: (frame: number) => void
}

export function DetectionTimeline({
  detectionsPerFrame,
  totalFrames,
  currentFrame,
  onFrameClick,
}: DetectionTimelineProps) {
  // Build density map
  const densityMap = useMemo(() => {
    const map = new Map<number, number>()
    let maxCount = 0
    detectionsPerFrame.forEach((result) => {
      map.set(result.frame, result.detections.length)
      maxCount = Math.max(maxCount, result.detections.length)
    })
    return { map, maxCount }
  }, [detectionsPerFrame])

  // Find frames with detections for quick navigation
  const framesWithDetections = useMemo(() => {
    return detectionsPerFrame
      .filter((r) => r.detections.length > 0)
      .map((r) => r.frame)
      .sort((a, b) => a - b)
  }, [detectionsPerFrame])

  const handleClick = (e: React.MouseEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const x = e.clientX - rect.left
    const frame = Math.floor((x / rect.width) * totalFrames)
    onFrameClick(Math.max(0, Math.min(totalFrames - 1, frame)))
  }

  const handleMarkerClick = (frame: number, e: React.MouseEvent) => {
    e.stopPropagation()
    onFrameClick(frame)
  }

  return (
    <div className="space-y-2">
      <div className="flex justify-between text-xs text-muted-foreground">
        <span>Frame 0</span>
        <span>Detection Density ({framesWithDetections.length} frames with detections)</span>
        <span>Frame {totalFrames - 1}</span>
      </div>
      <div
        className="relative h-10 cursor-pointer rounded bg-muted"
        role="slider"
        tabIndex={0}
        aria-valuenow={currentFrame}
        aria-valuemin={0}
        aria-valuemax={totalFrames - 1}
        onClick={handleClick}
        onKeyDown={(e) => {
          if (e.key === 'Enter') handleClick(e as unknown as React.MouseEvent<HTMLDivElement>)
        }}
      >
        {/* Detection density bars */}
        {Array.from({ length: Math.min(totalFrames, 200) }).map((_, i) => {
          // Sample frames for display if many frames
          const frameIdx = Math.floor((i / Math.min(totalFrames, 200)) * totalFrames)
          const count = densityMap.map.get(frameIdx) || 0
          const height = densityMap.maxCount > 0 ? (count / densityMap.maxCount) * 100 : 0
          return (
            <div
              key={`density-${frameIdx}`}
              className="absolute bottom-0 bg-blue-500/50 transition-colors hover:bg-blue-500/70"
              style={{
                left: `${(frameIdx / totalFrames) * 100}%`,
                width: `${Math.max(1, 100 / Math.min(totalFrames, 200))}%`,
                height: `${height}%`,
              }}
              title={`Frame ${frameIdx}: ${count} detection${count !== 1 ? 's' : ''}`}
            />
          )
        })}

        {/* Current frame indicator */}
        <div
          className="absolute bottom-0 top-0 z-10 w-0.5 bg-red-500"
          style={{ left: `${(currentFrame / totalFrames) * 100}%` }}
        />

        {/* Detection event markers */}
        {framesWithDetections.slice(0, 50).map((frame) => (
          <div
            key={frame}
            className="z-5 absolute top-0 h-2 w-1 cursor-pointer bg-green-500 transition-all hover:h-3"
            style={{ left: `${(frame / totalFrames) * 100}%` }}
            role="button"
            tabIndex={0}
            onClick={(e) => handleMarkerClick(frame, e)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') onFrameClick(frame)
            }}
            title={`Jump to frame ${frame}`}
          />
        ))}
      </div>

      {/* Quick navigation buttons */}
      <div className="flex flex-wrap gap-2">
        {framesWithDetections.slice(0, 10).map((frame) => (
          <button
            key={frame}
            onClick={() => onFrameClick(frame)}
            className={`rounded px-2 py-1 text-xs transition-colors ${
              currentFrame === frame ? 'bg-blue-500 text-white' : 'bg-muted hover:bg-muted/80'
            }`}
          >
            F{frame}
          </button>
        ))}
        {framesWithDetections.length > 10 && (
          <span className="py-1 text-xs text-muted-foreground">
            +{framesWithDetections.length - 10} more
          </span>
        )}
      </div>
    </div>
  )
}
