import { AlertTriangle, BarChart3, Loader2, Pause, Play, RotateCcw, Scan } from 'lucide-react'

import { PlaybackControlStrip } from '@/components/playback/PlaybackControlStrip'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Progress } from '@/components/ui/progress'
import type {
  Detection,
  DetectionFilters,
  DetectionResult,
  EpisodeDetectionSummary,
} from '@/types/detection'

import { DetectionCharts } from './DetectionCharts'
import { DetectionTimeline } from './DetectionTimeline'
import { DetectionViewer } from './DetectionViewer'

interface DetectionPanelResultsColumnProps {
  currentDetections: Detection[]
  currentFrame: number
  data: EpisodeDetectionSummary | null | undefined
  error: Error | null
  filteredData: EpisodeDetectionSummary | null
  imageUrl: string | null
  isLoading: boolean
  isPlaying: boolean
  isRunning: boolean
  needsRerun: boolean
  playbackSpeed: number
  progress: number
  filters: DetectionFilters
  runDetection: (input: { confidence: number }) => void
  setCurrentFrame: (frame: number) => void
  setPlaybackSpeed: (speed: number) => void
  togglePlayback: () => void
  totalFrames: number
}

export function DetectionPanelResultsColumn({
  currentDetections,
  currentFrame,
  data,
  error,
  filteredData,
  imageUrl,
  isLoading,
  isPlaying,
  isRunning,
  needsRerun,
  playbackSpeed,
  progress,
  filters,
  runDetection,
  setCurrentFrame,
  setPlaybackSpeed,
  togglePlayback,
  totalFrames,
}: DetectionPanelResultsColumnProps) {
  return (
    <div className="flex flex-col gap-4 lg:col-span-2">
      <Card className="flex-shrink-0">
        <CardContent className="p-4">
          <div className="mb-4 flex items-center justify-between">
            <div className="flex items-center gap-3">
              <Scan className="h-5 w-5 text-primary" />
              <div>
                <h3 className="font-medium">YOLO11 Object Detection</h3>
                <p className="text-xs text-muted-foreground">
                  Detect objects in all frames of this episode
                </p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              {needsRerun && data && (
                <span className="flex items-center gap-1 text-xs text-orange-500">
                  <AlertTriangle className="h-3 w-3" />
                  Edits detected
                </span>
              )}
              <Button
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
                    {needsRerun ? 'Re-run Detection' : data ? 'Run Again' : 'Run Detection'}
                  </>
                )}
              </Button>
            </div>
          </div>

          {isRunning && (
            <div className="space-y-2">
              <div className="flex justify-between text-xs text-muted-foreground">
                <span>Processing {totalFrames} frames...</span>
                <span>{Math.round(progress)}%</span>
              </div>
              <Progress value={progress} className="h-2" />
            </div>
          )}

          {error && (
            <div className="rounded bg-destructive/10 p-3 text-sm text-destructive">
              <strong>Error:</strong> {error.message}
            </div>
          )}
        </CardContent>
      </Card>

      <Card className="flex-shrink-0">
        <CardContent className="p-4">
          {!data && !isRunning ? (
            <div className="flex aspect-video items-center justify-center rounded-lg bg-muted">
              <div className="text-center text-muted-foreground">
                <Scan className="mx-auto mb-4 h-16 w-16 opacity-30" />
                <p className="mb-2 text-lg">No detection results</p>
                <p className="text-sm">Click "Run Detection" to analyze all frames with YOLO11</p>
              </div>
            </div>
          ) : isRunning && !data ? (
            <div className="flex aspect-video items-center justify-center rounded-lg bg-muted">
              <div className="text-center text-muted-foreground">
                <Loader2 className="mx-auto mb-4 h-16 w-16 animate-spin opacity-50" />
                <p className="mb-2 text-lg">Processing frames...</p>
                <p className="text-sm">This may take a moment for episodes with many frames</p>
              </div>
            </div>
          ) : (
            <>
              <div className="mb-4 aspect-video">
                <DetectionViewer imageUrl={imageUrl} detections={currentDetections} />
              </div>

              <PlaybackControlStrip
                currentFrame={currentFrame}
                totalFrames={totalFrames}
                controls={
                  <>
                    <Button size="sm" onClick={togglePlayback} className="gap-1">
                      {isPlaying ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
                      {isPlaying ? 'Pause' : 'Play'}
                    </Button>
                    <Button size="sm" variant="outline" onClick={() => setCurrentFrame(0)}>
                      <RotateCcw className="h-4 w-4" />
                    </Button>
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm">Speed:</span>
                      {[0.5, 1, 2].map((speed) => (
                        <Button
                          key={speed}
                          size="sm"
                          variant={playbackSpeed === speed ? 'default' : 'outline'}
                          onClick={() => setPlaybackSpeed(speed)}
                          className="px-2"
                        >
                          {speed}x
                        </Button>
                      ))}
                    </div>
                  </>
                }
                slider={
                  <input
                    type="range"
                    min={0}
                    max={totalFrames - 1}
                    value={currentFrame}
                    onChange={(event) => setCurrentFrame(parseInt(event.target.value, 10))}
                    className="w-full"
                  />
                }
              />
            </>
          )}
        </CardContent>
      </Card>

      {data && (
        <Card className="flex-shrink-0">
          <CardHeader className="px-4 py-3">
            <CardTitle className="text-sm">Detection Timeline</CardTitle>
          </CardHeader>
          <CardContent className="p-4 pt-0">
            <p className="mb-3 text-sm text-muted-foreground">
              Frame {currentFrame} - {currentDetections.length} detection
              {currentDetections.length !== 1 ? 's' : ''}
            </p>
            <DetectionTimeline
              detectionsPerFrame={filteredData?.detections_by_frame ?? ([] as DetectionResult[])}
              totalFrames={totalFrames}
              currentFrame={currentFrame}
              onFrameClick={setCurrentFrame}
            />
          </CardContent>
        </Card>
      )}

      {data && filteredData && (
        <Card className="flex-shrink-0">
          <CardHeader className="px-4 py-3">
            <CardTitle className="flex items-center gap-2 text-sm">
              <BarChart3 className="h-4 w-4" />
              Detection Statistics
            </CardTitle>
          </CardHeader>
          <CardContent className="p-4 pt-0">
            <DetectionCharts summary={filteredData} />
          </CardContent>
        </Card>
      )}
    </div>
  )
}
