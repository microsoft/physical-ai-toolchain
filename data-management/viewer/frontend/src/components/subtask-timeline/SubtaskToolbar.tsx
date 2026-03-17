/**
 * Subtask toolbar for adding, editing, and deleting segments.
 */

import { Plus, Trash2 } from 'lucide-react'
import { useCallback } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { cn } from '@/lib/utils'
import { useEpisodeStore, usePlaybackControls, useSubtaskState } from '@/stores'
import type { SubtaskSegment } from '@/types/episode-edit'
import { generateSubtaskId, getNextSubtaskColor, SUBTASK_COLORS } from '@/types/episode-edit'

interface SubtaskToolbarProps {
  /** Currently selected segment ID */
  selectedSegmentId?: string | null
  /** Callback when selection changes */
  onSelectionChange?: (id: string | null) => void
  /** Additional CSS classes */
  className?: string
}

/**
 * Toolbar for managing subtask segments.
 */
export function SubtaskToolbar({
  selectedSegmentId,
  onSelectionChange,
  className,
}: SubtaskToolbarProps) {
  const { subtasks, addSubtask, updateSubtask, removeSubtask } = useSubtaskState()
  const { currentFrame } = usePlaybackControls()
  const currentEpisode = useEpisodeStore((state) => state.currentEpisode)

  const totalFrames = currentEpisode?.meta.length ?? 100
  const selectedSegment = subtasks.find((s) => s.id === selectedSegmentId)

  // Add a new segment at current frame position
  const handleAddSegment = useCallback(() => {
    const defaultEnd = Math.min(currentFrame + 100, totalFrames - 1)
    const newSegment: SubtaskSegment = {
      id: generateSubtaskId(),
      label: `Subtask ${subtasks.length + 1}`,
      frameRange: [currentFrame, defaultEnd],
      color: getNextSubtaskColor(subtasks),
      source: 'manual',
    }
    addSubtask(newSegment)
    onSelectionChange?.(newSegment.id)
  }, [currentFrame, totalFrames, subtasks, addSubtask, onSelectionChange])

  // Delete selected segment
  const handleDeleteSelected = useCallback(() => {
    if (selectedSegmentId) {
      removeSubtask(selectedSegmentId)
      onSelectionChange?.(null)
    }
  }, [selectedSegmentId, removeSubtask, onSelectionChange])

  // Update segment label
  const handleLabelChange = useCallback(
    (label: string) => {
      if (selectedSegmentId) {
        updateSubtask(selectedSegmentId, { label })
      }
    },
    [selectedSegmentId, updateSubtask],
  )

  // Update segment color
  const handleColorChange = useCallback(
    (color: string) => {
      if (selectedSegmentId) {
        updateSubtask(selectedSegmentId, { color })
      }
    },
    [selectedSegmentId, updateSubtask],
  )

  if (!currentEpisode) {
    return null
  }

  return (
    <div className={cn('flex items-center gap-2', className)}>
      {/* Add segment at current frame */}
      <Button
        size="sm"
        variant="outline"
        onClick={handleAddSegment}
        title="Add subtask at current frame"
      >
        <Plus className="mr-1 h-4 w-4" />
        Add
      </Button>

      {/* Segment controls (when selected) */}
      {selectedSegment && (
        <>
          <div className="h-4 w-px bg-border" />

          {/* Edit label */}
          <Input
            value={selectedSegment.label}
            onChange={(e) => handleLabelChange(e.target.value)}
            className="h-8 w-32"
            placeholder="Label"
          />

          {/* Color picker */}
          <Popover>
            <PopoverTrigger asChild>
              <Button size="sm" variant="outline" className="w-8 p-0">
                <div
                  className="h-4 w-4 rounded"
                  style={{ backgroundColor: selectedSegment.color }}
                />
              </Button>
            </PopoverTrigger>
            <PopoverContent className="w-auto p-2">
              <div className="grid grid-cols-4 gap-1">
                {SUBTASK_COLORS.map((color) => (
                  <button
                    key={color}
                    className={cn(
                      'h-6 w-6 rounded transition-transform hover:scale-110',
                      selectedSegment.color === color && 'ring-2 ring-ring ring-offset-2',
                    )}
                    style={{ backgroundColor: color }}
                    onClick={() => handleColorChange(color)}
                  />
                ))}
              </div>
            </PopoverContent>
          </Popover>

          {/* Delete */}
          <Button
            size="sm"
            variant="ghost"
            className="text-destructive hover:text-destructive"
            onClick={handleDeleteSelected}
          >
            <Trash2 className="h-4 w-4" />
          </Button>

          {/* Segment info */}
          <span className="text-xs text-muted-foreground">
            {selectedSegment.frameRange[0]} - {selectedSegment.frameRange[1]}
          </span>
        </>
      )}
    </div>
  )
}
