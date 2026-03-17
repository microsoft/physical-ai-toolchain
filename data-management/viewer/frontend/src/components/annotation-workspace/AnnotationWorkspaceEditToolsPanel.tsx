import { RotateCcw } from 'lucide-react'

import {
  ColorAdjustmentControls,
  FrameInsertionToolbar,
  FrameRemovalToolbar,
  TrajectoryEditor,
  TransformControls,
} from '@/components/frame-editor'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'

interface AnnotationWorkspaceEditToolsPanelProps {
  onClearTransforms: () => void
  canResetTransforms: boolean
}

export function AnnotationWorkspaceEditToolsPanel({
  onClearTransforms,
  canResetTransforms,
}: AnnotationWorkspaceEditToolsPanelProps) {
  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-sm font-medium">Edit Tools</h3>
      </div>

      <FrameRemovalToolbar />

      <Separator />
      <FrameInsertionToolbar />

      <Separator />
      <div>
        <h3 className="mb-3 text-sm font-medium">Image Transform</h3>
        <TransformControls />
      </div>

      <Separator />
      <ColorAdjustmentControls />

      <Separator />
      <div>
        <h3 className="mb-3 text-sm font-medium">Trajectory Adjustment</h3>
        <TrajectoryEditor />
      </div>

      <Separator />
      <Button
        variant="outline"
        size="sm"
        onClick={onClearTransforms}
        disabled={!canResetTransforms}
        className="w-full"
      >
        <RotateCcw className="mr-2 h-4 w-4" />
        Reset All Image Transforms
      </Button>
    </div>
  )
}
