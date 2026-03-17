/**
 * Timeline component with episode scrubber and annotation markers.
 *
 * Performance optimizations:
 * - Marker clustering to prevent overlapping renders
 * - Maximum marker limit with smart filtering by severity
 * - Memoized position calculations
 */

import { useCallback, useMemo, useRef } from 'react'

import { cn } from '@/lib/utils'
import {
  useAnnotationStore,
  useEditStore,
  useEpisodeStore,
  useFrameInsertionState,
  usePlaybackControls,
  useTrajectoryAdjustmentState,
} from '@/stores'
import type { Anomaly } from '@/types'

import { TimelineMarker } from './TimelineMarker'

/** Maximum number of markers to render before clustering */
const MAX_VISIBLE_MARKERS = 50

/** Minimum percentage distance between markers before clustering */
const CLUSTER_THRESHOLD_PERCENT = 2

interface ClusteredMarker {
  /** Primary anomaly (highest severity in cluster) */
  anomaly: Anomaly
  /** Position as percentage */
  position: number
  /** Number of anomalies in this cluster */
  count: number
  /** All anomaly IDs in the cluster for tooltip */
  clusterIds: string[]
}

interface TimelineProps {
  /** Additional CSS classes */
  className?: string
}

/**
 * Episode timeline with scrubber and annotation markers.
 *
 * Shows anomalies, data quality issues, and allows seeking through the episode.
 *
 * @example
 * ```tsx
 * <Timeline className="h-16" />
 * ```
 */
export function Timeline({ className }: TimelineProps) {
  const timelineRef = useRef<HTMLDivElement>(null)
  const currentEpisode = useEpisodeStore((state) => state.currentEpisode)
  const { currentFrame, setCurrentFrame } = usePlaybackControls()
  const currentAnnotation = useAnnotationStore((state) => state.currentAnnotation)
  const removedFrames = useEditStore((state) => state.removedFrames)
  const { insertedFrames } = useFrameInsertionState()
  const { trajectoryAdjustments } = useTrajectoryAdjustmentState()

  // Total frames (use episode length or estimate from trajectory data)
  const totalFrames = useMemo(() => {
    if (currentEpisode?.meta.length) {
      return currentEpisode.meta.length
    }
    if (currentEpisode?.trajectoryData.length) {
      return currentEpisode.trajectoryData.length
    }
    return 100 // Default
  }, [currentEpisode])

  // Get anomalies from current annotation - memoized for stable reference
  const anomalies = useMemo(
    () => currentAnnotation?.anomalies.anomalies ?? [],
    [currentAnnotation?.anomalies.anomalies],
  )

  // Get data quality issues - memoized for stable reference
  const dataQualityIssues = useMemo(
    () => currentAnnotation?.dataQuality.issues ?? [],
    [currentAnnotation?.dataQuality.issues],
  )

  // Convert removed frames Set to sorted ranges for efficient rendering
  const removedFrameRanges = useMemo((): Array<[number, number]> => {
    if (removedFrames.size === 0) return []

    const sorted = [...removedFrames].sort((a, b) => a - b)
    const ranges: Array<[number, number]> = []
    let rangeStart = sorted[0]
    let rangeEnd = sorted[0]

    for (let i = 1; i < sorted.length; i++) {
      if (sorted[i] === rangeEnd + 1) {
        rangeEnd = sorted[i]
      } else {
        ranges.push([rangeStart, rangeEnd])
        rangeStart = sorted[i]
        rangeEnd = sorted[i]
      }
    }
    ranges.push([rangeStart, rangeEnd])
    return ranges
  }, [removedFrames])

  // Calculate position percentage for a frame - memoized
  const frameToPercent = useCallback(
    (frame: number) => {
      return (frame / totalFrames) * 100
    },
    [totalFrames],
  )

  /**
   * Cluster nearby anomaly markers to prevent overlapping and limit render count.
   * Groups anomalies that are within CLUSTER_THRESHOLD_PERCENT of each other,
   * keeping the highest severity anomaly as the representative.
   */
  const clusteredMarkers = useMemo((): ClusteredMarker[] => {
    if (anomalies.length === 0) return []

    // Calculate positions for all anomalies
    const markersWithPosition = anomalies.map((anomaly) => ({
      anomaly,
      position: ((anomaly.frameRange[0] + anomaly.frameRange[1]) / 2 / totalFrames) * 100,
    }))

    // Sort by position for clustering
    markersWithPosition.sort((a, b) => a.position - b.position)

    // Cluster nearby markers
    const clusters: ClusteredMarker[] = []
    let currentCluster: typeof markersWithPosition = []

    for (const marker of markersWithPosition) {
      if (currentCluster.length === 0) {
        currentCluster.push(marker)
      } else {
        const lastPosition = currentCluster[currentCluster.length - 1].position
        if (marker.position - lastPosition < CLUSTER_THRESHOLD_PERCENT) {
          currentCluster.push(marker)
        } else {
          // Finalize current cluster
          const severityOrder = { high: 0, medium: 1, low: 2 }
          currentCluster.sort(
            (a, b) => severityOrder[a.anomaly.severity] - severityOrder[b.anomaly.severity],
          )

          clusters.push({
            anomaly: currentCluster[0].anomaly,
            position:
              currentCluster.reduce((sum, m) => sum + m.position, 0) / currentCluster.length,
            count: currentCluster.length,
            clusterIds: currentCluster.map((m) => m.anomaly.id),
          })

          currentCluster = [marker]
        }
      }
    }

    // Don't forget the last cluster
    if (currentCluster.length > 0) {
      const severityOrder = { high: 0, medium: 1, low: 2 }
      currentCluster.sort(
        (a, b) => severityOrder[a.anomaly.severity] - severityOrder[b.anomaly.severity],
      )

      clusters.push({
        anomaly: currentCluster[0].anomaly,
        position: currentCluster.reduce((sum, m) => sum + m.position, 0) / currentCluster.length,
        count: currentCluster.length,
        clusterIds: currentCluster.map((m) => m.anomaly.id),
      })
    }

    // If still too many markers, prioritize by severity
    if (clusters.length > MAX_VISIBLE_MARKERS) {
      const severityOrder = { high: 0, medium: 1, low: 2 }
      clusters.sort((a, b) => severityOrder[a.anomaly.severity] - severityOrder[b.anomaly.severity])
      return clusters.slice(0, MAX_VISIBLE_MARKERS)
    }

    return clusters
  }, [anomalies, totalFrames])

  // Memoized handler factory for marker clicks
  const createMarkerClickHandler = useCallback(
    (frameStart: number) => () => {
      setCurrentFrame(frameStart)
    },
    [setCurrentFrame],
  )

  // Handle click on timeline to seek
  const handleTimelineClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!timelineRef.current) return

    const rect = timelineRef.current.getBoundingClientRect()
    const x = e.clientX - rect.left
    const percent = x / rect.width
    const frame = Math.floor(percent * totalFrames)
    setCurrentFrame(Math.max(0, Math.min(frame, totalFrames - 1)))
  }

  // Handle drag on timeline
  const handleMouseMove = (e: React.MouseEvent<HTMLDivElement>) => {
    if (e.buttons !== 1) return // Only on left mouse button
    handleTimelineClick(e)
  }

  // Current position percentage
  const currentPercent = frameToPercent(currentFrame)

  if (!currentEpisode) {
    return (
      <div className={cn('flex items-center justify-center rounded-lg bg-muted', className)}>
        <p className="text-sm text-muted-foreground">No episode selected</p>
      </div>
    )
  }

  return (
    <div className={cn('flex flex-col gap-1', className)}>
      {/* Timeline bar */}
      <div
        ref={timelineRef}
        className="relative h-8 cursor-pointer rounded bg-muted"
        role="slider"
        tabIndex={0}
        aria-valuenow={currentFrame}
        aria-valuemin={0}
        aria-valuemax={totalFrames - 1}
        onClick={handleTimelineClick}
        onKeyDown={(e) => {
          if (e.key === 'ArrowLeft') setCurrentFrame(Math.max(0, currentFrame - 1))
          else if (e.key === 'ArrowRight')
            setCurrentFrame(Math.min(totalFrames - 1, currentFrame + 1))
        }}
        onMouseMove={handleMouseMove}
      >
        {/* Removed frame ranges - shown as striped red overlay */}
        {removedFrameRanges.map(([start, end]) => (
          <div
            key={`removed-${start}-${end}`}
            className="absolute bottom-0 top-0 border-l border-r border-red-500/40 bg-red-500/20"
            style={{
              left: `${frameToPercent(start)}%`,
              width: `${Math.max(frameToPercent(end - start + 1), 0.5)}%`,
              backgroundImage:
                'repeating-linear-gradient(45deg, transparent, transparent 2px, rgba(239, 68, 68, 0.1) 2px, rgba(239, 68, 68, 0.1) 4px)',
            }}
            title={`Removed: frames ${start}-${end}`}
          />
        ))}

        {/* Data quality issue ranges */}
        {dataQualityIssues.map((issue) => {
          if (!issue.affectedFrames) return null
          const [start, end] = issue.affectedFrames
          return (
            <div
              key={`issue-${issue.type}-${issue.severity}-${start}-${end}`}
              className={cn(
                'absolute bottom-0 top-0 opacity-30',
                issue.severity === 'critical'
                  ? 'bg-red-500'
                  : issue.severity === 'major'
                    ? 'bg-orange-500'
                    : 'bg-yellow-500',
              )}
              style={{
                left: `${frameToPercent(start)}%`,
                width: `${frameToPercent(end - start)}%`,
              }}
              title={`${issue.type} (${issue.severity})`}
            />
          )
        })}

        {/* Anomaly markers - virtualized with clustering */}
        {clusteredMarkers.map((cluster) => (
          <TimelineMarker
            key={cluster.anomaly.id}
            anomaly={cluster.anomaly}
            position={cluster.position}
            onClick={createMarkerClickHandler(cluster.anomaly.frameRange[0])}
          />
        ))}

        {/* Inserted frame indicators */}
        {Array.from(insertedFrames.entries()).map(([afterIdx, insertion]) => {
          if (removedFrames.has(afterIdx)) return null
          return (
            <div
              key={`inserted-${afterIdx}`}
              className="absolute bottom-0 top-0 w-2 border-l border-r border-dashed border-blue-400 bg-blue-500/30"
              style={{ left: `${frameToPercent(afterIdx + 1)}%` }}
              title={`Inserted frame (factor: ${insertion.interpolationFactor})`}
            />
          )
        })}

        {/* Trajectory adjustment markers */}
        {Array.from(trajectoryAdjustments.keys()).map((frameIdx) => (
          <div
            key={`traj-adj-${frameIdx}`}
            className="absolute top-0 h-1.5 w-1.5 -translate-x-1/2 cursor-pointer rounded-full bg-orange-500 transition-transform hover:scale-150"
            style={{ left: `${frameToPercent(frameIdx)}%` }}
            title={`Trajectory adjustment at frame ${frameIdx}`}
            role="button"
            tabIndex={0}
            onClick={(e) => {
              e.stopPropagation()
              setCurrentFrame(frameIdx)
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.stopPropagation()
                setCurrentFrame(frameIdx)
              }
            }}
          />
        ))}

        {/* Playhead */}
        <div
          className="absolute bottom-0 top-0 z-10 w-0.5 bg-primary"
          style={{ left: `${currentPercent}%` }}
        >
          {/* Playhead handle */}
          <div className="absolute -top-1 left-1/2 h-3 w-3 -translate-x-1/2 rounded-full bg-primary" />
        </div>
      </div>

      {/* Frame labels */}
      <div className="flex justify-between text-xs text-muted-foreground">
        <span>0</span>
        <span>{Math.floor(totalFrames / 4)}</span>
        <span>{Math.floor(totalFrames / 2)}</span>
        <span>{Math.floor((totalFrames * 3) / 4)}</span>
        <span>{totalFrames}</span>
      </div>

      {/* Legend */}
      {(anomalies.length > 0 ||
        dataQualityIssues.length > 0 ||
        removedFrames.size > 0 ||
        insertedFrames.size > 0 ||
        trajectoryAdjustments.size > 0) && (
        <div className="flex flex-wrap gap-4 text-xs text-muted-foreground">
          {trajectoryAdjustments.size > 0 && (
            <div className="flex items-center gap-1">
              <div className="h-2 w-2 rounded-full bg-orange-500" />
              <span>{trajectoryAdjustments.size} Adjusted</span>
            </div>
          )}
          {removedFrames.size > 0 && (
            <div className="flex items-center gap-1">
              <div className="h-2 w-3 rounded-sm border border-red-500/40 bg-red-500/20" />
              <span>{removedFrames.size} Removed</span>
            </div>
          )}
          {insertedFrames.size > 0 && (
            <div className="flex items-center gap-1">
              <div className="h-2 w-3 rounded-sm border border-dashed border-blue-400 bg-blue-500/30" />
              <span>{insertedFrames.size} Inserted</span>
            </div>
          )}
          {anomalies.length > 0 && (
            <div className="flex items-center gap-1">
              <div className="h-2 w-2 rounded-full bg-red-500" />
              <span>{anomalies.length} Anomalies</span>
            </div>
          )}
          {dataQualityIssues.filter((i) => i.severity === 'critical').length > 0 && (
            <div className="flex items-center gap-1">
              <div className="h-2 w-3 rounded bg-red-500/30" />
              <span>Critical Issues</span>
            </div>
          )}
          {dataQualityIssues.filter((i) => i.severity === 'major').length > 0 && (
            <div className="flex items-center gap-1">
              <div className="h-2 w-3 rounded bg-orange-500/30" />
              <span>Major Issues</span>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
