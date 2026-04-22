/**
 * Trajectory editor for adjusting XYZ position data at each frame.
 *
 * Provides controls to modify joint positions with delta adjustments
 * that are stored non-destructively in the edit store.
 */

import { Check, RotateCcw, Trash2 } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'
import { useEpisodeStore, usePlaybackControls, useTrajectoryAdjustmentState } from '@/stores'
import type { TrajectoryPoint } from '@/types/api'

interface TrajectoryEditorProps {
  /** Additional CSS classes */
  className?: string
}

interface AxisInputProps {
  label: string
  value: number
  delta: number
  onDeltaChange: (delta: number) => void
  color: string
}

/**
 * Individual axis input with delta adjustment.
 */
function AxisInput({ label, value, delta, onDeltaChange, color }: AxisInputProps) {
  const adjustedValue = value + delta
  // Local state for text input to allow partial typing (e.g., "-" or "0.")
  const [inputValue, setInputValue] = useState(delta.toFixed(4))

  // Sync local input when delta changes externally
  useEffect(() => {
    setInputValue(delta.toFixed(4))
  }, [delta])

  const handleInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const text = e.target.value
      setInputValue(text)

      const parsed = parseFloat(text)
      if (!Number.isNaN(parsed)) {
        onDeltaChange(parsed)
      }
    },
    [onDeltaChange],
  )

  const handleInputBlur = useCallback(() => {
    // On blur, reset to properly formatted value
    setInputValue(delta.toFixed(4))
  }, [delta])

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between">
        <Label className={cn('text-xs font-medium', color)}>{label}</Label>
        <span className="text-muted-foreground font-mono text-xs">{adjustedValue.toFixed(4)}</span>
      </div>
      <div className="flex items-center gap-2">
        <input
          type="range"
          value={delta}
          onChange={(e) => onDeltaChange(parseFloat(e.target.value))}
          min={-0.5}
          max={0.5}
          step={0.001}
          className="accent-primary h-2 flex-1"
        />
        <Input
          type="number"
          value={inputValue}
          onChange={handleInputChange}
          onBlur={handleInputBlur}
          step={0.001}
          className="h-7 w-20 font-mono text-xs"
        />
      </div>
      {delta !== 0 && (
        <div className="text-muted-foreground text-xs">
          Original: {value.toFixed(4)} → Δ: {delta >= 0 ? '+' : ''}
          {delta.toFixed(4)}
        </div>
      )}
    </div>
  )
}

interface ArmEditorProps {
  title: string
  titleColor: string
  currentPoint: TrajectoryPoint
  posIndices: [number, number, number]
  gripperIndex: number
  delta: [number, number, number] | undefined
  gripperOverride: number | undefined
  onDeltaChange: (delta: [number, number, number]) => void
  onGripperChange: (value: number | undefined) => void
  onReset: () => void
}

/**
 * Editor panel for a single arm's XYZ and gripper.
 */
function ArmEditor({
  title,
  titleColor,
  currentPoint,
  posIndices,
  gripperIndex,
  delta,
  gripperOverride,
  onDeltaChange,
  onGripperChange,
  onReset,
}: ArmEditorProps) {
  const positions = currentPoint.jointPositions
  const currentDelta = useMemo((): [number, number, number] => delta ?? [0, 0, 0], [delta])
  const hasChanges = delta !== undefined || gripperOverride !== undefined
  const currentGripperValue = gripperOverride ?? positions[gripperIndex] ?? 0

  // Local state for gripper input to allow partial typing
  const [gripperInputValue, setGripperInputValue] = useState(currentGripperValue.toFixed(3))

  // Sync local input when gripper value changes externally
  useEffect(() => {
    setGripperInputValue(currentGripperValue.toFixed(3))
  }, [currentGripperValue])

  const handleGripperInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const text = e.target.value
      setGripperInputValue(text)

      const parsed = parseFloat(text)
      if (!Number.isNaN(parsed)) {
        onGripperChange(parsed)
      }
    },
    [onGripperChange],
  )

  const handleGripperInputBlur = useCallback(() => {
    setGripperInputValue(currentGripperValue.toFixed(3))
  }, [currentGripperValue])

  const handleAxisChange = useCallback(
    (axis: 0 | 1 | 2, value: number) => {
      const newDelta: [number, number, number] = [...currentDelta]
      newDelta[axis] = value
      onDeltaChange(newDelta)
    },
    [currentDelta, onDeltaChange],
  )

  return (
    <div className="bg-muted/50 space-y-3 rounded-lg p-3">
      <div className="flex items-center justify-between">
        <h4 className={cn('text-sm font-medium', titleColor)}>{title}</h4>
        {hasChanges && (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button variant="ghost" size="sm" className="h-6 w-6 p-0" onClick={onReset}>
                <RotateCcw className="h-3 w-3" />
              </Button>
            </TooltipTrigger>
            <TooltipContent>Reset {title} adjustments</TooltipContent>
          </Tooltip>
        )}
      </div>

      <div className="space-y-2">
        <AxisInput
          label="X"
          value={positions[posIndices[0]] ?? 0}
          delta={currentDelta[0]}
          onDeltaChange={(v) => handleAxisChange(0, v)}
          color="text-red-500"
        />
        <AxisInput
          label="Y"
          value={positions[posIndices[1]] ?? 0}
          delta={currentDelta[1]}
          onDeltaChange={(v) => handleAxisChange(1, v)}
          color="text-green-500"
        />
        <AxisInput
          label="Z"
          value={positions[posIndices[2]] ?? 0}
          delta={currentDelta[2]}
          onDeltaChange={(v) => handleAxisChange(2, v)}
          color="text-blue-500"
        />
      </div>

      {/* Gripper override */}
      <div className="space-y-1 border-t pt-2">
        <div className="flex items-center justify-between">
          <Label className="text-xs font-medium">Gripper</Label>
          <span className="text-muted-foreground font-mono text-xs">
            {currentGripperValue.toFixed(4)}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <input
            type="range"
            value={currentGripperValue}
            onChange={(e) => onGripperChange(parseFloat(e.target.value))}
            min={0}
            max={1}
            step={0.01}
            className="accent-primary h-2 flex-1"
          />
          <Input
            type="number"
            value={gripperInputValue}
            onChange={handleGripperInputChange}
            onBlur={handleGripperInputBlur}
            step={0.01}
            min={0}
            max={1}
            className="h-7 w-20 font-mono text-xs"
          />
          {gripperOverride !== undefined && (
            <Button
              variant="ghost"
              size="sm"
              className="h-7 w-7 p-0"
              onClick={() => onGripperChange(undefined)}
            >
              <RotateCcw className="h-3 w-3" />
            </Button>
          )}
        </div>
      </div>
    </div>
  )
}

/**
 * Trajectory editor component for adjusting XYZ positions at the current frame.
 *
 * @example
 * ```tsx
 * <TrajectoryEditor className="mt-4" />
 * ```
 */
export function TrajectoryEditor({ className }: TrajectoryEditorProps) {
  const { currentFrame } = usePlaybackControls()
  const currentEpisode = useEpisodeStore((state) => state.currentEpisode)
  const {
    trajectoryAdjustments,
    setTrajectoryAdjustment,
    removeTrajectoryAdjustment,
    clearTrajectoryAdjustments,
  } = useTrajectoryAdjustmentState()

  // Get current trajectory point
  const currentPoint = useMemo(() => {
    const trajectoryData = currentEpisode?.trajectoryData || []
    if (trajectoryData.length === 0) return null
    return trajectoryData[Math.min(currentFrame, trajectoryData.length - 1)]
  }, [currentEpisode?.trajectoryData, currentFrame])

  // Get current frame adjustment
  const currentAdjustment = trajectoryAdjustments.get(currentFrame)

  // Track local state for real-time updates
  const [rightArmDelta, setRightArmDelta] = useState<[number, number, number]>(
    currentAdjustment?.rightArmDelta ?? [0, 0, 0],
  )
  const [leftArmDelta, setLeftArmDelta] = useState<[number, number, number]>(
    currentAdjustment?.leftArmDelta ?? [0, 0, 0],
  )
  const [rightGripper, setRightGripper] = useState<number | undefined>(
    currentAdjustment?.rightGripperOverride,
  )
  const [leftGripper, setLeftGripper] = useState<number | undefined>(
    currentAdjustment?.leftGripperOverride,
  )

  // Sync local state when frame changes
  useEffect(() => {
    const adj = trajectoryAdjustments.get(currentFrame)
    setRightArmDelta(adj?.rightArmDelta ?? [0, 0, 0])
    setLeftArmDelta(adj?.leftArmDelta ?? [0, 0, 0])
    setRightGripper(adj?.rightGripperOverride)
    setLeftGripper(adj?.leftGripperOverride)
  }, [currentFrame, trajectoryAdjustments])

  // Check if there are any changes
  const hasFrameChanges = useMemo(() => {
    return (
      rightArmDelta.some((v) => v !== 0) ||
      leftArmDelta.some((v) => v !== 0) ||
      rightGripper !== undefined ||
      leftGripper !== undefined
    )
  }, [rightArmDelta, leftArmDelta, rightGripper, leftGripper])

  const hasAnyAdjustments = trajectoryAdjustments.size > 0

  // Apply current changes to store
  const handleApply = useCallback(() => {
    if (!hasFrameChanges) {
      removeTrajectoryAdjustment(currentFrame)
      return
    }

    setTrajectoryAdjustment(currentFrame, {
      rightArmDelta: rightArmDelta.some((v) => v !== 0) ? rightArmDelta : undefined,
      leftArmDelta: leftArmDelta.some((v) => v !== 0) ? leftArmDelta : undefined,
      rightGripperOverride: rightGripper,
      leftGripperOverride: leftGripper,
    })
  }, [
    currentFrame,
    hasFrameChanges,
    rightArmDelta,
    leftArmDelta,
    rightGripper,
    leftGripper,
    setTrajectoryAdjustment,
    removeTrajectoryAdjustment,
  ])

  // Reset current frame
  const handleResetFrame = useCallback(() => {
    setRightArmDelta([0, 0, 0])
    setLeftArmDelta([0, 0, 0])
    setRightGripper(undefined)
    setLeftGripper(undefined)
    removeTrajectoryAdjustment(currentFrame)
  }, [currentFrame, removeTrajectoryAdjustment])

  // Reset right arm only
  const handleResetRightArm = useCallback(() => {
    setRightArmDelta([0, 0, 0])
    setRightGripper(undefined)
  }, [])

  // Reset left arm only
  const handleResetLeftArm = useCallback(() => {
    setLeftArmDelta([0, 0, 0])
    setLeftGripper(undefined)
  }, [])

  if (!currentPoint) {
    return (
      <div className={cn('text-muted-foreground p-4 text-sm', className)}>
        No trajectory data available for this frame.
      </div>
    )
  }

  return (
    <div className={cn('space-y-4', className)}>
      {/* Frame indicator */}
      <div className="flex items-center justify-between">
        <div className="text-sm">
          <span className="font-medium">Frame {currentFrame}</span>
          {currentAdjustment && (
            <span className="ml-2 text-xs text-orange-500">(has adjustments)</span>
          )}
        </div>
        <div className="text-muted-foreground text-xs">
          {trajectoryAdjustments.size} frame(s) modified
        </div>
      </div>

      {/* Right Arm Editor */}
      <ArmEditor
        title="Right Arm"
        titleColor="text-blue-600"
        currentPoint={currentPoint}
        posIndices={[0, 1, 2]}
        gripperIndex={7}
        delta={rightArmDelta.some((v) => v !== 0) ? rightArmDelta : undefined}
        gripperOverride={rightGripper}
        onDeltaChange={setRightArmDelta}
        onGripperChange={setRightGripper}
        onReset={handleResetRightArm}
      />

      {/* Left Arm Editor */}
      <ArmEditor
        title="Left Arm"
        titleColor="text-green-600"
        currentPoint={currentPoint}
        posIndices={[8, 9, 10]}
        gripperIndex={15}
        delta={leftArmDelta.some((v) => v !== 0) ? leftArmDelta : undefined}
        gripperOverride={leftGripper}
        onDeltaChange={setLeftArmDelta}
        onGripperChange={setLeftGripper}
        onReset={handleResetLeftArm}
      />

      {/* Action buttons */}
      <div className="flex gap-2 pt-2">
        <Button
          size="sm"
          onClick={handleApply}
          disabled={!hasFrameChanges && !currentAdjustment}
          className="flex-1"
        >
          <Check className="mr-1 h-4 w-4" />
          Apply to Frame {currentFrame}
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={handleResetFrame}
          disabled={!hasFrameChanges && !currentAdjustment}
        >
          <RotateCcw className="mr-1 h-4 w-4" />
          Reset Frame
        </Button>
      </div>

      {/* Clear all adjustments */}
      {hasAnyAdjustments && (
        <Button
          variant="destructive"
          size="sm"
          onClick={clearTrajectoryAdjustments}
          className="w-full"
        >
          <Trash2 className="mr-1 h-4 w-4" />
          Clear All Trajectory Adjustments ({trajectoryAdjustments.size})
        </Button>
      )}
    </div>
  )
}
