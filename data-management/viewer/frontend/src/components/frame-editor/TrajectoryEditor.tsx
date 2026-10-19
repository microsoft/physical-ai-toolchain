/**
 * Trajectory editor for adjusting the state vector at each frame.
 *
 * Each state channel takes an additive delta or a set value. Adjustments are stored
 * non-destructively in the edit store, keyed by recorded frame, and preview on the trajectory plot.
 * Exports keep the recorded positions and add the adjustments beside them with a mask of the edited
 * rows: qpos_adjusted in HDF5 exports and adjusted.observation.state in LeRobot exports.
 */

import { Check, RotateCcw, Trash2 } from 'lucide-react'
import { useCallback, useMemo, useRef, useState } from 'react'
import { flushSync } from 'react-dom'

import { resolveStateChannelLabel } from '@/components/episode-viewer/joint-constants'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { cn } from '@/lib/utils'
import {
  useEditStore,
  useEpisodeStore,
  usePlaybackControls,
  useTrajectoryAdjustmentState,
} from '@/stores'
import { getOriginalIndex } from '@/stores/edit-store-frame-utils'
import { useJointConfigStore } from '@/stores/joint-config-store'

interface TrajectoryEditorProps {
  /** Additional CSS classes */
  className?: string
}

type ChannelRecord = Record<number, number>

function withoutChannel(record: ChannelRecord, index: number): ChannelRecord {
  return Object.fromEntries(Object.entries(record).filter(([key]) => Number(key) !== index))
}

interface ChannelEditorProps {
  label: string
  value: number
  delta: number
  setValue: number | undefined
  onDeltaChange: (delta: number) => void
  onSetValueChange: (value: number | undefined) => void
  onReset: () => void
}

/**
 * One state channel: a delta slider and input, a set-value input, and a reset.
 */
function ChannelEditor({
  label,
  value,
  delta,
  setValue,
  onDeltaChange,
  onSetValueChange,
  onReset,
}: ChannelEditorProps) {
  const sliderRef = useRef<HTMLInputElement>(null)
  const isSet = setValue !== undefined
  const hasChange = delta !== 0 || isSet
  // Local text state allows partial typing such as "-" or "0." before a value parses.
  const [deltaText, setDeltaText] = useState(delta.toFixed(4))
  const [valueText, setValueText] = useState(isSet ? setValue.toFixed(4) : '')
  // Re-sync the text only when the parsed value changes from outside the input.
  const [syncedDelta, setSyncedDelta] = useState(delta)
  const [syncedValue, setSyncedValue] = useState(setValue)
  if (delta !== syncedDelta) {
    setSyncedDelta(delta)
    setDeltaText(delta.toFixed(4))
  }
  if (setValue !== syncedValue) {
    setSyncedValue(setValue)
    setValueText(setValue === undefined ? '' : setValue.toFixed(4))
  }

  const handleDeltaText = useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      setDeltaText(event.target.value)
      const parsed = parseFloat(event.target.value)
      if (!Number.isNaN(parsed)) {
        onDeltaChange(parsed)
      }
    },
    [onDeltaChange],
  )

  const handleValueText = useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      setValueText(event.target.value)
      if (event.target.value.trim() === '') {
        onSetValueChange(undefined)
        return
      }
      const parsed = parseFloat(event.target.value)
      if (!Number.isNaN(parsed)) {
        onSetValueChange(parsed)
      }
    },
    [onSetValueChange],
  )

  return (
    <div className="space-y-1 border-b pb-2 last:border-b-0">
      <div className="flex items-center justify-between gap-2">
        <Label className="truncate text-xs font-medium">{label}</Label>
        <div className="flex items-center gap-1">
          <span className="text-muted-foreground font-mono text-xs">
            {(isSet ? setValue : value + delta).toFixed(4)}
          </span>
          {hasChange && (
            <Button
              variant="ghost"
              size="sm"
              className="h-6 w-6 p-0"
              aria-label={`Reset ${label}`}
              onClick={() => {
                // The reset button goes away with the change, so focus moves to the enabled slider.
                flushSync(onReset)
                sliderRef.current?.focus()
              }}
            >
              <RotateCcw className="h-3 w-3" />
            </Button>
          )}
        </div>
      </div>
      <input
        ref={sliderRef}
        type="range"
        aria-label={`${label} delta`}
        value={delta}
        onChange={(event) => onDeltaChange(parseFloat(event.target.value))}
        min={-0.5}
        max={0.5}
        step={0.001}
        disabled={isSet}
        className="accent-primary h-2 w-full"
      />
      <div className="grid grid-cols-2 gap-2">
        <div className="flex items-center gap-1 text-xs">
          <span aria-hidden="true" className="text-muted-foreground">
            Δ
          </span>
          <Input
            type="number"
            aria-label={`${label} delta`}
            value={deltaText}
            onChange={handleDeltaText}
            onBlur={() => setDeltaText(delta.toFixed(4))}
            step={0.001}
            disabled={isSet}
            className="h-7 min-w-0 px-2 font-mono text-xs md:text-xs"
          />
        </div>
        <div className="flex items-center gap-1 text-xs">
          <span aria-hidden="true" className="text-muted-foreground">
            Set
          </span>
          <Input
            type="number"
            aria-label={`${label} set value`}
            value={valueText}
            onChange={handleValueText}
            onBlur={() => setValueText(setValue === undefined ? '' : setValue.toFixed(4))}
            step={0.001}
            className="h-7 min-w-0 px-2 font-mono text-xs md:text-xs"
          />
        </div>
      </div>
      {hasChange && (
        <div className="text-muted-foreground text-xs">
          Original: {value.toFixed(4)} →{' '}
          {isSet ? `set ${setValue.toFixed(4)}` : `Δ: ${delta >= 0 ? '+' : ''}${delta.toFixed(4)}`}
        </div>
      )}
    </div>
  )
}

/**
 * Trajectory editor component for adjusting the state channels at the current frame.
 *
 * @example
 * ```tsx
 * <TrajectoryEditor className="mt-4" />
 * ```
 */
export function TrajectoryEditor({ className }: TrajectoryEditorProps) {
  const { currentFrame } = usePlaybackControls()
  const insertedFrames = useEditStore((state) => state.insertedFrames)
  const removedFrames = useEditStore((state) => state.removedFrames)
  // Remount per episode+frame so the editor inputs always start from that frame's
  // stored adjustment instead of retaining the previously edited frame's values.
  const episodeKey = useEpisodeStore(
    (state) => state.currentEpisode?.meta?.id ?? state.currentEpisode?.meta?.index,
  )
  // Adjustments apply to recorded frames, so the playback position maps back through the frame edits.
  const recordedFrame = getOriginalIndex(currentFrame, insertedFrames, removedFrames)

  if (recordedFrame === null) {
    return (
      <p className={cn('text-muted-foreground p-4 text-sm', className)}>
        This frame is inserted. It takes its values from the recorded frames on either side, so
        adjust those instead.
      </p>
    )
  }

  return (
    <TrajectoryEditorFrame
      key={`${episodeKey}:${recordedFrame}`}
      className={className}
      recordedFrame={recordedFrame}
      playbackFrame={currentFrame}
    />
  )
}

interface TrajectoryEditorFrameProps extends TrajectoryEditorProps {
  recordedFrame: number
  playbackFrame: number
}

function TrajectoryEditorFrame({
  className,
  recordedFrame,
  playbackFrame,
}: TrajectoryEditorFrameProps) {
  const currentEpisode = useEpisodeStore((state) => state.currentEpisode)
  const jointConfigLabels = useJointConfigStore((state) => state.config.labels)
  const {
    trajectoryAdjustments,
    setTrajectoryAdjustment,
    removeTrajectoryAdjustment,
    clearTrajectoryAdjustments,
  } = useTrajectoryAdjustmentState()
  const groupRef = useRef<HTMLDivElement>(null)
  const [status, setStatus] = useState('')

  // Report an action's result, and keep keyboard focus in the editor when the action disables or
  // removes the focused button.
  const announce = useCallback((message: string, refocus: boolean) => {
    setStatus(message)
    if (refocus) {
      groupRef.current?.focus()
    }
  }, [])

  const currentPoint = useMemo(() => {
    const trajectoryData = currentEpisode?.trajectoryData || []
    if (trajectoryData.length === 0) return null
    return trajectoryData[Math.min(recordedFrame, trajectoryData.length - 1)]
  }, [currentEpisode?.trajectoryData, recordedFrame])

  const stateVariables = useMemo(
    () =>
      (currentEpisode?.trajectoryVariables ?? []).filter((variable) => variable.kind === 'state'),
    [currentEpisode?.trajectoryVariables],
  )

  // Channel position is the identity of a state channel; named datasets also give it a stable variable key.
  const channels = useMemo(
    () =>
      (currentPoint?.jointPositions ?? []).map((value, index) => ({
        id: stateVariables[index]?.key ?? `channel-${index}`,
        index,
        value,
        label: resolveStateChannelLabel(index, stateVariables, jointConfigLabels),
      })),
    [currentPoint, jointConfigLabels, stateVariables],
  )

  const currentAdjustment = trajectoryAdjustments.get(recordedFrame)
  const [deltas, setDeltas] = useState<ChannelRecord>(() => ({
    ...currentAdjustment?.channelDeltas,
  }))
  const [values, setValues] = useState<ChannelRecord>(() => ({
    ...currentAdjustment?.channelValues,
  }))

  const changedDeltas = Object.entries(deltas).filter(([, delta]) => delta !== 0)
  const hasFrameChanges = changedDeltas.length > 0 || Object.keys(values).length > 0
  const hasAnyAdjustments = trajectoryAdjustments.size > 0

  const setChannelDelta = useCallback((index: number, delta: number) => {
    setDeltas((current) => ({ ...current, [index]: delta }))
  }, [])

  const setChannelValue = useCallback((index: number, value: number | undefined) => {
    if (value === undefined) {
      setValues((current) => withoutChannel(current, index))
      return
    }
    // A set value replaces the channel's delta, so the delta is cleared rather than kept hidden.
    setValues((current) => ({ ...current, [index]: value }))
    setDeltas((current) => withoutChannel(current, index))
  }, [])

  const resetChannel = useCallback((index: number, label: string) => {
    setDeltas((current) => withoutChannel(current, index))
    setValues((current) => withoutChannel(current, index))
    setStatus(`Reset ${label}`)
  }, [])

  const handleApply = useCallback(() => {
    if (!hasFrameChanges) {
      removeTrajectoryAdjustment(recordedFrame)
      announce(`Removed the adjustment from frame ${recordedFrame}`, true)
      return
    }

    setTrajectoryAdjustment(recordedFrame, {
      channelDeltas: changedDeltas.length > 0 ? Object.fromEntries(changedDeltas) : undefined,
      channelValues: Object.keys(values).length > 0 ? { ...values } : undefined,
    })
    announce(`Adjusted frame ${recordedFrame}`, false)
  }, [
    announce,
    changedDeltas,
    recordedFrame,
    hasFrameChanges,
    removeTrajectoryAdjustment,
    setTrajectoryAdjustment,
    values,
  ])

  const handleResetFrame = useCallback(() => {
    setDeltas({})
    setValues({})
    removeTrajectoryAdjustment(recordedFrame)
    announce(`Reset frame ${recordedFrame}`, true)
  }, [announce, recordedFrame, removeTrajectoryAdjustment])

  // Clearing every stored adjustment must also drop this frame's pending edits.
  const handleClearAll = useCallback(() => {
    setDeltas({})
    setValues({})
    clearTrajectoryAdjustments()
    announce('Cleared all trajectory adjustments', true)
  }, [announce, clearTrajectoryAdjustments])

  if (!currentPoint) {
    return (
      <div className={cn('text-muted-foreground p-4 text-sm', className)}>
        No trajectory data available for this frame.
      </div>
    )
  }

  return (
    <div
      ref={groupRef}
      role="group"
      aria-label={`Frame ${recordedFrame} trajectory adjustments`}
      tabIndex={-1}
      className={cn(
        'focus-visible:ring-ring space-y-4 rounded-md focus-visible:ring-1 focus-visible:outline-hidden',
        className,
      )}
    >
      {/* Frame indicator */}
      <div className="flex items-center justify-between">
        <div className="text-sm">
          <span className="font-medium">Frame {recordedFrame}</span>
          {currentAdjustment && (
            <span className="ml-2 text-xs text-orange-500">(has adjustments)</span>
          )}
        </div>
        <div className="text-muted-foreground text-xs">
          {trajectoryAdjustments.size} frame(s) modified
        </div>
      </div>

      {playbackFrame !== recordedFrame && (
        <p className="text-muted-foreground text-xs">
          Playback position {playbackFrame} shows recorded frame {recordedFrame}.
        </p>
      )}

      <p role="note" className="text-muted-foreground text-xs">
        Exports keep the recorded joint positions and add these adjustments beside them, as
        qpos_adjusted in HDF5 or adjusted.observation.state in LeRobot, with a mask of the edited
        rows.
      </p>

      <div className="bg-muted/50 max-h-96 space-y-2 overflow-y-auto rounded-lg p-3">
        {channels.map((channel) => (
          <ChannelEditor
            key={channel.id}
            label={channel.label}
            value={channel.value}
            delta={deltas[channel.index] ?? 0}
            setValue={values[channel.index]}
            onDeltaChange={(delta) => setChannelDelta(channel.index, delta)}
            onSetValueChange={(next) => setChannelValue(channel.index, next)}
            onReset={() => resetChannel(channel.index, channel.label)}
          />
        ))}
      </div>

      {/* Action buttons */}
      <div className="flex gap-2 pt-2">
        <Button
          size="sm"
          onClick={handleApply}
          disabled={!hasFrameChanges && !currentAdjustment}
          className="flex-1"
        >
          <Check className="mr-1 h-4 w-4" />
          Apply to Frame {recordedFrame}
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

      <p role="status" aria-live="polite" className="sr-only">
        {status}
      </p>

      {/* Clear all adjustments */}
      {hasAnyAdjustments && (
        <Button variant="destructive" size="sm" onClick={handleClearAll} className="w-full">
          <Trash2 className="mr-1 h-4 w-4" />
          Clear All Trajectory Adjustments ({trajectoryAdjustments.size})
        </Button>
      )}
    </div>
  )
}
