import { ArrowDown, ArrowUp, ListChecks, Trash2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import { usePlaybackControls, useSubtaskState } from '@/stores'

interface SubtaskListProps {
  selectedSubtaskId?: string | null
  onSelectionChange?: (id: string | null) => void
  className?: string
  draftRange?: [number, number] | null
  maxFrame?: number
  onDraftRangeChange?: (range: [number, number] | null) => void
  onCreateSubtaskFromRange?: (range: [number, number]) => void
}

export function SubtaskList({
  selectedSubtaskId,
  onSelectionChange,
  className,
  draftRange = null,
  maxFrame = 0,
  onDraftRangeChange,
  onCreateSubtaskFromRange,
}: SubtaskListProps) {
  const { subtasks, updateSubtask, removeSubtask, reorderSubtasks } = useSubtaskState()
  const { setCurrentFrame } = usePlaybackControls()

  const updateDraftRange = (nextStart: number, nextEnd: number) => {
    if (!Number.isFinite(nextStart) || !Number.isFinite(nextEnd)) {
      return
    }

    const boundedStart = Math.max(0, Math.min(nextStart, maxFrame))
    const boundedEnd = Math.max(0, Math.min(nextEnd, maxFrame))

    onDraftRangeChange?.([Math.min(boundedStart, boundedEnd), Math.max(boundedStart, boundedEnd)])
  }

  const nudgeDraftBoundary = (boundary: 'start' | 'end', delta: number) => {
    if (!draftRange) {
      return
    }

    if (boundary === 'start') {
      updateDraftRange(draftRange[0] + delta, draftRange[1])
      return
    }

    updateDraftRange(draftRange[0], draftRange[1] + delta)
  }

  if (subtasks.length === 0 && !draftRange) {
    return (
      <div
        className={cn(
          'text-muted-foreground rounded-lg border border-dashed p-4 text-sm',
          className,
        )}
        data-keep-playback-selection="true"
      >
        <div className="text-foreground flex items-center gap-2 font-medium">
          <ListChecks className="h-4 w-4" />
          Subtasks
        </div>
        <p className="mt-2 text-xs">
          Drag on the trajectory graph to select a frame range, then right click to create a
          subtask.
        </p>
      </div>
    )
  }

  return (
    <div className={cn('rounded-lg border p-3', className)} data-keep-playback-selection="true">
      <div className="mb-3 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <ListChecks className="h-4 w-4" />
          <h4 className="text-sm font-medium">Subtasks</h4>
        </div>
      </div>
      {draftRange && (
        <div className="border-primary/40 bg-primary/5 mb-3 rounded-md border p-3">
          <div className="flex items-center justify-between gap-2">
            <div>
              <div className="text-primary text-sm font-medium">Draft Selection</div>
              <p className="text-muted-foreground text-xs">
                Frames {draftRange[0]} to {draftRange[1]}
              </p>
            </div>
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={() => onCreateSubtaskFromRange?.(draftRange)}
              >
                Create Subtask
              </Button>
            </div>
          </div>
          <div className="mt-3 grid gap-2 md:grid-cols-2">
            <div className="bg-background rounded-sm border p-2">
              <div className="text-muted-foreground mb-1 text-xs font-medium">Start</div>
              <div className="flex items-center gap-2">
                <Button size="sm" variant="outline" onClick={() => nudgeDraftBoundary('start', -1)}>
                  -1
                </Button>
                <Input
                  type="number"
                  min={0}
                  max={maxFrame}
                  value={draftRange[0]}
                  onChange={(event) => updateDraftRange(Number(event.target.value), draftRange[1])}
                  aria-label="Draft selection start frame"
                />
                <Button size="sm" variant="outline" onClick={() => nudgeDraftBoundary('start', 1)}>
                  +1
                </Button>
              </div>
            </div>
            <div className="bg-background rounded-sm border p-2">
              <div className="text-muted-foreground mb-1 text-xs font-medium">End</div>
              <div className="flex items-center gap-2">
                <Button size="sm" variant="outline" onClick={() => nudgeDraftBoundary('end', -1)}>
                  -1
                </Button>
                <Input
                  type="number"
                  min={0}
                  max={maxFrame}
                  value={draftRange[1]}
                  onChange={(event) => updateDraftRange(draftRange[0], Number(event.target.value))}
                  aria-label="Draft selection end frame"
                />
                <Button size="sm" variant="outline" onClick={() => nudgeDraftBoundary('end', 1)}>
                  +1
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}
      <div className="flex flex-col gap-2">
        {subtasks.map((segment, index) => {
          const isSelected = segment.id === selectedSubtaskId

          return (
            <div
              key={segment.id}
              className={cn(
                'rounded-md border p-2 transition-colors',
                isSelected ? 'border-primary bg-primary/5' : 'border-border bg-background',
              )}
            >
              <div className="flex items-start justify-between gap-2">
                <button
                  type="button"
                  className="flex min-w-0 flex-1 items-start gap-2 text-left"
                  onClick={() => {
                    setCurrentFrame(segment.frameRange[0])
                    onSelectionChange?.(segment.id)
                  }}
                >
                  <span
                    className="mt-1 h-3 w-3 shrink-0 rounded-xs"
                    style={{ backgroundColor: segment.color }}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-sm font-medium">{segment.label}</span>
                      {isSelected && (
                        <span className="bg-primary/10 text-primary rounded-sm px-1.5 py-0.5 text-[10px] font-medium">
                          Active
                        </span>
                      )}
                    </div>
                    <p className="text-muted-foreground text-xs">
                      Frames {segment.frameRange[0]} to {segment.frameRange[1]}
                    </p>
                  </div>
                </button>
                <div className="flex shrink-0 items-center gap-1">
                  <Button
                    size="icon"
                    variant="ghost"
                    className="h-7 w-7"
                    disabled={index === 0}
                    onClick={() => reorderSubtasks(index, index - 1)}
                    aria-label={`Move ${segment.label} up`}
                  >
                    <ArrowUp className="h-3.5 w-3.5" />
                  </Button>
                  <Button
                    size="icon"
                    variant="ghost"
                    className="h-7 w-7"
                    disabled={index === subtasks.length - 1}
                    onClick={() => reorderSubtasks(index, index + 1)}
                    aria-label={`Move ${segment.label} down`}
                  >
                    <ArrowDown className="h-3.5 w-3.5" />
                  </Button>
                  <Button
                    size="icon"
                    variant="ghost"
                    className="text-destructive hover:text-destructive h-7 w-7"
                    onClick={() => {
                      removeSubtask(segment.id)
                      if (isSelected) {
                        onSelectionChange?.(null)
                      }
                    }}
                    aria-label={`Delete ${segment.label}`}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
              </div>
              <div className="mt-2 flex items-center gap-2">
                <Input
                  value={segment.label}
                  onChange={(event) => updateSubtask(segment.id, { label: event.target.value })}
                  className="h-8"
                  aria-label={`${segment.label} label`}
                />
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
