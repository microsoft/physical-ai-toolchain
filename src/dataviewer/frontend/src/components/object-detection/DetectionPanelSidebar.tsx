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
    <div className="flex flex-col gap-4 min-h-0">
      <Card className="flex-1 flex flex-col min-h-0 overflow-auto">
        <CardHeader className="py-3 px-4">
          <CardTitle className="text-sm flex items-center gap-2">
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
        <Card className="flex-shrink-0 max-h-80 overflow-auto">
          <CardHeader className="py-3 px-4">
            <CardTitle className="text-sm flex items-center gap-2">
              <Eye className="h-4 w-4" />
              Frame {currentFrame} Detections
            </CardTitle>
          </CardHeader>
          <CardContent className="p-4 pt-0">
            <div className="space-y-2">
              {currentDetections.map((detection) => (
                <div
                  key={`${detection.class_name}-${detection.confidence}-${detection.bbox.join('-')}`}
                  className="flex items-center justify-between p-2 bg-muted rounded text-sm"
                >
                  <span className="font-medium">{detection.class_name}</span>
                  <span className="text-muted-foreground">{(detection.confidence * 100).toFixed(1)}%</span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {data && (
        <Card className="flex-shrink-0">
          <CardHeader className="py-3 px-4">
            <CardTitle className="text-sm">Summary</CardTitle>
          </CardHeader>
          <CardContent className="p-4 pt-0">
            <div className="grid grid-cols-2 gap-3 text-center">
              <div className="bg-muted p-3 rounded-lg">
                <div className="text-xl font-bold text-blue-500">{filteredData?.total_detections || 0}</div>
                <div className="text-xs text-muted-foreground">Total</div>
              </div>
              <div className="bg-muted p-3 rounded-lg">
                <div className="text-xl font-bold text-green-500">{availableClasses.length}</div>
                <div className="text-xs text-muted-foreground">Classes</div>
              </div>
              <div className="bg-muted p-3 rounded-lg">
                <div className="text-xl font-bold text-purple-500">{data.processed_frames}</div>
                <div className="text-xs text-muted-foreground">Frames</div>
              </div>
              <div className="bg-muted p-3 rounded-lg">
                <div className="text-xl font-bold text-orange-500">{(filters.minConfidence * 100).toFixed(0)}%</div>
                <div className="text-xs text-muted-foreground">Min Conf</div>
              </div>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
