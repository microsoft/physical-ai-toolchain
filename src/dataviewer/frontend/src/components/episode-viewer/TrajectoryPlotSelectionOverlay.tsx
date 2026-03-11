import { Button } from '@/components/ui/button'

interface TrajectoryPlotSelectionOverlayProps {
  selectedRange: [number, number] | null
  selectionHighlight: { left: string; width: string } | null
  contextMenuPosition: { x: number; y: number } | null
  onCreateSubtaskFromRange?: (range: [number, number]) => void
  onDismissContextMenu: () => void
}

export function TrajectoryPlotSelectionOverlay({
  selectedRange,
  selectionHighlight,
  contextMenuPosition,
  onCreateSubtaskFromRange,
  onDismissContextMenu,
}: TrajectoryPlotSelectionOverlayProps) {
  return (
    <div
      data-keep-playback-selection="true"
      className="pointer-events-none absolute inset-0 z-10"
    >
      {selectionHighlight && (
        <div
          className="absolute bottom-2 top-2 rounded-md border border-primary/60 bg-primary/10"
          style={selectionHighlight}
        />
      )}
      {contextMenuPosition && selectedRange && (
        <div
          data-keep-playback-selection="true"
          className="pointer-events-auto absolute z-20 rounded-md border bg-popover p-1 shadow-md"
          style={{ left: contextMenuPosition.x, top: contextMenuPosition.y }}
          onContextMenu={(event) => event.preventDefault()}
          onPointerDown={(event) => event.stopPropagation()}
          onPointerUp={(event) => event.stopPropagation()}
        >
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={(event) => {
              event.stopPropagation()
              onCreateSubtaskFromRange?.(selectedRange)
              onDismissContextMenu()
            }}
          >
            Create Subtask
          </Button>
        </div>
      )}
    </div>
  )
}
