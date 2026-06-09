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
})
