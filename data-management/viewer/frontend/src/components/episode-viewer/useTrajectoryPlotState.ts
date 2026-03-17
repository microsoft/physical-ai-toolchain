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

import { getJointLabel } from './joint-constants'
import { buildTrajectoryChartData } from './trajectory-plot-utils'
import { useTrajectoryPlotSelection } from './useTrajectoryPlotSelection'

interface UseTrajectoryPlotStateOptions {
  onSaved?: () => void
  selectedRange?: [number, number] | null
  onSelectedRangeChange?: (range: [number, number] | null) => void
  onCreateSubtaskFromRange?: (range: [number, number]) => void
  onSelectionStart?: () => void
  onSelectionComplete?: (range: [number, number]) => void
  onSeekFrame?: (frame: number) => void
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

  const withSave = useCallback(
    <T extends unknown[]>(fn: (...args: T) => void) =>
      (...args: T) => {
        fn(...args)
        queueMicrotask(() => saveJointConfig(onSaved))
      },
    [onSaved, saveJointConfig],
  )

  const resolveLabel = useCallback(
    (idx: number) => jointConfig.labels[String(idx)] ?? getJointLabel(idx),
    [jointConfig.labels],
  )

  const chartData = useMemo(() => {
    if (!currentEpisode?.trajectoryData) {
      return []
    }

    return buildTrajectoryChartData({
      trajectoryData: currentEpisode.trajectoryData,
      trajectoryAdjustments,
      showVelocity,
      showNormalized,
    })
  }, [currentEpisode?.trajectoryData, showNormalized, showVelocity, trajectoryAdjustments])

  const jointCount = useMemo(() => {
    if (!currentEpisode?.trajectoryData?.[0]) {
      return 0
    }
    return currentEpisode.trajectoryData[0].jointPositions.length
  }, [currentEpisode?.trajectoryData])

  const autoSelectedJoints = useMemo(
    () =>
      getAutoSelectedJointsForEpisode(
        currentEpisode?.trajectoryData ?? [],
        jointConfig.groups,
        jointCount,
      ),
    [currentEpisode?.trajectoryData, jointConfig.groups, jointCount],
  )

  useEffect(() => {
    setSelectedJoints(autoSelectedJoints)
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
    withSave,
    isNormalizationDisabled: showVelocity,
  }
}
