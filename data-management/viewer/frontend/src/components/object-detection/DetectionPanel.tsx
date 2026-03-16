import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

import { DetectionPanelResultsColumn } from './DetectionPanelResultsColumn'
import { DetectionPanelSidebar } from './DetectionPanelSidebar'
import { useDetectionPanelState } from './useDetectionPanelState'

export function DetectionPanel() {
  const {
    availableClasses,
    currentDataset,
    currentDetections,
    currentEpisode,
    currentFrame,
    data,
    error,
    filteredData,
    filters,
    imageUrl,
    isLoading,
    isPlaying,
    isRunning,
    needsRerun,
    playbackSpeed,
    progress,
    runDetection,
    setCurrentFrame,
    setFilters,
    setPlaybackSpeed,
    togglePlayback,
    totalFrames,
  } = useDetectionPanelState()

  if (!currentDataset || !currentEpisode) {
    return (
      <div className="flex h-full items-center justify-center">
        <Card className="max-w-md">
          <CardHeader>
            <CardTitle>No Episode Selected</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-muted-foreground">
              Select a dataset and episode from the sidebar to run object detection.
            </p>
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 overflow-auto lg:grid-cols-3">
      <DetectionPanelResultsColumn
        currentDetections={currentDetections}
        currentFrame={currentFrame}
        data={data}
        error={error instanceof Error ? error : null}
        filteredData={filteredData}
        filters={filters}
        imageUrl={imageUrl}
        isLoading={isLoading}
        isPlaying={isPlaying}
        isRunning={isRunning}
        needsRerun={needsRerun}
        playbackSpeed={playbackSpeed}
        progress={progress}
        runDetection={runDetection}
        setCurrentFrame={setCurrentFrame}
        setPlaybackSpeed={setPlaybackSpeed}
        togglePlayback={togglePlayback}
        totalFrames={totalFrames}
      />

      <DetectionPanelSidebar
        availableClasses={availableClasses}
        currentDetections={currentDetections}
        currentFrame={currentFrame}
        data={data}
        filteredData={filteredData}
        filters={filters}
        onFiltersChange={setFilters}
      />
    </div>
  )
}
