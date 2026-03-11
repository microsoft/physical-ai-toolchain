/**
 * Subtask timeline track for visualizing frame segments.
 *
 * Renders colored segments below the main timeline to show
 * labeled sub-task regions.
 */

import { useCallback,useMemo } from 'react';

import { cn } from '@/lib/utils';
import { useEpisodeStore,usePlaybackControls, useSubtaskState } from '@/stores';
import type { SubtaskSegment } from '@/types/episode-edit';

import { SubtaskSegmentSlider } from './SubtaskSegmentSlider';

interface SubtaskTimelineTrackProps {
  /** Total frames in the episode */
  totalFrames: number;
  /** Whether editing is enabled */
  editable?: boolean;
  /** Additional CSS classes */
  className?: string;
  /** Callback when a segment is clicked */
  onSegmentClick?: (segment: SubtaskSegment) => void;
  /** Active selected segment id */
  selectedSegmentId?: string | null;
  /** Draft graph range awaiting subtask creation */
  draftRange?: [number, number] | null;
}

/**
 * Timeline track showing sub-task segments.
 *
 * @example
 * ```tsx
 * <SubtaskTimelineTrack
 *   totalFrames={1000}
 *   editable={true}
 *   onSegmentClick={(s) => console.log('Clicked:', s.label)}
 * />
 * ```
 */
export function SubtaskTimelineTrack({
  totalFrames,
  editable = false,
  className,
  onSegmentClick,
  selectedSegmentId,
  draftRange = null,
}: SubtaskTimelineTrackProps) {
  const { subtasks, updateSubtask, validationErrors } = useSubtaskState();
  const { setCurrentFrame } = usePlaybackControls();
  const currentEpisode = useEpisodeStore((state) => state.currentEpisode);

  // Sort segments by start frame for display
  const sortedSegments = useMemo(
    () =>
      [...subtasks].sort((a, b) => a.frameRange[0] - b.frameRange[0]),
    [subtasks]
  );

  // Convert frame index to percentage position
  const frameToPercent = useCallback(
    (frame: number) => (frame / totalFrames) * 100,
    [totalFrames]
  );

  // Handle segment range change from slider
  const handleRangeChange = useCallback(
    (id: string, newRange: [number, number]) => {
      updateSubtask(id, { frameRange: newRange });
    },
    [updateSubtask]
  );

  // Handle segment click - navigate to start frame
  const handleSegmentClick = useCallback(
    (segment: SubtaskSegment) => {
      setCurrentFrame(segment.frameRange[0]);
      onSegmentClick?.(segment);
    },
    [setCurrentFrame, onSegmentClick]
  );

  if (!currentEpisode) {
    return null;
  }

  return (
    <div className={cn('flex flex-col gap-1', className)} data-keep-playback-selection="true">
      {/* Segment track */}
      <div className="relative h-6 bg-muted/50 rounded">
        {draftRange && (
          <div
            className="absolute bottom-0 top-0 rounded border border-dashed border-primary/70 bg-primary/10"
            style={{
              left: `${frameToPercent(draftRange[0])}%`,
              width: `${Math.max(frameToPercent(draftRange[1] - draftRange[0]), 0.5)}%`,
            }}
          />
        )}
        {sortedSegments.map((segment) => {
          const left = frameToPercent(segment.frameRange[0]);
          const width = frameToPercent(
            segment.frameRange[1] - segment.frameRange[0]
          );
          const isSelected = segment.id === selectedSegmentId;

          if (editable) {
            return (
              <SubtaskSegmentSlider
                key={segment.id}
                segment={segment}
                totalFrames={totalFrames}
                onRangeChange={(range) => handleRangeChange(segment.id, range)}
                onClick={() => handleSegmentClick(segment)}
                isActive={isSelected}
              />
            );
          }

          return (
            <button
              key={segment.id}
              className={cn(
                'absolute top-1 bottom-1 rounded-sm transition-opacity hover:opacity-80 cursor-pointer',
                isSelected && 'ring-2 ring-primary ring-offset-1 ring-offset-background',
              )}
              style={{
                left: `${left}%`,
                width: `${Math.max(width, 0.5)}%`,
                backgroundColor: segment.color,
              }}
              onClick={() => handleSegmentClick(segment)}
              title={`${segment.label} (${segment.frameRange[0]}-${segment.frameRange[1]})`}
            >
              <span className="sr-only">{segment.label}</span>
            </button>
          );
        })}
      </div>

      {/* Legend */}
      {sortedSegments.length > 0 && (
        <div className="flex flex-wrap gap-2 text-xs">
          {sortedSegments.map((segment) => (
            <button
              key={segment.id}
              className={cn(
                'flex items-center gap-1 rounded px-1.5 py-0.5 hover:opacity-80 transition-opacity',
                segment.id === selectedSegmentId && 'bg-primary/10 text-primary',
              )}
              onClick={() => handleSegmentClick(segment)}
            >
              <div
                className="w-3 h-3 rounded-sm"
                style={{ backgroundColor: segment.color }}
              />
              <span className="text-muted-foreground">{segment.label}</span>
            </button>
          ))}
        </div>
      )}

      {/* Validation errors */}
      {validationErrors.length > 0 && (
        <div className="text-xs text-destructive">
          {validationErrors.map((error) => (
            <div key={error}>⚠ {error}</div>
          ))}
        </div>
      )}
    </div>
  );
}
