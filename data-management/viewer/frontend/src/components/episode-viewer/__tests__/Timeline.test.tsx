import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  useAnnotationStore,
  useEditStore,
  useEpisodeStore,
  useFrameInsertionState,
  usePlaybackControls,
  useTrajectoryAdjustmentState,
} from '@/stores'
import type { Anomaly } from '@/types'

import { Timeline } from '../Timeline'

vi.mock('@/stores', () => ({
  useAnnotationStore: vi.fn(),
  useEditStore: vi.fn(),
  useEpisodeStore: vi.fn(),
  useFrameInsertionState: vi.fn(),
  usePlaybackControls: vi.fn(),
  useTrajectoryAdjustmentState: vi.fn(),
}))

const mockedAnnotation = vi.mocked(useAnnotationStore)
const mockedEdit = vi.mocked(useEditStore)
const mockedEpisode = vi.mocked(useEpisodeStore)
const mockedFrameInsertion = vi.mocked(useFrameInsertionState)
const mockedPlayback = vi.mocked(usePlaybackControls)
const mockedTrajectoryAdjustment = vi.mocked(useTrajectoryAdjustmentState)

interface SetupOptions {
  episode?: unknown
  currentFrame?: number
  setCurrentFrame?: ReturnType<typeof vi.fn>
  anomalies?: Anomaly[]
  dataQualityIssues?: unknown[]
  removedFrames?: Set<number>
  insertedFrames?: Map<number, unknown>
  trajectoryAdjustments?: Map<number, unknown>
}

function makeAnomaly(
  id: string,
  start: number,
  end: number,
  severity: Anomaly['severity'] = 'high',
): Anomaly {
  return {
    id,
    type: 'collision',
    severity,
    frameRange: [start, end],
    timestamp: [0, 0],
    description: `anomaly ${id}`,
    autoDetected: true,
    verified: false,
  }
}

function setup(opts: SetupOptions = {}) {
  const setCurrentFrame = opts.setCurrentFrame ?? vi.fn()
  const episode =
    opts.episode === undefined ? { meta: { length: 200 }, trajectoryData: [] } : opts.episode
  const annotation =
    opts.anomalies !== undefined || opts.dataQualityIssues !== undefined
      ? {
          anomalies: { anomalies: opts.anomalies ?? [] },
          dataQuality: { issues: opts.dataQualityIssues ?? [] },
        }
      : null

  mockedEpisode.mockImplementation(((selector: unknown) =>
    typeof selector === 'function'
      ? (selector as (s: unknown) => unknown)({ currentEpisode: episode })
      : episode) as never)
  mockedAnnotation.mockImplementation(((selector: unknown) =>
    typeof selector === 'function'
      ? (selector as (s: unknown) => unknown)({ currentAnnotation: annotation })
      : annotation) as never)
  mockedEdit.mockImplementation(((selector: unknown) =>
    typeof selector === 'function'
      ? (selector as (s: unknown) => unknown)({
          removedFrames: opts.removedFrames ?? new Set<number>(),
        })
      : (opts.removedFrames ?? new Set<number>())) as never)
  mockedFrameInsertion.mockReturnValue({
    insertedFrames: opts.insertedFrames ?? new Map(),
  } as never)
  mockedTrajectoryAdjustment.mockReturnValue({
    trajectoryAdjustments: opts.trajectoryAdjustments ?? new Map(),
  } as never)
  mockedPlayback.mockReturnValue({
    currentFrame: opts.currentFrame ?? 0,
    setCurrentFrame,
    isPlaying: false,
    playbackSpeed: 1,
    togglePlayback: vi.fn(),
    setPlaybackSpeed: vi.fn(),
  } as never)

  return { setCurrentFrame }
}

describe('Timeline', () => {
  let rectSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    rectSpy = vi.spyOn(Element.prototype, 'getBoundingClientRect').mockReturnValue({
      width: 800,
      height: 40,
      left: 0,
      top: 0,
      right: 800,
      bottom: 40,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    } as DOMRect)
  })

  afterEach(() => {
    rectSpy.mockRestore()
    vi.resetAllMocks()
  })

  it('renders the placeholder when no episode is loaded', () => {
    setup({ episode: null })
    render(<Timeline />)
    expect(screen.getByText('No episode selected')).toBeInTheDocument()
  })

  it('renders no anomaly markers when there are no anomalies', () => {
    setup({ anomalies: [] })
    render(<Timeline />)
    const slider = screen.getByRole('slider')
    expect(slider.querySelectorAll('button[title]')).toHaveLength(0)
  })

  it('seeks to the clicked frame when the slider is clicked', () => {
    const { setCurrentFrame } = setup({ currentFrame: 0 })
    render(<Timeline />)
    const slider = screen.getByRole('slider')
    fireEvent.click(slider, { clientX: 400 })
    expect(setCurrentFrame).toHaveBeenCalledWith(100)
  })

  it('clamps the seek frame to the last valid index', () => {
    const { setCurrentFrame } = setup({ currentFrame: 0 })
    render(<Timeline />)
    const slider = screen.getByRole('slider')
    fireEvent.click(slider, { clientX: 1600 })
    expect(setCurrentFrame).toHaveBeenCalledWith(199)
  })

  it('moves the playhead one frame to the right on ArrowRight, clamped to totalFrames - 1', () => {
    const { setCurrentFrame } = setup({ currentFrame: 199 })
    render(<Timeline />)
    fireEvent.keyDown(screen.getByRole('slider'), { key: 'ArrowRight' })
    expect(setCurrentFrame).toHaveBeenCalledWith(199)
  })

  it('moves the playhead one frame to the left on ArrowLeft, clamped to zero', () => {
    const { setCurrentFrame } = setup({ currentFrame: 0 })
    render(<Timeline />)
    fireEvent.keyDown(screen.getByRole('slider'), { key: 'ArrowLeft' })
    expect(setCurrentFrame).toHaveBeenCalledWith(0)
  })

  it('caps the rendered anomaly markers at the maximum visible count', () => {
    const anomalies = Array.from({ length: 60 }, (_, i) =>
      makeAnomaly(String(i), i * 4, i * 4 + 1, 'high'),
    )
    setup({ anomalies })
    render(<Timeline />)
    const slider = screen.getByRole('slider')
    expect(slider.querySelectorAll('button[title]').length).toBeLessThanOrEqual(50)
  })

  it('positions the playhead based on the current frame', () => {
    setup({ currentFrame: 100 })
    render(<Timeline />)
    const slider = screen.getByRole('slider')
    const playhead = slider.querySelector('[style*="left"]') as HTMLElement | null
    expect(playhead).not.toBeNull()
    expect(playhead!.style.left).toBe('50%')
  })
})
