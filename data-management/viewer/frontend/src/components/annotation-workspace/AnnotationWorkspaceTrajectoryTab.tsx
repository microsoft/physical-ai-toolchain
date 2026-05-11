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
  languageInstructionPanel: ReactNode
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
  languageInstructionPanel,
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
        className="grid h-full min-h-0 grid-cols-1 gap-2 lg:grid-cols-3"
      >
        <div
          data-testid="trajectory-playback-group-panel"
          className="bg-card order-1 overflow-y-auto rounded-xl border p-3 shadow-xs lg:col-span-2"
        >
          <div className="space-y-2">
            {playbackCard}
            <Card className="overflow-hidden">
              <CardContent data-testid="trajectory-graph-panel" className="flex flex-col gap-2 p-3">
                {(selectedRange || selectedSubtaskId) && (
                  <div className="flex items-center justify-end">
                    <Button size="sm" variant="outline" onClick={onClearPlaybackSelection}>
                      Clear Selection
                    </Button>
                  </div>
                )}
                <TrajectoryPlot
                  className="h-[180px]"
                  selectedRange={selectedRange}
                  onSelectedRangeChange={onDraftRangeChange}
                  onCreateSubtaskFromRange={onCreateSubtaskFromRange}
                  onSeekFrame={onGraphSeek}
                  onSelectionStart={onSelectionStart}
                  onSelectionComplete={onSelectionComplete}
                />
                <div className="bg-muted/20 rounded-lg border p-2">
                  <div className="mb-1 flex items-center justify-between gap-2">
                    <h4 className="text-xs font-medium">Subtask Timeline</h4>
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
            {subtaskListCard}
          </div>
        </div>
        <Card
          data-testid="trajectory-labels-panel"
          className="order-2 min-h-[280px] overflow-hidden lg:min-h-0"
        >
          <CardContent className="h-full overflow-y-auto p-4">
            <div className="space-y-6">
              {labelPanel}
              <div className="border-t pt-6">{languageInstructionPanel}</div>
              <div className="border-t pt-6">{editToolsPanel}</div>
            </div>
          </CardContent>
        </Card>
      </div>
    </TabsContent>
  )
}
