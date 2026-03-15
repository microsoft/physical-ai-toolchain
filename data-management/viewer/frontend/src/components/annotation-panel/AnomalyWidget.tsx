/**
 * Anomaly annotation widget.
 *
 * Provides controls for managing anomaly annotations including
 * auto-detected anomalies and manual additions.
 */

import { CheckCircle, Plus, Zap } from 'lucide-react'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { useAnnotationStore, usePlaybackControls } from '@/stores'
import type { Anomaly } from '@/types'

import { AddAnomalyDialog } from './AddAnomalyDialog'
import { AnomalyList } from './AnomalyList'

/**
 * Widget for managing anomaly annotations.
 *
 * @example
 * ```tsx
 * <AnomalyWidget />
 * ```
 */
export function AnomalyWidget() {
  const currentAnnotation = useAnnotationStore((state) => state.currentAnnotation)
  const addAnomaly = useAnnotationStore((state) => state.addAnomaly)
  const removeAnomaly = useAnnotationStore((state) => state.removeAnomaly)
  const updateAnomaly = useAnnotationStore((state) => state.updateAnomaly)
  const { currentFrame, setCurrentFrame } = usePlaybackControls()

  const [dialogOpen, setDialogOpen] = useState(false)

  const anomalies = currentAnnotation?.anomalies.anomalies ?? []

  const handleAddAnomaly = (anomalyData: Omit<Anomaly, 'id'>) => {
    addAnomaly({
      ...anomalyData,
      id: `anomaly-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`,
    })
  }

  const handleToggleVerified = (id: string) => {
    const anomaly = anomalies.find((a) => a.id === id)
    if (anomaly) {
      updateAnomaly(id, { verified: !anomaly.verified })
    }
  }

  // Count statistics
  const autoDetectedCount = anomalies.filter((a) => a.autoDetected).length
  const verifiedCount = anomalies.filter((a) => a.verified).length
  const unverifiedCount = anomalies.filter((a) => a.autoDetected && !a.verified).length

  if (!currentAnnotation) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Anomalies</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">No episode selected</p>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center justify-between text-sm">
          Anomalies
          {anomalies.length > 0 && (
            <span className="text-xs font-normal text-muted-foreground">
              {anomalies.length} total
            </span>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Statistics */}
        {anomalies.length > 0 && (
          <div className="flex gap-4 text-xs text-muted-foreground">
            {autoDetectedCount > 0 && (
              <span className="flex items-center gap-1">
                <Zap className="h-3 w-3 text-blue-500" />
                {autoDetectedCount} auto-detected
              </span>
            )}
            {verifiedCount > 0 && (
              <span className="flex items-center gap-1">
                <CheckCircle className="h-3 w-3 text-green-500" />
                {verifiedCount} verified
              </span>
            )}
            {unverifiedCount > 0 && (
              <span className="text-orange-500">{unverifiedCount} pending review</span>
            )}
          </div>
        )}

        {/* Anomaly list */}
        <AnomalyList
          anomalies={anomalies}
          onRemove={removeAnomaly}
          onToggleVerified={handleToggleVerified}
          onSeek={setCurrentFrame}
        />

        {/* Add button */}
        <Button variant="outline" size="sm" onClick={() => setDialogOpen(true)} className="w-full">
          <Plus className="mr-2 h-4 w-4" />
          Add Anomaly at Frame {currentFrame}
        </Button>
      </CardContent>

      <AddAnomalyDialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        onAdd={handleAddAnomaly}
        currentFrame={currentFrame}
      />
    </Card>
  )
}
