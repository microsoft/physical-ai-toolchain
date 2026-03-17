/**
 * Marker component for frame insertion points in the timeline.
 *
 * Displays a clickable marker between frames that allows users
 * to insert interpolated frames at that position.
 */

import { Plus } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

interface FrameInsertionMarkerProps {
  /** Frame index after which this marker appears */
  afterFrameIndex: number
  /** Whether a frame is already inserted at this position */
  isInserted: boolean
  /** Callback when marker is clicked to insert */
  onClick: () => void
  /** Callback to remove an inserted frame */
  onRemove?: () => void
  /** Position as percentage (0-100) */
  position: number
}

export function FrameInsertionMarker({
  afterFrameIndex,
  isInserted,
  onClick,
  onRemove,
  position,
}: FrameInsertionMarkerProps) {
  return (
    <div
      className={cn(
        'absolute top-0 h-full w-1 -translate-x-1/2 cursor-pointer',
        'flex items-center justify-center',
        'group transition-all duration-150 hover:w-4',
        isInserted && 'w-4 bg-blue-500/20',
      )}
      style={{ left: `${position}%` }}
      data-frame-index={afterFrameIndex}
    >
      <Button
        variant="ghost"
        size="icon"
        className={cn(
          'h-5 w-5 rounded-full opacity-0 group-hover:opacity-100',
          'transition-opacity duration-150',
          isInserted && 'bg-blue-500 opacity-100 hover:bg-red-500',
        )}
        onClick={(e) => {
          e.stopPropagation()
          if (isInserted && onRemove) {
            onRemove()
          } else {
            onClick()
          }
        }}
        title={isInserted ? 'Remove inserted frame' : 'Insert frame here'}
      >
        <Plus
          className={cn(
            'h-3 w-3 text-white',
            isInserted && 'rotate-45', // Shows X when inserted
          )}
        />
      </Button>
    </div>
  )
}
