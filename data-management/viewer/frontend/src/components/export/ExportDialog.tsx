import { useMemo, useRef, useState } from 'react'

import type { ExportRequestWithEdits } from '@/api/export'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useCapabilities } from '@/hooks/use-datasets'
import { useExport } from '@/hooks/use-export'
import { getEffectiveFrameCount, useEditStore } from '@/stores'
import { useEpisodeStore } from '@/stores'

import { ExportProgress } from './ExportProgress'

interface ExportDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  datasetId: string
  episodeIndices: number[]
}

function describeExport(count: number, lerobot: boolean | undefined): string {
  if (lerobot === undefined) return `Export ${count} episode(s) with applied edits.`
  return lerobot
    ? `Export ${count} episode(s) as a new LeRobot dataset with applied edits. Use a new or empty output directory.`
    : `Export ${count} episode(s) to HDF5 episode files with applied edits.`
}

/**
 * Dialog for configuring and executing episode exports
 */
export function ExportDialog({ open, onOpenChange, datasetId, episodeIndices }: ExportDialogProps) {
  const [outputPath, setOutputPath] = useState('/exports')
  const [applyEdits, setApplyEdits] = useState(true)
  const [includeSubtasks, setIncludeSubtasks] = useState(true)
  const [includeLanguage, setIncludeLanguage] = useState(true)
  const returnFocusRef = useRef<HTMLElement | null>(null)

  const getEditOperations = useEditStore((state) => state.getEditOperations)
  const removedFrames = useEditStore((state) => state.removedFrames)
  const insertedFrames = useEditStore((state) => state.insertedFrames)
  const currentEpisode = useEpisodeStore((state) => state.currentEpisode)
  const totalFrames = currentEpisode?.meta.length ?? 0
  const { isExporting, progress, result, error, startExport, cancelExport, reset } = useExport({
    datasetId,
  })
  const { data: capabilities } = useCapabilities(datasetId)

  // Calculate effective frame count based on edits
  const effectiveFrameCount = useMemo(() => {
    if (!applyEdits || totalFrames === 0) {
      return totalFrames
    }
    return getEffectiveFrameCount(totalFrames, insertedFrames, removedFrames)
  }, [applyEdits, totalFrames, insertedFrames, removedFrames])

  const handleExport = () => {
    const operations = applyEdits ? getEditOperations() : null
    const edits =
      operations && !includeSubtasks ? { ...operations, subtasks: undefined } : operations
    const request: ExportRequestWithEdits = {
      episodeIndices,
      outputPath,
      applyEdits,
      edits: edits ? { [edits.episodeIndex]: edits } : undefined,
      ...(capabilities?.isLerobotDataset ? { includeLanguageInstructions: includeLanguage } : {}),
    }
    startExport(request)
  }

  const handleClose = () => {
    if (!isExporting) {
      reset()
      onOpenChange(false)
    }
  }

  const showProgress = isExporting || result !== null || error !== null

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent
        className="sm:max-w-[500px]"
        onOpenAutoFocus={() => {
          if (document.activeElement instanceof HTMLElement) {
            returnFocusRef.current = document.activeElement
          }
        }}
        onCloseAutoFocus={(event) => {
          if (returnFocusRef.current) {
            event.preventDefault()
            returnFocusRef.current.focus()
          }
        }}
      >
        <DialogHeader>
          <DialogTitle>Export Episodes</DialogTitle>
          <DialogDescription>
            {describeExport(episodeIndices.length, capabilities?.isLerobotDataset)}
          </DialogDescription>
        </DialogHeader>

        {showProgress ? (
          <ExportProgress progress={progress} result={result} error={error} />
        ) : (
          <div className="grid gap-4 py-4">
            <div className="grid gap-2">
              <Label htmlFor="output-path">Output Directory</Label>
              <Input
                id="output-path"
                value={outputPath}
                onChange={(e) => setOutputPath(e.target.value)}
                placeholder="/path/to/exports"
              />
            </div>

            <div className="flex items-center space-x-2">
              <Checkbox
                id="apply-edits"
                checked={applyEdits}
                onCheckedChange={(checked) => setApplyEdits(checked === true)}
              />
              <Label htmlFor="apply-edits">Apply crop, resize, and frame removal</Label>
            </div>

            <div className="flex items-center space-x-2">
              <Checkbox
                id="include-subtasks"
                checked={includeSubtasks}
                onCheckedChange={(checked) => setIncludeSubtasks(checked === true)}
              />
              <Label htmlFor="include-subtasks">
                {capabilities?.isLerobotDataset
                  ? 'Include subtasks as LeRobot subtask annotations'
                  : 'Include subtask metadata'}
              </Label>
            </div>

            {capabilities?.isLerobotDataset && (
              <div className="flex items-center space-x-2">
                <Checkbox
                  id="include-language"
                  checked={includeLanguage}
                  onCheckedChange={(checked) => setIncludeLanguage(checked === true)}
                />
                <Label htmlFor="include-language">
                  Include language instructions as LeRobot task phrasings and plan
                </Label>
              </div>
            )}

            <div className="text-muted-foreground space-y-1 text-sm">
              <div>Episodes to export: {episodeIndices.length}</div>
              {totalFrames > 0 && (
                <div>
                  Frames: {effectiveFrameCount}
                  {applyEdits && effectiveFrameCount !== totalFrames && (
                    <span className="ml-1 text-xs">
                      (original: {totalFrames}, removed: {removedFrames.size}, inserted:{' '}
                      {insertedFrames.size})
                    </span>
                  )}
                </div>
              )}
            </div>
          </div>
        )}

        <DialogFooter>
          {isExporting ? (
            <Button variant="destructive" onClick={cancelExport}>
              Cancel Export
            </Button>
          ) : result?.success ? (
            <Button onClick={handleClose}>Done</Button>
          ) : (
            <>
              <Button variant="outline" onClick={handleClose}>
                Cancel
              </Button>
              <Button onClick={handleExport}>Start Export</Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
