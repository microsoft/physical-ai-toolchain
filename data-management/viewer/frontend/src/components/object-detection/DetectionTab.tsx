/**
 * Detection tab wrapper component for AnnotationWorkspace.
 */

import { AlertTriangle, BarChart3, Eye, Filter, Loader2, Scan } from 'lucide-react'
import { useMemo } from 'react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useObjectDetection } from '@/hooks/use-object-detection'
import { useDatasetStore, useEpisodeStore, usePlaybackControls } from '@/stores'

import { DetectionCharts } from './DetectionCharts'
import { DetectionFilters } from './DetectionFilters'
import { DetectionTimeline } from './DetectionTimeline'
import { DetectionViewer } from './DetectionViewer'

export function DetectionTab() {
  const currentDataset = useDatasetStore((state) => state.currentDataset)
  const currentEpisode = useEpisodeStore((state) => state.currentEpisode)
  const { currentFrame, setCurrentFrame } = usePlaybackControls()

  const {
    data,
    filteredData,
    isLoading,
    isRunning,
    error,
    needsRerun,
    filters,
    setFilters,
    runDetection,
    availableClasses,
  } = useObjectDetection()

  // Get current frame detections
  const currentDetections = useMemo(() => {
    if (!filteredData) return []
    const frameResult = filteredData.detections_by_frame.find((r) => r.frame === currentFrame)
    return frameResult?.detections || []
  }, [filteredData, currentFrame])

  // Build image URL for detection overlay
  const imageUrl = useMemo(() => {
    if (!currentDataset || !currentEpisode) return null
    return `/api/datasets/${currentDataset.id}/episodes/${currentEpisode.meta.index}/frames/${currentFrame}?camera=il-camera`
  }, [currentDataset, currentEpisode, currentFrame])

  const totalFrames = currentEpisode?.meta.length || 100

  if (!currentDataset || !currentEpisode) {
    return null
  }

  return (
    <Card>
      <CardHeader className="px-4 py-3">
        <div className="flex items-center justify-between">
          <CardTitle className="flex items-center gap-2 text-sm">
            <Scan className="h-4 w-4" />
            Object Detection (YOLO11)
          </CardTitle>
          <div className="flex items-center gap-2">
            {needsRerun && data && (
              <span className="flex items-center gap-1 text-xs text-orange-500">
                <AlertTriangle className="h-3 w-3" />
                Edits detected
              </span>
            )}
            <Button
              size="sm"
              onClick={() => runDetection({ confidence: filters.minConfidence })}
              disabled={isRunning || isLoading}
            >
              {isRunning ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Detecting...
                </>
              ) : (
                <>
                  <Scan className="mr-2 h-4 w-4" />
                  {needsRerun ? 'Re-run Detection' : 'Run Detection'}
                </>
              )}
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent className="p-4 pt-0">
        {error && (
          <div className="bg-destructive/10 text-destructive mb-4 rounded-sm p-3 text-sm">
            <strong>Error:</strong> {error instanceof Error ? error.message : 'Detection failed'}
          </div>
        )}

        {!data && !isRunning && (
          <div className="text-muted-foreground py-8 text-center">
            <Scan className="mx-auto mb-3 h-12 w-12 opacity-50" />
            <p className="mb-2">No detection results yet</p>
            <p className="text-xs">Click "Run Detection" to analyze all frames with YOLO11</p>
          </div>
        )}

        {isRunning && !data && (
          <div className="text-muted-foreground py-8 text-center">
            <Loader2 className="mx-auto mb-3 h-12 w-12 animate-spin" />
            <p className="mb-2">Processing frames...</p>
            <p className="text-xs">This may take a moment for episodes with many frames</p>
          </div>
        )}

        {data && (
          <Tabs defaultValue="viewer" className="space-y-4">
            <TabsList className="grid w-full grid-cols-4">
              <TabsTrigger value="viewer" className="gap-1 text-xs">
                <Eye className="h-3 w-3" />
                Viewer
              </TabsTrigger>
              <TabsTrigger value="timeline" className="text-xs">
                Timeline
              </TabsTrigger>
              <TabsTrigger value="filters" className="gap-1 text-xs">
                <Filter className="h-3 w-3" />
                Filter
              </TabsTrigger>
              <TabsTrigger value="charts" className="gap-1 text-xs">
                <BarChart3 className="h-3 w-3" />
                Charts
              </TabsTrigger>
            </TabsList>

            <TabsContent value="viewer" className="space-y-4">
              <div className="aspect-video">
                <DetectionViewer imageUrl={imageUrl} detections={currentDetections} />
              </div>
              <div className="text-muted-foreground text-center text-sm">
                Frame {currentFrame} - {currentDetections.length} detection
                {currentDetections.length !== 1 ? 's' : ''}
              </div>
              <DetectionTimeline
                detectionsPerFrame={filteredData?.detections_by_frame || []}
                totalFrames={totalFrames}
                currentFrame={currentFrame}
                onFrameClick={setCurrentFrame}
              />
            </TabsContent>

            <TabsContent value="timeline" className="space-y-4">
              <div className="mb-2 text-sm">
                Click on the timeline to jump to a specific frame, or click the frame buttons below.
              </div>
              <DetectionTimeline
                detectionsPerFrame={filteredData?.detections_by_frame || []}
                totalFrames={totalFrames}
                currentFrame={currentFrame}
                onFrameClick={setCurrentFrame}
              />
            </TabsContent>

            <TabsContent value="filters">
              <DetectionFilters
                filters={filters}
                availableClasses={availableClasses}
                onFiltersChange={setFilters}
              />
            </TabsContent>

            <TabsContent value="charts">
              {filteredData && <DetectionCharts summary={filteredData} />}
            </TabsContent>
          </Tabs>
        )}
      </CardContent>
    </Card>
  )
}
