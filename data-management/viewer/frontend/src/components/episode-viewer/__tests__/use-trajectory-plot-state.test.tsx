import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/hooks/use-joint-config', () => ({
  useJointConfigDefaults: () => ({ data: undefined }),
  useSaveJointConfig: () => ({ save: vi.fn() }),
  useSaveJointConfigDefaults: () => ({ mutate: vi.fn(), isPending: false }),
}))

import { useTrajectoryPlotState } from '@/components/episode-viewer/useTrajectoryPlotState'
import { useEditStore, useEpisodeStore } from '@/stores'
import { useJointConfigStore } from '@/stores/joint-config-store'

describe('useTrajectoryPlotState', () => {
  beforeEach(() => {
    useEpisodeStore.getState().reset()
    useEditStore.getState().clear()
    useJointConfigStore.getState().reset()

    useEpisodeStore.getState().setCurrentEpisode({
      meta: { index: 0, length: 3, taskIndex: 0, hasAnnotations: false },
      videoUrls: {},
      cameras: [],
      trajectoryData: [
        {
          frame: 0,
          timestamp: 0,
          jointPositions: Array.from({ length: 17 }, (_, index) => index),
          jointVelocities: Array.from({ length: 17 }, (_, index) => index / 10),
          endEffectorPose: [],
          gripperState: 0,
        },
        {
          frame: 1,
          timestamp: 0.1,
          jointPositions: Array.from({ length: 17 }, (_, index) => index + 1),
          jointVelocities: Array.from({ length: 17 }, (_, index) => (index + 1) / 10),
          endEffectorPose: [],
          gripperState: 1,
        },
      ],
    })
  })

  it('defaults normalization on and switches to raw data when toggled off', () => {
    const { result } = renderHook(() => useTrajectoryPlotState({}))

    expect(result.current.showNormalized).toBe(true)
    expect(result.current.chartData[0]?.joint_1).toBe(0)

    act(() => {
      result.current.toggleNormalization()
    })

    expect(result.current.showNormalized).toBe(false)
    expect(result.current.chartData[0]?.joint_1).toBe(1)
  })

  it('disables normalization toggling while velocity mode is active', () => {
    const { result } = renderHook(() => useTrajectoryPlotState({}))

    act(() => {
      result.current.setShowVelocity(true)
    })

    expect(result.current.isNormalizationDisabled).toBe(true)
    expect(result.current.chartData[0]?.joint_1).toBe(0.1)
  })

  it('removes state and action prefixes from named trajectory labels', () => {
    useEpisodeStore.getState().setCurrentEpisode({
      meta: { index: 0, length: 1, taskIndex: 0, hasAnnotations: false },
      videoUrls: {},
      cameras: [],
      trajectoryVariables: [
        {
          key: 'observation.state[0]',
          label: 'State: shoulder_pan_joint',
          source: 'observation.state',
          kind: 'state',
        },
        {
          key: 'action[0]',
          label: 'Action: target_shoulder_pan_joint',
          source: 'action',
          kind: 'action',
        },
      ],
      trajectoryData: [
        {
          frame: 0,
          timestamp: 0,
          jointPositions: [0],
          jointVelocities: [0],
          endEffectorPose: [],
          gripperState: 0,
          variables: {
            'observation.state[0]': 0,
            'action[0]': 1,
          },
        },
      ],
    })

    const { result } = renderHook(() => useTrajectoryPlotState({}))

    expect(result.current.resolveLabel(0)).toBe('shoulder_pan_joint')
    expect(result.current.resolveLabel(1)).toBe('target_shoulder_pan_joint')
  })

  it('seeks to the frame carried by a chart click payload', () => {
    const onSeekFrame = vi.fn()
    const { result } = renderHook(() => useTrajectoryPlotState({ onSeekFrame }))

    act(() => {
      result.current.handleChartClick({ activePayload: [{ payload: { frame: 2 } }] })
    })

    expect(useEpisodeStore.getState().currentFrame).toBe(2)
    expect(onSeekFrame).toHaveBeenCalledWith(2)
  })

  it('ignores chart clicks without a frame payload', () => {
    const onSeekFrame = vi.fn()
    const { result } = renderHook(() => useTrajectoryPlotState({ onSeekFrame }))

    act(() => {
      result.current.handleChartClick({})
    })

    expect(onSeekFrame).not.toHaveBeenCalled()
  })

  it('does not toggle normalization while velocity mode is active', () => {
    const { result } = renderHook(() => useTrajectoryPlotState({}))

    act(() => {
      result.current.setShowVelocity(true)
    })
    const before = result.current.showNormalized

    act(() => {
      result.current.toggleNormalization()
    })

    expect(result.current.showNormalized).toBe(before)
  })

  it('exposes joint data keys and editable labels for unnamed trajectories', () => {
    const { result } = renderHook(() => useTrajectoryPlotState({}))

    expect(result.current.resolveDataKey(0)).toBe('joint_0')
    expect(result.current.variablesEditable).toBe(true)
    expect(result.current.variableLabels).toBe(result.current.jointConfig.labels)
  })

  it('switches to series data keys and locks labels for named trajectories', () => {
    useEpisodeStore.getState().setCurrentEpisode({
      meta: { index: 0, length: 1, taskIndex: 0, hasAnnotations: false },
      videoUrls: {},
      cameras: [],
      trajectoryVariables: [
        {
          key: 'observation.state[0]',
          label: 'State: a',
          source: 'observation.state',
          kind: 'state',
        },
      ],
      trajectoryData: [
        {
          frame: 0,
          timestamp: 0,
          jointPositions: [0],
          jointVelocities: [0],
          endEffectorPose: [],
          gripperState: 0,
          variables: { 'observation.state[0]': 0 },
        },
      ],
    })

    const { result } = renderHook(() => useTrajectoryPlotState({}))

    expect(result.current.resolveDataKey(0)).toBe('series_0')
    expect(result.current.variablesEditable).toBe(false)
    expect(result.current.variableGroups.length).toBeGreaterThan(0)
  })

  it('runs the wrapped action when invoking a save-bound handler', () => {
    const { result } = renderHook(() => useTrajectoryPlotState({}))
    const action = vi.fn()

    act(() => {
      result.current.withSave(action)('payload')
    })

    expect(action).toHaveBeenCalledWith('payload')
  })

  it('returns no selection highlight without a selected range', () => {
    const { result } = renderHook(() => useTrajectoryPlotState({ selectedRange: null }))

    expect(result.current.selectionHighlight).toBeNull()
  })

  it('ranks the most-varying series when there are more than sixteen variables', () => {
    const variableCount = 18
    const trajectoryVariables = Array.from({ length: variableCount }, (_, index) => ({
      key: `observation.state[${index}]`,
      label: `State: joint_${index}`,
      source: 'observation.state',
      kind: 'state' as const,
    }))
    const makeFrame = (frame: number, scale: number) => ({
      frame,
      timestamp: frame,
      jointPositions: [],
      jointVelocities: [],
      endEffectorPose: [],
      gripperState: 0,
      // Higher-index variables vary more, so they should be ranked in.
      variables: Object.fromEntries(
        trajectoryVariables.map((variable, index) => [variable.key, frame * index * scale]),
      ),
    })

    useEpisodeStore.getState().setCurrentEpisode({
      meta: { index: 0, length: 3, taskIndex: 0, hasAnnotations: false },
      videoUrls: {},
      cameras: [],
      trajectoryVariables,
      trajectoryData: [makeFrame(0, 1), makeFrame(1, 1), makeFrame(2, 1)],
    })

    const { result } = renderHook(() => useTrajectoryPlotState({}))

    expect(result.current.jointCount).toBe(variableCount)
    // More than sixteen variables triggers the rank-and-trim path (top 12).
    expect(result.current.selectedJoints).toHaveLength(12)
    expect(
      result.current.selectedJoints.every((index) => index >= 0 && index < variableCount),
    ).toBe(true)
  })
})
