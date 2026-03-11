/**
 * Trajectory visualization component showing joint positions over time.
 *
 * Performance optimizations:
 * - CurrentFrameMarker is isolated to prevent full chart re-renders on frame changes
 * - Chart data is memoized based on trajectory data and velocity toggle
 * - Reference line position updates without re-rendering chart lines
 */

import { memo } from 'react';

import { cn } from '@/lib/utils'

import { JointConfigDefaultsEditor } from './JointConfigDefaultsEditor'
import { TrajectoryPlotChart } from './TrajectoryPlotChart'
import { TrajectoryPlotControls } from './TrajectoryPlotControls'
import { useTrajectoryPlotState } from './useTrajectoryPlotState'

interface TrajectoryPlotProps {
  /** Additional CSS classes */
  className?: string;
  /** Callback invoked after a successful save */
  onSaved?: () => void;
  /** Active graph selection range */
  selectedRange?: [number, number] | null;
  /** Called when the graph selection changes */
  onSelectedRangeChange?: (range: [number, number] | null) => void;
  /** Called when the user creates a subtask from the selected range */
  onCreateSubtaskFromRange?: (range: [number, number]) => void;
  /** Called when the user begins dragging a graph selection */
  onSelectionStart?: () => void;
  /** Called after a graph drag selection is committed */
  onSelectionComplete?: (range: [number, number]) => void;
  /** Called when the graph explicitly seeks to a frame */
  onSeekFrame?: (frame: number) => void;
}

/**
 * Line chart showing joint positions over time with current frame marker.
 *
 * Performance: Uses isolated CurrentFrameMarker to prevent full chart re-renders
 * when scrubbing through frames.
 *
 * @example
 * ```tsx
 * <TrajectoryPlot className="h-64" />
 * ```
 */
export const TrajectoryPlot = memo(function TrajectoryPlot({
  className,
  onSaved,
  selectedRange = null,
  onSelectedRangeChange,
  onCreateSubtaskFromRange,
  onSelectionStart,
  onSelectionComplete,
  onSeekFrame,
}: TrajectoryPlotProps) {
  const state = useTrajectoryPlotState({
    onSaved,
    selectedRange,
    onSelectedRangeChange,
    onCreateSubtaskFromRange,
    onSelectionStart,
    onSelectionComplete,
    onSeekFrame,
  })

  if (!state.currentEpisode) {
    return (
      <div
        className={cn(
          'flex items-center justify-center bg-muted rounded-lg',
          className
        )}
      >
        <p className="text-muted-foreground">No episode selected</p>
      </div>
    );
  }

  if (state.chartData.length === 0) {
    return (
      <div
        className={cn(
          'flex items-center justify-center bg-muted rounded-lg',
          className
        )}
      >
        <p className="text-muted-foreground">No trajectory data available</p>
      </div>
    );
  }

  return (
    <div className={cn('flex min-h-0 flex-col gap-2', className)}>
      <TrajectoryPlotControls
        jointCount={state.jointCount}
        selectedJoints={state.selectedJoints}
        onSelectJoints={state.setSelectedJoints}
        groups={state.jointConfig.groups}
        labels={state.jointConfig.labels}
        onEditJointLabel={state.withSave(state.updateLabel)}
        onEditGroupLabel={state.withSave(state.updateGroupLabel)}
        onCreateGroup={state.withSave(state.createGroup)}
        onDeleteGroup={state.withSave(state.deleteGroup)}
        onMoveJoint={state.withSave(state.moveJoint)}
        onOpenDefaults={() => state.setDefaultsOpen(true)}
        showVelocity={state.showVelocity}
        onSetShowVelocity={state.setShowVelocity}
        showNormalized={state.showNormalized}
        isNormalizationDisabled={state.isNormalizationDisabled}
        onToggleNormalization={state.toggleNormalization}
      />

      <TrajectoryPlotChart
        chartData={state.chartData}
        currentFrame={state.currentFrame}
        selectedJoints={state.selectedJoints}
        resolveLabel={state.resolveLabel}
        trajectoryAdjustments={state.trajectoryAdjustments}
        showVelocity={state.showVelocity}
        showNormalized={state.showNormalized}
        selectedRange={selectedRange}
        selectionHighlight={state.selectionHighlight}
        contextMenuPosition={state.selection.contextMenuPosition}
        onChartClick={state.handleChartClick}
        onSelectionContextMenu={state.selection.handleSelectionContextMenu}
        onSelectionPointerDown={state.selection.handleSelectionPointerDown}
        onSelectionPointerMove={state.selection.handleSelectionPointerMove}
        onSelectionPointerUp={state.selection.handleSelectionPointerUp}
        onCreateSubtaskFromRange={onCreateSubtaskFromRange}
        onDismissContextMenu={state.selection.dismissContextMenu}
        selectionSurfaceRef={state.selectionSurfaceRef}
      />

      <JointConfigDefaultsEditor
        open={state.defaultsOpen}
        onOpenChange={state.setDefaultsOpen}
        groups={state.defaults?.groups ?? state.jointConfig.groups}
        labels={state.defaults?.labels ?? state.jointConfig.labels}
        onSave={(config) => {
          state.saveDefaults.mutate(
            { datasetId: '_defaults', ...config },
            {
              onSuccess: () => {
                state.setDefaultsOpen(false)
                onSaved?.()
              },
            },
          )
        }}
        isSaving={state.saveDefaults.isPending}
      />
    </div>
  );
});
