/**
 * Open-vocabulary object detection widget.
 *
 * Runs YOLO-World on a single reference frame (default: the first frame of the
 * episode), lets the annotator refine the label list, re-run with the refined
 * labels, and persist the resulting boxes onto the episode annotation alongside
 * subtask labels and language instructions.
 */

import { Loader2, Plus, RotateCcw, Save, Scan, Trash2, X } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { runDetection } from '@/api/detection'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { useAnnotationStore } from '@/stores'
import { useDatasetStore } from '@/stores/dataset-store'
import { useEpisodeStore } from '@/stores/episode-store'
import type { ObjectDetectionAnnotation, ObjectDetectionBox } from '@/types'
import type { Detection } from '@/types/detection'

const DEFAULT_OPEN_VOCAB_MODEL = 'yolov8s-world'
const PLACEHOLDER_LABELS = 'red block, gripper, tool'

interface DrawState {
  detections: Detection[]
  imageWidth: number
  imageHeight: number
}

function paletteColor(index: number): string {
  const palette = [
    '#FF6B6B',
    '#4ECDC4',
    '#FFD93D',
    '#6C5CE7',
    '#74B9FF',
    '#A29BFE',
    '#55EFC4',
    '#FAB1A0',
  ]
  return palette[index % palette.length]
}

export function ObjectDetectionWidget() {
  const currentDataset = useDatasetStore((state) => state.currentDataset)
  const currentEpisode = useEpisodeStore((state) => state.currentEpisode)
  const currentAnnotation = useAnnotationStore((state) => state.currentAnnotation)
  const upsertObjectDetection = useAnnotationStore((state) => state.upsertObjectDetection)
  const removeObjectDetection = useAnnotationStore((state) => state.removeObjectDetection)

  const datasetId = currentDataset?.id ?? null
  const episodeIndex = currentEpisode?.meta.index ?? null
  const availableCameras = useMemo(() => currentEpisode?.cameras ?? [], [currentEpisode])

  const [frameIndex, setFrameIndex] = useState(0)
  const [camera, setCamera] = useState<string | null>(null)
  const [labels, setLabels] = useState<string[]>([])
  const [labelInput, setLabelInput] = useState('')
  const [detections, setDetections] = useState<Detection[] | null>(null)
  const [queriedLabels, setQueriedLabels] = useState<string[]>([])
  const [isRunning, setIsRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [draw, setDraw] = useState<DrawState | null>(null)

  // Keep the selected camera valid for the current episode. If the prior
  // selection is no longer in the camera list (episode change, dataset swap),
  // fall back to the first available camera so previews and detection both
  // hit a real frame.
  useEffect(() => {
    if (availableCameras.length === 0) {
      if (camera !== null) setCamera(null)
      return
    }
    if (!camera || !availableCameras.includes(camera)) {
      setCamera(availableCameras[0])
    }
  }, [availableCameras, camera])

  // Restore saved detections for the current reference frame when the episode
  // changes, so the widget reflects what's already persisted.
  useEffect(() => {
    if (!currentAnnotation) {
      setDetections(null)
      setQueriedLabels([])
      setLabels([])
      return
    }
    const saved = currentAnnotation.objectDetections?.find(
      (entry) => entry.frameIndex === frameIndex && (!camera || entry.camera === camera),
    )
    if (saved) {
      const restored: Detection[] = saved.detections.map((det) => ({
        class_id: 0,
        class_name: det.label,
        confidence: det.confidence,
        bbox: det.bbox,
      }))
      setDetections(restored)
      setQueriedLabels(saved.queriedLabels)
      setLabels(saved.queriedLabels)
    } else {
      setDetections(null)
      setQueriedLabels([])
    }
  }, [currentAnnotation, frameIndex, camera])

  const imageUrl = useMemo(() => {
    if (!datasetId || episodeIndex == null || !camera) return null
    return `/api/datasets/${datasetId}/episodes/${episodeIndex}/frames/${frameIndex}?camera=${encodeURIComponent(camera)}`
  }, [datasetId, episodeIndex, frameIndex, camera])

  const addLabel = useCallback((raw: string) => {
    const trimmed = raw.trim()
    if (!trimmed) return
    setLabels((current) => (current.includes(trimmed) ? current : [...current, trimmed]))
    setLabelInput('')
  }, [])

  const removeLabel = useCallback((label: string) => {
    setLabels((current) => current.filter((entry) => entry !== label))
  }, [])

  const handleLabelKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLInputElement>) => {
      if (event.key === 'Enter' || event.key === ',' || event.key === 'Tab') {
        if (labelInput.trim()) {
          event.preventDefault()
          addLabel(labelInput)
        }
      } else if (event.key === 'Backspace' && !labelInput && labels.length > 0) {
        setLabels((current) => current.slice(0, -1))
      }
    },
    [addLabel, labelInput, labels.length],
  )

  const handleLabelBlur = useCallback(() => {
    if (labelInput.trim()) {
      addLabel(labelInput)
    }
  }, [addLabel, labelInput])

  const handleRun = useCallback(async () => {
    if (!datasetId || episodeIndex == null) return
    setIsRunning(true)
    setError(null)
    try {
      const requestLabels = labels.length > 0 ? labels : undefined
      const summary = await runDetection(datasetId, episodeIndex, {
        frames: [frameIndex],
        labels: requestLabels,
        model: requestLabels ? DEFAULT_OPEN_VOCAB_MODEL : undefined,
        camera: camera ?? undefined,
      })
      const frameResult = summary.detections_by_frame.find((entry) => entry.frame === frameIndex)
      setDetections(frameResult?.detections ?? [])
      setQueriedLabels(requestLabels ?? [])
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Detection failed')
      setDetections(null)
    } finally {
      setIsRunning(false)
    }
  }, [datasetId, episodeIndex, frameIndex, labels, camera])

  const handleSave = useCallback(() => {
    if (!detections || !camera) return
    const annotation: ObjectDetectionAnnotation = {
      frameIndex,
      camera,
      queriedLabels,
      detections: detections.map<ObjectDetectionBox>((det) => ({
        label: det.class_name,
        confidence: det.confidence,
        bbox: det.bbox,
      })),
      model: queriedLabels.length > 0 ? DEFAULT_OPEN_VOCAB_MODEL : 'yolo11n',
    }
    upsertObjectDetection(annotation)
  }, [detections, frameIndex, queriedLabels, upsertObjectDetection, camera])

  const handleClearSaved = useCallback(() => {
    removeObjectDetection(frameIndex)
  }, [frameIndex, removeObjectDetection])

  const handleResetResults = useCallback(() => {
    setDetections(null)
    setQueriedLabels([])
    setError(null)
  }, [])

  const savedForFrame = useMemo(
    () => currentAnnotation?.objectDetections?.find((entry) => entry.frameIndex === frameIndex),
    [currentAnnotation, frameIndex],
  )

  const totalSaved = currentAnnotation?.objectDetections?.length ?? 0

  if (!currentDataset || !currentEpisode) {
    return (
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm">Object Detection</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-muted-foreground text-sm">No episode selected</p>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center justify-between text-sm">
          Object Detection
          {totalSaved > 0 && (
            <Badge variant="secondary" className="text-xs font-normal">
              {totalSaved} saved
            </Badge>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex items-end gap-2">
          <div className="flex-1">
            <label
              htmlFor="object-detection-frame"
              className="text-muted-foreground mb-1 block text-xs"
            >
              Reference frame
            </label>
            <Input
              id="object-detection-frame"
              type="number"
              min={0}
              max={Math.max(currentEpisode.meta.length - 1, 0)}
              value={frameIndex}
              onChange={(event) => {
                const next = Number.parseInt(event.target.value, 10)
                if (Number.isFinite(next) && next >= 0) {
                  setFrameIndex(next)
                }
              }}
              className="h-8"
            />
          </div>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => setFrameIndex(0)}
            disabled={frameIndex === 0}
            title="Reset to first frame"
          >
            <RotateCcw className="h-3 w-3" />
          </Button>
        </div>

        <div>
          <label
            htmlFor="object-detection-camera"
            className="text-muted-foreground mb-1 block text-xs"
          >
            Camera
          </label>
          {availableCameras.length === 0 ? (
            <p className="text-muted-foreground text-xs italic">
              No cameras reported for this episode.
            </p>
          ) : (
            <Select value={camera ?? undefined} onValueChange={(value) => setCamera(value)}>
              <SelectTrigger id="object-detection-camera" className="h-8">
                <SelectValue placeholder="Select camera" />
              </SelectTrigger>
              <SelectContent>
                {availableCameras.map((cam) => (
                  <SelectItem key={cam} value={cam}>
                    {cam}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </div>

        <div>
          <label
            htmlFor="object-detection-label-input"
            className="text-muted-foreground mb-1 block text-xs"
          >
            Labels to detect{' '}
            <span className="text-muted-foreground/70">(leave empty for default COCO classes)</span>
          </label>
          <div className="flex flex-wrap items-center gap-1 rounded-md border px-2 py-1.5">
            {labels.map((label, index) => (
              <Badge
                key={`${label}-${index}`}
                variant="secondary"
                className="gap-1 text-xs font-normal"
                style={{ borderLeftColor: paletteColor(index), borderLeftWidth: 3 }}
              >
                {label}
                <button
                  type="button"
                  className="hover:text-destructive"
                  onClick={() => removeLabel(label)}
                  aria-label={`Remove label ${label}`}
                >
                  <X className="h-3 w-3" />
                </button>
              </Badge>
            ))}
            <Input
              id="object-detection-label-input"
              value={labelInput}
              onChange={(event) => setLabelInput(event.target.value)}
              onKeyDown={handleLabelKeyDown}
              onBlur={handleLabelBlur}
              placeholder={labels.length === 0 ? PLACEHOLDER_LABELS : 'Add label…'}
              className="h-7 min-w-[8ch] flex-1 border-0 px-1 shadow-none focus-visible:ring-0"
            />
          </div>
        </div>

        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            size="sm"
            onClick={() => void handleRun()}
            disabled={isRunning || !datasetId || episodeIndex == null}
          >
            {isRunning ? (
              <>
                <Loader2 className="mr-2 h-3 w-3 animate-spin" />
                Detecting…
              </>
            ) : (
              <>
                <Scan className="mr-2 h-3 w-3" />
                {detections ? 'Re-run' : 'Detect'}
              </>
            )}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={handleSave}
            disabled={!detections || detections.length === 0}
          >
            <Save className="mr-2 h-3 w-3" />
            {savedForFrame ? 'Update saved' : 'Save'}
          </Button>
          {savedForFrame && (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={handleClearSaved}
              title="Remove saved detections for this frame"
            >
              <Trash2 className="mr-2 h-3 w-3" />
              Remove saved
            </Button>
          )}
          {detections && (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={handleResetResults}
              title="Clear current results"
            >
              <X className="mr-2 h-3 w-3" />
              Clear
            </Button>
          )}
        </div>

        {error && (
          <div className="bg-destructive/10 text-destructive rounded-sm p-2 text-xs">{error}</div>
        )}

        {imageUrl && (
          <FramePreview
            imageUrl={imageUrl}
            detections={detections}
            onLoaded={(width, height) =>
              setDraw({ detections: detections ?? [], imageWidth: width, imageHeight: height })
            }
          />
        )}

        {detections && detections.length === 0 && !isRunning && (
          <p className="text-muted-foreground text-xs">
            No matches{queriedLabels.length > 0 ? ' for the supplied labels' : ''}. Try different
            labels or lower confidence by re-running.
          </p>
        )}

        {detections && detections.length > 0 && (
          <div className="space-y-1">
            <p className="text-muted-foreground text-xs">
              Detections ({detections.length}) · frame {frameIndex}
              {draw && ` · ${draw.imageWidth}×${draw.imageHeight}px`}
            </p>
            <ul className="divide-y rounded-md border text-xs">
              {detections.map((det, index) => (
                <li
                  key={`${det.class_name}-${index}`}
                  className="flex items-center gap-2 px-2 py-1"
                >
                  <span
                    className="inline-block h-2 w-2 rounded-full"
                    style={{ backgroundColor: paletteColor(index) }}
                  />
                  <span className="font-medium">{det.class_name}</span>
                  <span className="text-muted-foreground">
                    {(det.confidence * 100).toFixed(0)}%
                  </span>
                  <span className="text-muted-foreground ml-auto font-mono">
                    [{det.bbox.map((value) => Math.round(value)).join(', ')}]
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {savedForFrame && (
          <p className="text-muted-foreground text-xs">
            <Plus className="mr-1 inline h-3 w-3" />
            Saved on this frame: {savedForFrame.detections.length} box(es) · queried{' '}
            {savedForFrame.queriedLabels.length > 0
              ? savedForFrame.queriedLabels.join(', ')
              : 'COCO defaults'}
          </p>
        )}
      </CardContent>
    </Card>
  )
}

interface FramePreviewProps {
  imageUrl: string
  detections: Detection[] | null
  onLoaded: (imageWidth: number, imageHeight: number) => void
}

function FramePreview({ imageUrl, detections, onLoaded }: FramePreviewProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    const container = containerRef.current
    if (!canvas || !container) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    let cancelled = false
    const img = new Image()
    img.crossOrigin = 'anonymous'
    img.onload = () => {
      if (cancelled) return
      const containerWidth = container.clientWidth || 320
      const scale = containerWidth / img.width
      const drawWidth = img.width * scale
      const drawHeight = img.height * scale
      canvas.width = drawWidth
      canvas.height = drawHeight
      ctx.drawImage(img, 0, 0, drawWidth, drawHeight)

      onLoaded(img.width, img.height)

      if (!detections || detections.length === 0) return
      detections.forEach((det, index) => {
        const [x1, y1, x2, y2] = det.bbox
        const sx = x1 * scale
        const sy = y1 * scale
        const sw = (x2 - x1) * scale
        const sh = (y2 - y1) * scale
        const color = paletteColor(index)

        ctx.strokeStyle = color
        ctx.lineWidth = 2
        ctx.strokeRect(sx, sy, sw, sh)

        const label = `${det.class_name} ${(det.confidence * 100).toFixed(0)}%`
        ctx.font = '11px sans-serif'
        const textWidth = ctx.measureText(label).width
        ctx.fillStyle = color
        ctx.fillRect(sx, Math.max(sy - 14, 0), textWidth + 6, 14)
        ctx.fillStyle = '#000'
        ctx.fillText(label, sx + 3, Math.max(sy - 3, 11))
      })
    }
    img.onerror = () => {
      if (cancelled) return
      ctx.clearRect(0, 0, canvas.width, canvas.height)
    }
    img.src = imageUrl
    return () => {
      cancelled = true
    }
  }, [imageUrl, detections, onLoaded])

  return (
    <div ref={containerRef} className="bg-muted overflow-hidden rounded-md">
      <canvas ref={canvasRef} className="block w-full" />
    </div>
  )
}
