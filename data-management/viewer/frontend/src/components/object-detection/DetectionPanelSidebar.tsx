import { Eye, Filter } from 'lucide-react'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import type { Detection, DetectionFilters, EpisodeDetectionSummary } from '@/types/detection'

import { DetectionFilters as DetectionFiltersPanel } from './DetectionFilters'

interface DetectionPanelSidebarProps {
  availableClasses: string[]
  currentDetections: Detection[]
  currentFrame: number
  data: EpisodeDetectionSummary | null | undefined
  filteredData: EpisodeDetectionSummary | null
  filters: DetectionFilters
  onFiltersChange: (filters: DetectionFilters) => void
}

export function DetectionPanelSidebar({
  availableClasses,
  currentDetections,
  currentFrame,
  data,
  filteredData,
  filters,
  onFiltersChange,
}: DetectionPanelSidebarProps) {
  return (
    <div className="flex min-h-0 flex-col gap-4">
      <Card className="flex min-h-0 flex-1 flex-col overflow-auto">
        <CardHeader className="px-4 py-3">
          <CardTitle className="flex items-center gap-2 text-sm">
            <Filter className="h-4 w-4" />
            Detection Filters
          </CardTitle>
        </CardHeader>
        <CardContent className="p-4 pt-0">
          <DetectionFiltersPanel
            filters={filters}
            availableClasses={availableClasses}
            onFiltersChange={onFiltersChange}
          />
        </CardContent>
      </Card>

      {data && currentDetections.length > 0 && (
        <Card className="max-h-80 shrink-0 overflow-auto">
          <CardHeader className="px-4 py-3">
            <CardTitle className="flex items-center gap-2 text-sm">
              <Eye className="h-4 w-4" />
              Frame {currentFrame} Detections
            </CardTitle>
          </CardHeader>
          <CardContent className="p-4 pt-0">
            <div className="space-y-2">
              {currentDetections.map((detection) => (
                <div
                  key={`${detection.class_name}-${detection.confidence}-${detection.bbox.join('-')}`}
                  className="bg-muted flex items-center justify-between rounded-sm p-2 text-sm"
                >
                  <span className="font-medium">{detection.class_name}</span>
                  <span className="text-muted-foreground">
                    {(detection.confidence * 100).toFixed(1)}%
                  </span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {data && (
        <Card className="shrink-0">
          <CardHeader className="px-4 py-3">
            <CardTitle className="text-sm">Summary</CardTitle>
          </CardHeader>
          <CardContent className="p-4 pt-0">
            <div className="grid grid-cols-2 gap-3 text-center">
              <div className="bg-muted rounded-lg p-3">
                <div className="text-xl font-bold text-blue-500">
                  {filteredData?.total_detections || 0}
                </div>
                <div className="text-muted-foreground text-xs">Total</div>
              </div>
              <div className="bg-muted rounded-lg p-3">
                <div className="text-xl font-bold text-green-500">{availableClasses.length}</div>
                <div className="text-muted-foreground text-xs">Classes</div>
              </div>
              <div className="bg-muted rounded-lg p-3">
                <div className="text-xl font-bold text-purple-500">{data.processed_frames}</div>
                <div className="text-muted-foreground text-xs">Frames</div>
              </div>
              <div className="bg-muted rounded-lg p-3">
                <div className="text-xl font-bold text-orange-500">
                  {(filters.minConfidence * 100).toFixed(0)}%
                </div>
                <div className="text-muted-foreground text-xs">Min Conf</div>
              </div>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
