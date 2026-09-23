/**
 * Data quality annotation widget.
 *
 * Provides controls for rating overall data quality and
 * managing a list of specific issues.
 */

import { Plus } from 'lucide-react'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import { useAnnotationStore, usePlaybackControls } from '@/stores'
import type { DataQualityIssue, DataQualityLevel } from '@/types'

import { AddIssueDialog } from './AddIssueDialog'
import { IssueList } from './IssueList'

interface DataQualityWidgetProps {
  embedded?: boolean
}

/**
 * Widget for annotating data quality with overall rating and issue list.
 *
 * @example
 * ```tsx
 * <DataQualityWidget />
 * ```
 */
export function DataQualityWidget({ embedded = false }: DataQualityWidgetProps) {
  const currentAnnotation = useAnnotationStore((state) => state.currentAnnotation)
  const updateDataQuality = useAnnotationStore((state) => state.updateDataQuality)
  const { currentFrame, setCurrentFrame } = usePlaybackControls()

  const [dialogOpen, setDialogOpen] = useState(false)

  const dataQuality = currentAnnotation?.dataQuality

  const ratingOptions: { value: DataQualityLevel; label: string; color: string }[] = [
    { value: 'good', label: 'Good', color: 'bg-green-100 text-green-700' },
    { value: 'acceptable', label: 'Acceptable', color: 'bg-yellow-100 text-yellow-700' },
    { value: 'poor', label: 'Poor', color: 'bg-orange-100 text-orange-700' },
    { value: 'unusable', label: 'Unusable', color: 'bg-red-100 text-red-700' },
  ]

  const handleAddIssue = (issue: DataQualityIssue) => {
    const currentIssues = dataQuality?.issues ?? []
    updateDataQuality({
      issues: [...currentIssues, issue],
    })
  }

  const handleRemoveIssue = (index: number) => {
    const currentIssues = dataQuality?.issues ?? []
    updateDataQuality({
      issues: currentIssues.filter((_, i) => i !== index),
    })
  }

  if (!currentAnnotation) {
    if (embedded) {
      return <p className="text-muted-foreground text-sm">No episode selected</p>
    }

    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Data Quality</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-muted-foreground text-sm">No episode selected</p>
        </CardContent>
      </Card>
    )
  }

  const issueCount = dataQuality?.issues?.length ?? 0

  const content = (
    <>
      <div className="flex items-center justify-between pb-3 text-sm font-medium">
        <span>Data Quality</span>
        {dataQuality?.overallQuality && (
          <span
            className={cn(
              'rounded-sm px-2 py-0.5 text-xs font-medium',
              ratingOptions.find((r) => r.value === dataQuality.overallQuality)?.color,
            )}
          >
            {dataQuality.overallQuality}
          </span>
        )}
      </div>
      <div className="space-y-4">
        <div className="space-y-2">
          <span id="quality-rating-label" className="text-sm font-medium">
            Overall Quality
          </span>
          <div
            className="grid grid-cols-2 gap-2 xl:grid-cols-4"
            role="group"
            aria-labelledby="quality-rating-label"
          >
            {ratingOptions.map((option) => (
              <Button
                key={option.value}
                variant={dataQuality?.overallQuality === option.value ? 'default' : 'outline'}
                size="sm"
                onClick={() => updateDataQuality({ overallQuality: option.value })}
              >
                {option.label}
              </Button>
            ))}
          </div>
        </div>

        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium">
              Issues {issueCount > 0 && `(${issueCount})`}
            </span>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setDialogOpen(true)}
              className="h-7 text-xs"
            >
              <Plus className="mr-1 h-3 w-3" />
              Add Issue
            </Button>
          </div>

          <IssueList
            issues={dataQuality?.issues ?? []}
            onRemove={handleRemoveIssue}
            onSeek={setCurrentFrame}
          />
        </div>
      </div>

      <AddIssueDialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        onAdd={handleAddIssue}
        currentFrame={currentFrame}
      />
    </>
  )

  if (embedded) {
    return <div>{content}</div>
  }

  return (
    <Card>
      <CardContent className="pt-6">{content}</CardContent>
    </Card>
  )
}
