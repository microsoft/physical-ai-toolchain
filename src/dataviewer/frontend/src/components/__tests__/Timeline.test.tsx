import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { Timeline } from '@/components/episode-viewer/Timeline'
import { useEditStore, useEpisodeStore } from '@/stores'

const EPISODE_LENGTH = 100

function seedEpisode() {
  useEpisodeStore.getState().setCurrentEpisode({
    meta: { index: 0, length: EPISODE_LENGTH, taskIndex: 0, hasAnnotations: false },
    videoUrls: {},
    cameras: [],
    trajectoryData: [],
  })
}

afterEach(cleanup)

beforeEach(() => {
  useEpisodeStore.getState().reset()
  useEditStore.getState().clear()
})

describe('Timeline', () => {
  it('renders frame labels when an episode is loaded', () => {
    seedEpisode()
    render(<Timeline />)
    expect(screen.getByText('0')).toBeInTheDocument()
    expect(screen.getByText(String(EPISODE_LENGTH))).toBeInTheDocument()
  })

  it('does not render frame insertion markers', () => {
    seedEpisode()
    const { container } = render(<Timeline />)
    const insertionMarkers = container.querySelectorAll('[data-frame-index]')
    expect(insertionMarkers).toHaveLength(0)
  })

  it('clicking the timeline bar seeks without inserting frames', () => {
    seedEpisode()
    useEpisodeStore.getState().setCurrentFrame(0)
    const { container } = render(<Timeline />)
    const slider = container.querySelector('[role="slider"]')!

    // Simulate getBoundingClientRect for position calculation
    Object.defineProperty(slider, 'getBoundingClientRect', {
      value: () => ({ left: 0, width: 1000, top: 0, bottom: 32, height: 32, right: 1000, x: 0, y: 0, toJSON: () => ({}) }),
    })

    fireEvent.click(slider, { clientX: 500 })

    // Frame should have changed (seek worked)
    expect(useEpisodeStore.getState().currentFrame).toBeGreaterThan(0)
    // No frames should have been inserted
    expect(useEditStore.getState().insertedFrames.size).toBe(0)
  })

  it('shows no episode message when no episode is loaded', () => {
    render(<Timeline />)
    expect(screen.getByText('No episode selected')).toBeInTheDocument()
  })
})
