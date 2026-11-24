import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'

import { useEditStore, useEpisodeStore } from '@/stores'

import { TrajectoryEditor } from '../TrajectoryEditor'

const FRAMES = 4

beforeEach(() => {
  useEpisodeStore.getState().reset()
  useEditStore.getState().clear()
  useEpisodeStore.getState().setCurrentEpisode({
    meta: { index: 0, length: FRAMES, taskIndex: 0, hasAnnotations: false },
    videoUrls: {},
    cameras: [],
    trajectoryData: Array.from({ length: FRAMES }, (_, frame) => ({
      frame,
      timestamp: frame / 10,
      jointPositions: [frame, frame + 0.5],
      jointVelocities: [0, 0],
      endEffectorPose: [],
      gripperState: 0,
    })),
  })
  useEditStore.getState().initializeEdit('ds', 0)
})

describe('TrajectoryEditor export request', () => {
  it.each([
    ['a removal', () => useEditStore.getState().toggleFrameRemoval(0), 1, 2],
    ['an insertion', () => useEditStore.getState().insertFrame(0, 0.5), 2, 1],
  ])(
    'keys an adjustment made after %s by its recorded frame',
    (_edit, applyEdit, playbackPosition, recordedFrame) => {
      applyEdit()
      useEpisodeStore.getState().setCurrentFrame(playbackPosition)
      render(<TrajectoryEditor />)

      fireEvent.change(screen.getAllByRole('slider')[0], { target: { value: '0.25' } })
      fireEvent.click(screen.getByRole('button', { name: `Apply to Frame ${recordedFrame}` }))

      expect(useEditStore.getState().getEditOperations()?.trajectoryAdjustments).toEqual([
        { frameIndex: recordedFrame, channelDeltas: { 0: 0.25 }, channelValues: undefined },
      ])
    },
  )
})
