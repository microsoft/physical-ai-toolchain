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
      : episode) as unknown as typeof useEpisodeStore)
  mockedAnnotation.mockImplementation(((selector: unknown) =>
    typeof selector === 'function'
      ? (selector as (s: unknown) => unknown)({ currentAnnotation: annotation })
      : annotation) as unknown as typeof useAnnotationStore)
  mockedEdit.mockImplementation(((selector: unknown) =>
    typeof selector === 'function'
      ? (selector as (s: unknown) => unknown)({
          removedFrames: opts.removedFrames ?? new Set<number>(),
        })
      : (opts.removedFrames ?? new Set<number>())) as unknown as typeof useEditStore)
  mockedFrameInsertion.mockReturnValue({
    insertedFrames: opts.insertedFrames ?? new Map(),
  } as unknown as ReturnType<typeof useFrameInsertionState>)
  mockedTrajectoryAdjustment.mockReturnValue({
    trajectoryAdjustments: opts.trajectoryAdjustments ?? new Map(),
  } as unknown as ReturnType<typeof useTrajectoryAdjustmentState>)
  mockedPlayback.mockReturnValue({
    currentFrame: opts.currentFrame ?? 0,
    setCurrentFrame,
    isPlaying: false,
    playbackSpeed: 1,
    togglePlayback: vi.fn(),
    setPlaybackSpeed: vi.fn(),
  } as unknown as ReturnType<typeof usePlaybackControls>)

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
      makeAnomaly(String(i), i * 7, i * 7 + 1, 'high'),
    )
    setup({ anomalies })
    render(<Timeline />)
    const slider = screen.getByRole('slider')
    expect(slider.querySelectorAll('button[title]').length).toBeLessThanOrEqual(50)
  })

  it('renders edit and quality ranges and supports adjustment marker pointer and keyboard navigation', () => {
    const { setCurrentFrame } = setup({
      removedFrames: new Set([1, 2, 3, 8]),
      insertedFrames: new Map([
        [2, { interpolationFactor: 0.5 }],
        [4, { interpolationFactor: 0.25 }],
      ]),
      trajectoryAdjustments: new Map([[20, {}]]),
      dataQualityIssues: [
        { type: 'blur', severity: 'critical', affectedFrames: [10, 15] },
        { type: 'missing', severity: 'major', affectedFrames: [16, 18] },
        { type: 'lighting', severity: 'minor', affectedFrames: [25, 26] },
        { type: 'other', severity: 'minor' },
      ],
    })
    render(<Timeline />)
    expect(screen.getByTitle('Removed: frames 1-3')).toBeVisible()
    expect(screen.getByTitle('Removed: frames 8-8')).toBeVisible()
    expect(screen.getAllByTitle(/Inserted frame/)).toHaveLength(1)
    expect(screen.getByText('Critical Issues')).toBeVisible()
    expect(screen.getByText('Major Issues')).toBeVisible()
    const marker = screen.getByTitle('Trajectory adjustment at frame 20')
    fireEvent.click(marker)
    fireEvent.keyDown(marker, { key: 'Enter' })
    fireEvent.keyDown(marker, { key: ' ' })
    expect(setCurrentFrame).toHaveBeenCalledTimes(3)
    expect(setCurrentFrame).toHaveBeenLastCalledWith(20)
  })

  it('clusters close anomalies by severity and seeks to the representative', () => {
    const { setCurrentFrame } = setup({
      anomalies: [
        makeAnomaly('low', 10, 10, 'low'),
        makeAnomaly('high', 11, 11, 'high'),
        makeAnomaly('far', 100, 101, 'medium'),
      ],
    })
    render(<Timeline />)
    const markers = screen.getByRole('slider').querySelectorAll('button[title]')
    expect(markers).toHaveLength(2)
    fireEvent.click(markers[0])
    expect(setCurrentFrame).toHaveBeenCalledWith(11)
  })

  it('uses trajectory length when metadata is empty and seeks only during primary-button drags', () => {
    const { setCurrentFrame } = setup({
      episode: { meta: { length: 0 }, trajectoryData: [{}, {}, {}] },
    })
    render(<Timeline />)
    const slider = screen.getByRole('slider')
    expect(slider).toHaveAttribute('aria-valuemax', '2')
    fireEvent.mouseMove(slider, { clientX: 400, buttons: 0 })
    expect(setCurrentFrame).not.toHaveBeenCalled()
    fireEvent.mouseMove(slider, { clientX: 400, buttons: 1 })
    expect(setCurrentFrame).toHaveBeenCalledWith(1)
    fireEvent.click(slider, { clientX: -50 })
    expect(setCurrentFrame).toHaveBeenLastCalledWith(0)
  })

  it('positions the playhead based on the current frame', () => {
    setup({ currentFrame: 100 })
    render(<Timeline />)
    const slider = screen.getByRole('slider')
    const playhead = slider.querySelector('[style*="left"]') as HTMLElement | null
    expect(playhead).not.toBeNull()
    if (!playhead) return
    expect(playhead.style.left).toBe('50%')
  })
})
