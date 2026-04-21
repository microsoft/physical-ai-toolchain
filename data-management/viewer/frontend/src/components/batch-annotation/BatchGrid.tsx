/**
 * Batch annotation grid component.
 */

import { ChevronLeft, ChevronRight, Grid2X2, Grid3X3, LayoutGrid } from 'lucide-react'
import { useCallback, useMemo, useState } from 'react'

import { Button } from '@/components/ui/button'
import { useBatchSelection } from '@/hooks/use-batch-selection'
import { cn } from '@/lib/utils'
import type { EpisodeMeta, TaskCompletenessRating } from '@/types'

import { BatchActions } from './BatchActions'
import { EpisodePreviewCard } from './EpisodePreviewCard'

interface BatchGridProps {
  /** List of episodes to display */
  episodes: EpisodeMeta[]
  /** Callback for quick rating an episode */
  onQuickRate: (index: number, rating: TaskCompletenessRating) => void
  /** Callback to open episode in viewer */
  onOpenEpisode: (index: number) => void
  /** Callback for batch rating */
  onBatchRate: (indices: number[], rating: TaskCompletenessRating) => Promise<void>
  /** Callback for batch quality */
  onBatchQuality: (indices: number[], score: number) => Promise<void>
  /** Generate thumbnail URL for an episode */
  getThumbnailUrl?: (episode: EpisodeMeta, index: number) => string | undefined
  /** Additional CSS classes */
  className?: string
}

const PAGE_SIZE = 24

/**
 * Grid view for batch annotation of episodes.
 *
 * @example
 * ```tsx
 * <BatchGrid
 *   episodes={episodes}
 *   onQuickRate={handleQuickRate}
 *   onOpenEpisode={handleOpen}
 *   onBatchRate={handleBatchRate}
 *   onBatchQuality={handleBatchQuality}
 * />
 * ```
 */
export function BatchGrid({
  episodes,
  onQuickRate,
  onOpenEpisode,
  onBatchRate,
  onBatchQuality,
  getThumbnailUrl,
  className,
}: BatchGridProps) {
  const [currentPage, setCurrentPage] = useState(0)
  const [columns, setColumns] = useState<2 | 3 | 4>(3)
  const [isProcessing, setIsProcessing] = useState(false)
  const [progress, setProgress] = useState(0)
  const [statusFilter, setStatusFilter] = useState<'all' | 'pending' | 'complete'>('all')

  const {
    selectedIndices,
    selectedCount,
    selectedArray,
    toggleSelection,
    selectRange,
    selectAll,
    clearSelection,
    lastClickedIndex,
  } = useBatchSelection()

  // Filter episodes by status
  const filteredEpisodes = useMemo(() => {
    if (statusFilter === 'all') return episodes
    return episodes.filter((ep) =>
      statusFilter === 'pending'
        ? ep.annotationStatus === 'pending'
        : ep.annotationStatus === 'complete',
    )
  }, [episodes, statusFilter])

  // Pagination
  const totalPages = Math.ceil(filteredEpisodes.length / PAGE_SIZE)
  const paginatedEpisodes = useMemo(() => {
    const start = currentPage * PAGE_SIZE
    return filteredEpisodes.slice(start, start + PAGE_SIZE)
  }, [filteredEpisodes, currentPage])

  // Handle selection with shift-click
  const handleToggleSelect = useCallback(
    (index: number, shiftKey: boolean) => {
      if (shiftKey && lastClickedIndex !== null) {
        selectRange(lastClickedIndex, index)
      } else {
        toggleSelection(index)
      }
    },
    [lastClickedIndex, selectRange, toggleSelection],
  )

  // Handle select all (current page)
  const handleSelectAll = useCallback(() => {
    const start = currentPage * PAGE_SIZE
    const indices = filteredEpisodes.slice(start, start + PAGE_SIZE).map((_, i) => start + i)
    selectAll(indices)
  }, [currentPage, filteredEpisodes, selectAll])

  // Handle batch rating
  const handleBatchRate = useCallback(
    async (rating: TaskCompletenessRating) => {
      setIsProcessing(true)
      setProgress(0)

      try {
        // Process in chunks for progress indication
        const chunkSize = 10
        for (let i = 0; i < selectedArray.length; i += chunkSize) {
          const chunk = selectedArray.slice(i, i + chunkSize)
          await onBatchRate(chunk, rating)
          setProgress(Math.round(((i + chunk.length) / selectedArray.length) * 100))
        }
        clearSelection()
      } finally {
        setIsProcessing(false)
        setProgress(0)
      }
    },
    [selectedArray, onBatchRate, clearSelection],
  )

  // Handle batch quality
  const handleBatchQuality = useCallback(
    async (score: number) => {
      setIsProcessing(true)
      setProgress(0)

      try {
        const chunkSize = 10
        for (let i = 0; i < selectedArray.length; i += chunkSize) {
          const chunk = selectedArray.slice(i, i + chunkSize)
          await onBatchQuality(chunk, score)
          setProgress(Math.round(((i + chunk.length) / selectedArray.length) * 100))
        }
        clearSelection()
      } finally {
        setIsProcessing(false)
        setProgress(0)
      }
    },
    [selectedArray, onBatchQuality, clearSelection],
  )

  const gridCols = {
    2: 'grid-cols-2',
    3: 'grid-cols-3',
    4: 'grid-cols-4',
  }

  return (
    <div className={cn('flex flex-col gap-4', className)}>
      {/* Batch actions toolbar */}
      <BatchActions
        selectedCount={selectedCount}
        totalCount={filteredEpisodes.length}
        isProcessing={isProcessing}
        progress={progress}
        onSelectAll={handleSelectAll}
        onClearSelection={clearSelection}
        onApplyRating={handleBatchRate}
        onApplyQuality={handleBatchQuality}
      />

      {/* Filter and display controls */}
      <div className="flex items-center justify-between">
        <div className="flex gap-2">
          <Button
            variant={statusFilter === 'all' ? 'default' : 'outline'}
            size="sm"
            onClick={() => setStatusFilter('all')}
          >
            All ({episodes.length})
          </Button>
          <Button
            variant={statusFilter === 'pending' ? 'default' : 'outline'}
            size="sm"
            onClick={() => setStatusFilter('pending')}
          >
            Pending ({episodes.filter((e) => e.annotationStatus === 'pending').length})
          </Button>
          <Button
            variant={statusFilter === 'complete' ? 'default' : 'outline'}
            size="sm"
            onClick={() => setStatusFilter('complete')}
          >
            Complete ({episodes.filter((e) => e.annotationStatus === 'complete').length})
          </Button>
        </div>

        {/* Column selector */}
        <div className="flex gap-1">
          <Button
            variant={columns === 2 ? 'default' : 'outline'}
            size="icon"
            className="h-8 w-8"
            onClick={() => setColumns(2)}
          >
            <Grid2X2 className="h-4 w-4" />
          </Button>
          <Button
            variant={columns === 3 ? 'default' : 'outline'}
            size="icon"
            className="h-8 w-8"
            onClick={() => setColumns(3)}
          >
            <Grid3X3 className="h-4 w-4" />
          </Button>
          <Button
            variant={columns === 4 ? 'default' : 'outline'}
            size="icon"
            className="h-8 w-8"
            onClick={() => setColumns(4)}
          >
            <LayoutGrid className="h-4 w-4" />
          </Button>
        </div>
      </div>

      {/* Episode grid */}
      <div className={cn('grid gap-4', gridCols[columns])}>
        {paginatedEpisodes.map((episode, pageIndex) => {
          const globalIndex = currentPage * PAGE_SIZE + pageIndex
          return (
            <EpisodePreviewCard
              key={globalIndex}
              episode={episode}
              index={globalIndex}
              isSelected={selectedIndices.has(globalIndex)}
              onToggleSelect={handleToggleSelect}
              onQuickRate={onQuickRate}
              onOpen={onOpenEpisode}
              thumbnailUrl={getThumbnailUrl?.(episode, globalIndex)}
            />
          )
        })}
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-4">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setCurrentPage((p) => Math.max(0, p - 1))}
            disabled={currentPage === 0}
          >
            <ChevronLeft className="mr-1 h-4 w-4" />
            Previous
          </Button>
          <span className="text-muted-foreground text-sm">
            Page {currentPage + 1} of {totalPages}
          </span>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setCurrentPage((p) => Math.min(totalPages - 1, p + 1))}
            disabled={currentPage === totalPages - 1}
          >
            Next
            <ChevronRight className="ml-1 h-4 w-4" />
          </Button>
        </div>
      )}
    </div>
  )
}
