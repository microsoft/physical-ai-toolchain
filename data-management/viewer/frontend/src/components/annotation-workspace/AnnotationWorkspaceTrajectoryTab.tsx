import type { ReactNode } from 'react'

import { TrajectoryPlot } from '@/components/episode-viewer'
import { SubtaskTimelineTrack, SubtaskToolbar } from '@/components/subtask-timeline'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { TabsContent } from '@/components/ui/tabs'

interface AnnotationWorkspaceTrajectoryTabProps {
  playbackCard: ReactNode
  subtaskListCard: ReactNode
  labelPanel: ReactNode
  editToolsPanel: ReactNode
  selectedRange: [number, number] | null
  selectedSubtaskId: string | null
  onClearPlaybackSelection: () => void
  onDraftRangeChange: (range: [number, number] | null) => void
  onCreateSubtaskFromRange: (range: [number, number]) => void
  onGraphSeek: (frame: number) => void
  onSelectionStart: () => void
  onSelectionComplete: (range: [number, number]) => void
  totalFrames: number
  onSubtaskSelectionChange: (id: string | null) => void
}

export function AnnotationWorkspaceTrajectoryTab({
  playbackCard,
  subtaskListCard,
  labelPanel,
  editToolsPanel,
  selectedRange,
  selectedSubtaskId,
  onClearPlaybackSelection,
  onDraftRangeChange,
  onCreateSubtaskFromRange,
  onGraphSeek,
  onSelectionStart,
  onSelectionComplete,
  totalFrames,
  onSubtaskSelectionChange,
}: AnnotationWorkspaceTrajectoryTabProps) {
  return (
    <TabsContent value="trajectory" className="mt-2.5 min-h-0 flex-1">
      <div
        data-testid="trajectory-layout-grid"
        className="grid h-full min-h-0 grid-cols-1 gap-4 lg:grid-cols-3"
      >
        <div
          data-testid="trajectory-playback-group-panel"
          className="order-1 min-h-[320px] overflow-y-auto rounded-xl border bg-card p-3 shadow-sm lg:col-span-2"
        >
          <div className="space-y-3">
            {playbackCard}
            {subtaskListCard}
          </div>
        </div>
        <Card
          data-testid="trajectory-labels-panel"
          className="order-2 min-h-[280px] overflow-hidden lg:row-span-2 lg:min-h-0"
        >
          <CardContent className="h-full overflow-y-auto p-4">
            <div className="space-y-6">
              {labelPanel}
              <div className="border-t pt-6">{editToolsPanel}</div>
            </div>
          </CardContent>
        </Card>
        <Card className="order-3 min-h-[360px] overflow-hidden lg:col-span-2">
          <CardContent
            data-testid="trajectory-graph-panel"
            className="flex h-full min-h-0 flex-col gap-2 overflow-y-auto p-3"
          >
            {(selectedRange || selectedSubtaskId) && (
              <div className="flex items-center justify-end">
                <Button size="sm" variant="outline" onClick={onClearPlaybackSelection}>
                  Clear Selection
                </Button>
              </div>
            )}
            <TrajectoryPlot
              className="min-h-[280px] flex-1"
              selectedRange={selectedRange}
              onSelectedRangeChange={onDraftRangeChange}
              onCreateSubtaskFromRange={onCreateSubtaskFromRange}
              onSeekFrame={onGraphSeek}
              onSelectionStart={onSelectionStart}
              onSelectionComplete={onSelectionComplete}
            />
            <div className="rounded-lg border bg-muted/20 p-3">
              <div className="mb-2 flex items-center justify-between gap-3">
                <div>
                  <h4 className="text-sm font-medium">Subtask Timeline</h4>
                  <p className="text-xs text-muted-foreground">
                    Compare subtask ranges directly against trajectory changes on the same frame
                    timeline.
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <SubtaskToolbar
                  selectedSegmentId={selectedSubtaskId}
                  onSelectionChange={onSubtaskSelectionChange}
                />
              </div>
              <SubtaskTimelineTrack
                totalFrames={totalFrames}
                editable
                selectedSegmentId={selectedSubtaskId}
                draftRange={selectedRange}
                onSegmentClick={(segment) => onSubtaskSelectionChange(segment.id)}
              />
            </div>
          </CardContent>
        </Card>
      </div>
    </TabsContent>
  )
}
