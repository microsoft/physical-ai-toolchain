import { useMemo, useState } from 'react'

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

/**
 * Dialog for configuring and executing episode exports
 */
export function ExportDialog({ open, onOpenChange, datasetId, episodeIndices }: ExportDialogProps) {
  const [outputPath, setOutputPath] = useState('/exports')
  const [applyEdits, setApplyEdits] = useState(true)
  const [includeSubtasks, setIncludeSubtasks] = useState(true)

  const getEditOperations = useEditStore((state) => state.getEditOperations)
  const removedFrames = useEditStore((state) => state.removedFrames)
  const insertedFrames = useEditStore((state) => state.insertedFrames)
  const currentEpisode = useEpisodeStore((state) => state.currentEpisode)
  const totalFrames = currentEpisode?.meta.length ?? 0
  const { isExporting, progress, result, error, startExport, cancelExport, reset } = useExport({
    datasetId,
  })

  // Calculate effective frame count based on edits
  const effectiveFrameCount = useMemo(() => {
    if (!applyEdits || totalFrames === 0) {
      return totalFrames
    }
    return getEffectiveFrameCount(totalFrames, insertedFrames, removedFrames)
  }, [applyEdits, totalFrames, insertedFrames, removedFrames])

  const handleExport = () => {
    const edits = applyEdits ? getEditOperations() : null
    const request: ExportRequestWithEdits = {
      episodeIndices,
      outputPath,
      applyEdits,
      includeSubtasks,
      format: 'hdf5',
      edits: edits ? { [edits.episodeIndex]: edits } : undefined,
    }
    startExport(request)
  }

  const handleClose = () => {
    if (!isExporting) {
      reset()
      onOpenChange(false)
    }
  }

  const showProgress = isExporting || result !== null

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="sm:max-w-[500px]">
        <DialogHeader>
          <DialogTitle>Export Episodes</DialogTitle>
          <DialogDescription>
            Export {episodeIndices.length} episode(s) with applied edits.
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
              <Label htmlFor="include-subtasks">Include subtask metadata</Label>
            </div>

            <div className="space-y-1 text-sm text-muted-foreground">
              <div>Episodes to export: {episodeIndices.join(', ')}</div>
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
