/**
 * Subtask segment slider with dual thumbs for range editing.
 *
 * Uses the shared slider wrapper for accessible range selection.
 */

import { useCallback } from 'react'

import { Slider, SliderRange, SliderThumb, SliderTrack } from '@/components/ui/slider'
import { cn } from '@/lib/utils'
import type { SubtaskSegment } from '@/types/episode-edit'

interface SubtaskSegmentSliderProps {
  /** The segment to display/edit */
  segment: SubtaskSegment
  /** Total frames in the episode */
  totalFrames: number
  /** Callback when range changes */
  onRangeChange: (range: [number, number]) => void
  /** Callback when segment is clicked */
  onClick?: () => void
  /** Additional CSS classes */
  className?: string
  /** Whether the segment is the active selection */
  isActive?: boolean
}

/**
 * Dual-thumb slider for editing a subtask segment's frame range.
 *
 * @example
 * ```tsx
 * <SubtaskSegmentSlider
 *   segment={segment}
 *   totalFrames={1000}
 *   onRangeChange={(range) => updateSegment(segment.id, { frameRange: range })}
 * />
 * ```
 */
export function SubtaskSegmentSlider({
  segment,
  totalFrames,
  onRangeChange,
  onClick,
  className,
  isActive = false,
}: SubtaskSegmentSliderProps) {
  const handleValueChange = useCallback(
    (values: number[]) => {
      if (values.length === 2) {
        onRangeChange([values[0], values[1]])
      }
    },
    [onRangeChange],
  )

  return (
    <Slider
      className={cn('absolute top-1 bottom-1 touch-none select-none', className)}
      style={{
        left: 0,
        right: 0,
      }}
      value={[segment.frameRange[0], segment.frameRange[1]]}
      onValueChange={handleValueChange}
      min={0}
      max={totalFrames}
      step={1}
      minStepsBetweenThumbs={1}
    >
      <SliderTrack className="h-full w-full rounded-xs bg-transparent">
        <SliderRange
          className={cn(
            'absolute h-full cursor-pointer rounded-xs transition-opacity hover:opacity-90',
            isActive && 'ring-primary ring-offset-background ring-2 ring-offset-1',
          )}
          style={{ backgroundColor: segment.color }}
          onClick={onClick}
        />
      </SliderTrack>

      {/* Start thumb */}
      <SliderThumb
        className={cn(
          'h-4 w-2 rounded-xs border-2 shadow-xs',
          'focus:ring-ring focus:ring-2 focus:ring-offset-2 focus:outline-hidden',
          'cursor-ew-resize transition-transform hover:scale-110',
        )}
        style={{ borderColor: segment.color }}
        aria-label={`${segment.label} start frame`}
      />

      {/* End thumb */}
      <SliderThumb
        className={cn(
          'h-4 w-2 rounded-xs border-2 shadow-xs',
          'focus:ring-ring focus:ring-2 focus:ring-offset-2 focus:outline-hidden',
          'cursor-ew-resize transition-transform hover:scale-110',
        )}
        style={{ borderColor: segment.color }}
        aria-label={`${segment.label} end frame`}
      />
    </Slider>
  )
}
