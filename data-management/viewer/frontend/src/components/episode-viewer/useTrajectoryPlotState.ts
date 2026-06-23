import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import {
  useJointConfigDefaults,
  useSaveJointConfig,
  useSaveJointConfigDefaults,
} from '@/hooks/use-joint-config'
import { getAutoSelectedJointsForEpisode } from '@/lib/joint-significance'
import { recordDiagnosticEvent } from '@/lib/playback-diagnostics'
import {
  resolveSelectionHighlightStyle,
  resolveSurfaceFrame,
  resolveTrajectoryPlotArea,
  type TrajectoryPlotArea,
} from '@/lib/trajectory-graph-geometry'
import { useEpisodeStore, useTrajectoryAdjustmentState } from '@/stores'
import { useJointConfigStore } from '@/stores/joint-config-store'
import type { TrajectoryVariable } from '@/types'

import { getJointLabel, type JointGroup } from './joint-constants'
import { buildTrajectoryChartData } from './trajectory-plot-utils'
import { useTrajectoryPlotSelection } from './useTrajectoryPlotSelection'

// Stable empty fallback so an episode without named variables does not allocate
// a fresh array every render (which would churn every dependent memo/effect).
const EMPTY_TRAJECTORY_VARIABLES: TrajectoryVariable[] = []

function sameNumberArray(a: readonly number[], b: readonly number[]): boolean {
  return a.length === b.length && a.every((value, index) => value === b[index])
}

interface UseTrajectoryPlotStateOptions {
  onSaved?: () => void
  selectedRange?: [number, number] | null
  onSelectedRangeChange?: (range: [number, number] | null) => void
  onCreateSubtaskFromRange?: (range: [number, number]) => void
  onSelectionStart?: () => void
  onSelectionComplete?: (range: [number, number]) => void
  onSeekFrame?: (frame: number) => void
}

function humanizeSource(source: string) {
  return source
    .replace(/^observation\./, '')
    .replace(/[._-]+/g, ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase())
}

function removeVariableLabelPrefix(label: string) {
  return label.replace(/^(State|Action):\s+/i, '')
}

function buildVariableGroups(variables: readonly TrajectoryVariable[]): JointGroup[] {
  const groups = new Map<string, JointGroup>()

  variables.forEach((variable, index) => {
    const id = `${variable.kind}:${variable.source}`
    const existing = groups.get(id)
    if (existing) {
      existing.indices.push(index)
      return
    }

    groups.set(id, {
      id,
      label: humanizeSource(variable.source),
      indices: [index],
    })
  })

  return Array.from(groups.values())
}

function resolveNamedVariableSelection(
  chartData: Array<Record<string, number | boolean>>,
  variableCount: number,
) {
  if (variableCount <= 16) {
    return Array.from({ length: variableCount }, (_, index) => index)
  }

  return Array.from({ length: variableCount }, (_, index) => {
    const values = chartData
      .map((point) => point[`series_${index}`])
      .filter((value): value is number => typeof value === 'number')

    return {
      index,
      range: values.length ? Math.max(...values) - Math.min(...values) : 0,
    }
  })
    .sort((left, right) => right.range - left.range)
    .slice(0, 12)
    .map((item) => item.index)
    .sort((left, right) => left - right)
}

export function useTrajectoryPlotState({
  onSaved,
  selectedRange = null,
  onSelectedRangeChange,
  onCreateSubtaskFromRange,
  onSelectionStart,
  onSelectionComplete,
  onSeekFrame,
}: UseTrajectoryPlotStateOptions) {
  const currentEpisode = useEpisodeStore((state) => state.currentEpisode)
  const currentFrame = useEpisodeStore((state) => state.currentFrame)
  const setCurrentFrame = useEpisodeStore((state) => state.setCurrentFrame)
  const { trajectoryAdjustments } = useTrajectoryAdjustmentState()
  const jointConfig = useJointConfigStore((state) => state.config)
  const updateLabel = useJointConfigStore((state) => state.updateLabel)
  const updateGroupLabel = useJointConfigStore((state) => state.updateGroupLabel)
  const createGroup = useJointConfigStore((state) => state.createGroup)
  const deleteGroup = useJointConfigStore((state) => state.deleteGroup)
  const moveJoint = useJointConfigStore((state) => state.moveJoint)
  const { save: saveJointConfig } = useSaveJointConfig()
  const { data: defaults } = useJointConfigDefaults()
  const saveDefaults = useSaveJointConfigDefaults()

  const [selectedJoints, setSelectedJoints] = useState<number[]>([])
  const [showVelocity, setShowVelocity] = useState(false)
  const [showNormalized, setShowNormalized] = useState(true)
  const [defaultsOpen, setDefaultsOpen] = useState(false)
  const [plotArea, setPlotArea] = useState<TrajectoryPlotArea | null>(null)
  const selectionSurfaceRef = useRef<HTMLDivElement>(null)
  const namedTrajectoryVariables = currentEpisode?.trajectoryVariables ?? EMPTY_TRAJECTORY_VARIABLES
  const shouldUseNamedVariables = !showVelocity && namedTrajectoryVariables.length > 0
  const stateVariables = useMemo(
    () => namedTrajectoryVariables.filter((variable) => variable.kind === 'state'),
    [namedTrajectoryVariables],
  )

  const withSave = useCallback(
    <T extends unknown[]>(fn: (...args: T) => void) =>
      (...args: T) => {
        fn(...args)
        queueMicrotask(() => saveJointConfig(onSaved))
      },
    [onSaved, saveJointConfig],
  )

  const resolveLabel = useCallback(
    (idx: number) => {
      if (shouldUseNamedVariables) {
        return removeVariableLabelPrefix(namedTrajectoryVariables[idx]?.label ?? getJointLabel(idx))
      }
      if (showVelocity && stateVariables[idx]) {
        return removeVariableLabelPrefix(stateVariables[idx].label)
      }
      return jointConfig.labels[String(idx)] ?? getJointLabel(idx)
    },
    [
      jointConfig.labels,
      namedTrajectoryVariables,
      shouldUseNamedVariables,
      showVelocity,
      stateVariables,
    ],
  )

  const resolveDataKey = useCallback(
    (idx: number) => (shouldUseNamedVariables ? `series_${idx}` : `joint_${idx}`),
    [shouldUseNamedVariables],
  )

  const variableLabels = useMemo(() => {
    if (shouldUseNamedVariables) {
      return Object.fromEntries(
        Array.from({ length: namedTrajectoryVariables.length }, (_, index) => [
          String(index),
          resolveLabel(index),
        ]),
      )
    }
    if (showVelocity && stateVariables.length > 0) {
      return Object.fromEntries(
        stateVariables.map((variable, index) => [
          String(index),
          removeVariableLabelPrefix(variable.label),
        ]),
      )
    }
    return {}
  }, [
    namedTrajectoryVariables.length,
    resolveLabel,
    shouldUseNamedVariables,
    showVelocity,
    stateVariables,
  ])

  const variableGroups = useMemo(() => {
    if (shouldUseNamedVariables) {
      return buildVariableGroups(namedTrajectoryVariables)
    }
    if (showVelocity && stateVariables.length > 0) {
      return buildVariableGroups(stateVariables)
    }
    return jointConfig.groups
  }, [
    jointConfig.groups,
    namedTrajectoryVariables,
    shouldUseNamedVariables,
    showVelocity,
    stateVariables,
  ])

  const chartData = useMemo(() => {
    if (!currentEpisode?.trajectoryData) {
      return []
    }

    return buildTrajectoryChartData({
      trajectoryData: currentEpisode.trajectoryData,
      trajectoryAdjustments,
      trajectoryVariables: currentEpisode.trajectoryVariables,
      showVelocity,
      showNormalized,
    })
  }, [currentEpisode?.trajectoryData, showNormalized, showVelocity, trajectoryAdjustments])

  const jointCount = useMemo(() => {
    if (!currentEpisode?.trajectoryData?.[0]) {
      return 0
    }
    if (shouldUseNamedVariables) {
      return namedTrajectoryVariables.length
    }
    return showVelocity
      ? currentEpisode.trajectoryData[0].jointVelocities.length
      : currentEpisode.trajectoryData[0].jointPositions.length
  }, [
    currentEpisode?.trajectoryData,
    namedTrajectoryVariables.length,
    shouldUseNamedVariables,
    showVelocity,
  ])

  const autoSelectedJoints = useMemo(() => {
    if (shouldUseNamedVariables) {
      return resolveNamedVariableSelection(chartData, jointCount)
    }

    const groups =
      showVelocity && stateVariables.length > 0
        ? buildVariableGroups(stateVariables)
        : jointConfig.groups

    return getAutoSelectedJointsForEpisode(currentEpisode?.trajectoryData ?? [], groups, jointCount)
  }, [
    chartData,
    currentEpisode?.trajectoryData,
    jointConfig.groups,
    jointCount,
    shouldUseNamedVariables,
    showVelocity,
    stateVariables,
  ])

  useEffect(() => {
    // Only update when the auto-selection actually changes. ``autoSelectedJoints``
    // is a freshly-derived array each render, so assigning it unconditionally
    // would set new state every render and cascade into an infinite re-render loop.
    setSelectedJoints((prev) =>
      sameNumberArray(prev, autoSelectedJoints) ? prev : autoSelectedJoints,
    )
  }, [autoSelectedJoints])

  useEffect(() => {
    const surface = selectionSurfaceRef.current
    if (!surface) {
      return
    }

    const measurePlotArea = () => {
      setPlotArea(resolveTrajectoryPlotArea(surface))
    }

    measurePlotArea()

    const observer = new ResizeObserver(() => {
      measurePlotArea()
    })

    observer.observe(surface)

    const svg = surface.parentElement?.querySelector('svg')
    if (svg instanceof Element) {
      observer.observe(svg)
    }

    window.addEventListener('resize', measurePlotArea)
    return () => {
      observer.disconnect()
      window.removeEventListener('resize', measurePlotArea)
    }
  }, [
    chartData.length,
    currentEpisode?.meta.length,
    selectedJoints.length,
    showNormalized,
    showVelocity,
  ])

  const frameFromClientX = useCallback(
    (clientX: number) => {
      const bounds = selectionSurfaceRef.current?.getBoundingClientRect()

      if (!bounds || bounds.width <= 0) {
        return 0
      }

      return resolveSurfaceFrame(
        clientX - bounds.left,
        currentEpisode?.meta.length ?? 1,
        plotArea ?? resolveTrajectoryPlotArea(selectionSurfaceRef.current),
      )
    },
    [currentEpisode?.meta.length, plotArea],
  )

  const selection = useTrajectoryPlotSelection({
    currentEpisodeLength: currentEpisode?.meta.length ?? 0,
    frameFromClientX,
    getSurfaceBounds: () => selectionSurfaceRef.current?.getBoundingClientRect() ?? null,
    selectedRange,
    onSelectedRangeChange,
    onSelectionStart,
    onSelectionComplete,
    onSeekFrame,
    onSetCurrentFrame: setCurrentFrame,
    onRecordEvent: recordDiagnosticEvent,
  })

  const selectionHighlight = useMemo(() => {
    if (!selectedRange || (currentEpisode?.meta.length ?? 0) <= 1) {
      return null
    }

    const highlight = resolveSelectionHighlightStyle(
      selectedRange,
      currentEpisode?.meta.length ?? 0,
      plotArea,
    )

    if (!highlight) {
      return null
    }

    return {
      left: `${highlight.left}px`,
      width: `${highlight.width}px`,
    }
  }, [currentEpisode?.meta.length, plotArea, selectedRange])

  const handleChartClick = useCallback(
    (data: unknown) => {
      const chartData = data as { activePayload?: { payload?: { frame: number } }[] }
      if (chartData?.activePayload?.[0]?.payload?.frame !== undefined) {
        const frame = chartData.activePayload[0].payload.frame

        setCurrentFrame(frame)
        onSeekFrame?.(frame)
      }
    },
    [onSeekFrame, setCurrentFrame],
  )

  const toggleNormalization = useCallback(() => {
    if (showVelocity) {
      return
    }

    setShowNormalized((current) => !current)
  }, [showVelocity])

  return {
    chartData,
    createGroup,
    currentFrame,
    currentEpisode,
    defaults,
    defaultsOpen,
    deleteGroup,
    handleChartClick,
    jointConfig,
    jointCount,
    moveJoint,
    onCreateSubtaskFromRange,
    plotArea,
    resolveLabel,
    resolveDataKey,
    saveDefaults,
    selectedJoints,
    selection,
    selectionHighlight,
    selectionSurfaceRef,
    setDefaultsOpen,
    setSelectedJoints,
    setShowVelocity,
    showNormalized,
    showVelocity,
    toggleNormalization,
    trajectoryAdjustments,
    updateGroupLabel,
    updateLabel,
    variableGroups,
    variableLabels:
      shouldUseNamedVariables || (showVelocity && stateVariables.length > 0)
        ? variableLabels
        : jointConfig.labels,
    variablesEditable: !shouldUseNamedVariables && !(showVelocity && stateVariables.length > 0),
    withSave,
    isNormalizationDisabled: showVelocity,
  }
}
