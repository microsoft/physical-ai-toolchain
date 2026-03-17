import { SubtaskList } from '@/components/subtask-timeline'
import { Card, CardContent } from '@/components/ui/card'

interface AnnotationWorkspaceSubtaskListCardProps {
  compact?: boolean
  selectedSubtaskId: string | null
  onSelectionChange: (id: string | null) => void
  draftRange: [number, number] | null
  maxFrame: number
  onDraftRangeChange: (range: [number, number] | null) => void
  onCreateSubtaskFromRange: (range: [number, number]) => void
}

export function AnnotationWorkspaceSubtaskListCard({
  compact = false,
  selectedSubtaskId,
  onSelectionChange,
  draftRange,
  maxFrame,
  onDraftRangeChange,
  onCreateSubtaskFromRange,
}: AnnotationWorkspaceSubtaskListCardProps) {
  return (
    <Card className={compact ? 'min-h-[220px]' : 'mt-4'}>
      <CardContent className={compact ? 'p-3' : 'p-4'}>
        <SubtaskList
          selectedSubtaskId={selectedSubtaskId}
          onSelectionChange={onSelectionChange}
          draftRange={draftRange}
          maxFrame={maxFrame}
          onDraftRangeChange={onDraftRangeChange}
          onCreateSubtaskFromRange={onCreateSubtaskFromRange}
        />
      </CardContent>
    </Card>
  )
}
