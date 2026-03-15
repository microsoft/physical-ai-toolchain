/**
 * Episode preview card for batch annotation grid.
 *
 * Performance: Wrapped in React.memo to prevent re-renders when sibling cards
 * change selection state. Only re-renders when own props change.
 */

import { AlertTriangle, Check, Play, Star } from 'lucide-react'
import { memo, useCallback, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import type { EpisodeMeta, TaskCompletenessRating } from '@/types'

interface EpisodePreviewCardProps {
  /** Episode metadata */
  episode: EpisodeMeta
  /** Episode index in the dataset */
  index: number
  /** Whether the episode is selected */
  isSelected: boolean
  /** Callback when selection toggled */
  onToggleSelect: (index: number, shiftKey: boolean) => void
  /** Callback for quick rating */
  onQuickRate: (index: number, rating: TaskCompletenessRating) => void
  /** Callback to open episode in viewer */
  onOpen: (index: number) => void
  /** Thumbnail URL if available */
  thumbnailUrl?: string
}

/**
 * Compact preview card for batch annotation grid.
 *
 * Performance: Memoized to prevent re-renders when other cards in the grid change.
 *
 * @example
 * ```tsx
 * <EpisodePreviewCard
 *   episode={episode}
 *   index={0}
 *   isSelected={isSelected}
 *   onToggleSelect={handleToggle}
 *   onQuickRate={handleQuickRate}
 *   onOpen={handleOpen}
 * />
 * ```
 */
export const EpisodePreviewCard = memo(function EpisodePreviewCard({
  episode,
  index,
  isSelected,
  onToggleSelect,
  onQuickRate,
  onOpen,
  thumbnailUrl,
}: EpisodePreviewCardProps) {
  const [isHovered, setIsHovered] = useState(false)

  // Memoized callbacks to prevent recreating functions on re-render
  const handleClick = useCallback(
    (e: React.MouseEvent) => {
      if (e.target instanceof HTMLButtonElement) return
      onToggleSelect(index, e.shiftKey)
    },
    [onToggleSelect, index],
  )

  const handleMouseEnter = useCallback(() => setIsHovered(true), [])
  const handleMouseLeave = useCallback(() => setIsHovered(false), [])

  const handleOpen = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation()
      onOpen(index)
    },
    [onOpen, index],
  )

  const handleRateSuccess = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation()
      onQuickRate(index, 'success')
    },
    [onQuickRate, index],
  )

  const handleRatePartial = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation()
      onQuickRate(index, 'partial')
    },
    [onQuickRate, index],
  )

  const handleRateFailure = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation()
      onQuickRate(index, 'failure')
    },
    [onQuickRate, index],
  )

  const annotationStatus = episode.annotationStatus
  const hasAnnotation = annotationStatus !== 'pending'

  return (
    <Card
      className={cn(
        'relative cursor-pointer transition-all hover:shadow-md',
        isSelected && 'ring-2 ring-primary',
        hasAnnotation && 'border-green-200',
      )}
      onClick={handleClick}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
    >
      {/* Selection checkbox overlay */}
      <div
        className={cn(
          'absolute left-2 top-2 z-10 flex h-5 w-5 items-center justify-center rounded border-2 transition-colors',
          isSelected
            ? 'border-primary bg-primary text-primary-foreground'
            : 'border-muted-foreground/50 bg-background/80',
        )}
      >
        {isSelected && <Check className="h-3 w-3" />}
      </div>

      {/* Annotation status badge */}
      {hasAnnotation && (
        <div className="absolute right-2 top-2 z-10">
          <span
            className={cn(
              'rounded-full px-1.5 py-0.5 text-xs font-medium',
              annotationStatus === 'complete'
                ? 'bg-green-100 text-green-700'
                : annotationStatus === 'in-progress'
                  ? 'bg-yellow-100 text-yellow-700'
                  : 'bg-gray-100 text-gray-700',
            )}
          >
            {annotationStatus}
          </span>
        </div>
      )}

      {/* Thumbnail */}
      <div className="relative aspect-video overflow-hidden rounded-t-lg bg-muted">
        {thumbnailUrl ? (
          <img src={thumbnailUrl} alt={`Episode ${index}`} className="h-full w-full object-cover" />
        ) : (
          <div className="flex h-full w-full items-center justify-center text-muted-foreground">
            <Play className="h-8 w-8" />
          </div>
        )}

        {/* Hover overlay with open button */}
        {isHovered && (
          <div className="absolute inset-0 flex items-center justify-center bg-black/50">
            <Button variant="secondary" size="sm" onClick={handleOpen}>
              <Play className="mr-1 h-4 w-4" />
              Open
            </Button>
          </div>
        )}
      </div>

      <CardContent className="p-3">
        {/* Episode info */}
        <div className="mb-2 flex items-center justify-between">
          <span className="text-sm font-medium">Episode {index}</span>
          <span className="text-xs text-muted-foreground">{episode.length} frames</span>
        </div>

        {/* Task name if available */}
        {episode.task && (
          <p className="mb-2 truncate text-xs text-muted-foreground">{episode.task}</p>
        )}

        {/* Quick rating buttons */}
        <div className="flex gap-1">
          <Button
            variant="outline"
            size="sm"
            className="h-7 flex-1 text-xs"
            onClick={handleRateSuccess}
          >
            <Check className="mr-1 h-3 w-3 text-green-500" />S
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="h-7 flex-1 text-xs"
            onClick={handleRatePartial}
          >
            <Star className="mr-1 h-3 w-3 text-yellow-500" />P
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="h-7 flex-1 text-xs"
            onClick={handleRateFailure}
          >
            <AlertTriangle className="mr-1 h-3 w-3 text-red-500" />F
          </Button>
        </div>
      </CardContent>
    </Card>
  )
})
