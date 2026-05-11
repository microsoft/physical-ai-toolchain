import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { DetectionResult } from '@/types/detection'

import { DetectionTimeline } from '../DetectionTimeline'

const buildDetection = (frame: number, count: number): DetectionResult => ({
  frame,
  detections: Array.from({ length: count }, (_, i) => ({
    class_id: i,
    class_name: `class_${i}`,
    confidence: 0.9,
    bbox: [0, 0, 10, 10],
  })),
  processing_time_ms: 5,
})

const stubSliderRect = (width: number, left = 0) => {
  vi.spyOn(HTMLDivElement.prototype, 'getBoundingClientRect').mockReturnValue({
    width,
    height: 40,
    left,
    right: left + width,
    top: 0,
    bottom: 40,
    x: left,
    y: 0,
    toJSON: () => ({}),
  } as DOMRect)
}

describe('DetectionTimeline', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders frame range labels and the count of frames with detections', () => {
    render(
      <DetectionTimeline
        detectionsPerFrame={[buildDetection(2, 1), buildDetection(7, 3), buildDetection(9, 0)]}
        totalFrames={20}
        currentFrame={5}
        onFrameClick={vi.fn()}
      />,
    )

    expect(screen.getByText('Frame 0')).toBeInTheDocument()
    expect(screen.getByText('Frame 19')).toBeInTheDocument()
    expect(screen.getByText('Detection Density (2 frames with detections)')).toBeInTheDocument()
  })

  it('exposes the slider with correct aria attributes', () => {
    render(
      <DetectionTimeline
        detectionsPerFrame={[]}
        totalFrames={50}
        currentFrame={12}
        onFrameClick={vi.fn()}
      />,
    )

    const slider = screen.getByRole('slider')
    expect(slider).toHaveAttribute('aria-valuenow', '12')
    expect(slider).toHaveAttribute('aria-valuemin', '0')
    expect(slider).toHaveAttribute('aria-valuemax', '49')
  })

  it('translates a click x-coordinate into a frame index', () => {
    stubSliderRect(200)
    const onFrameClick = vi.fn()
    render(
      <DetectionTimeline
        detectionsPerFrame={[]}
        totalFrames={10}
        currentFrame={0}
        onFrameClick={onFrameClick}
      />,
    )

    fireEvent.click(screen.getByRole('slider'), { clientX: 100 })

    expect(onFrameClick).toHaveBeenCalledWith(5)
  })

  it('clamps a click below the slider to frame 0', () => {
    stubSliderRect(200, 50)
    const onFrameClick = vi.fn()
    render(
      <DetectionTimeline
        detectionsPerFrame={[]}
        totalFrames={10}
        currentFrame={5}
        onFrameClick={onFrameClick}
      />,
    )

    // clientX (10) - rect.left (50) = -40 → would be frame -2 → clamped to 0
    fireEvent.click(screen.getByRole('slider'), { clientX: 10 })

    expect(onFrameClick).toHaveBeenCalledWith(0)
  })

  it('clamps a click past the slider end to the last frame', () => {
    stubSliderRect(200)
    const onFrameClick = vi.fn()
    render(
      <DetectionTimeline
        detectionsPerFrame={[]}
        totalFrames={10}
        currentFrame={0}
        onFrameClick={onFrameClick}
      />,
    )

    // clientX 400 → x=400 → frame 20 → clamped to 9
    fireEvent.click(screen.getByRole('slider'), { clientX: 400 })

    expect(onFrameClick).toHaveBeenCalledWith(9)
  })

  it('renders one density bar per frame when total frames is below the cap', () => {
    render(
      <DetectionTimeline
        detectionsPerFrame={[buildDetection(0, 2), buildDetection(1, 1)]}
        totalFrames={5}
        currentFrame={0}
        onFrameClick={vi.fn()}
      />,
    )

    const bars = screen.getAllByTitle(/^Frame \d+: \d+ detections?$/)
    expect(bars).toHaveLength(5)
  })

  it('caps density bars at 200 when total frames exceeds the cap', () => {
    render(
      <DetectionTimeline
        detectionsPerFrame={[]}
        totalFrames={1000}
        currentFrame={0}
        onFrameClick={vi.fn()}
      />,
    )

    const bars = screen.getAllByTitle(/^Frame \d+: \d+ detections?$/)
    expect(bars).toHaveLength(200)
  })

  it('uses singular "detection" in density bar titles for single-detection frames', () => {
    render(
      <DetectionTimeline
        detectionsPerFrame={[buildDetection(0, 1), buildDetection(1, 3)]}
        totalFrames={2}
        currentFrame={0}
        onFrameClick={vi.fn()}
      />,
    )

    expect(screen.getByTitle('Frame 0: 1 detection')).toBeInTheDocument()
    expect(screen.getByTitle('Frame 1: 3 detections')).toBeInTheDocument()
  })

  it('renders one marker for each frame that has detections', () => {
    render(
      <DetectionTimeline
        detectionsPerFrame={[buildDetection(2, 1), buildDetection(8, 2), buildDetection(15, 0)]}
        totalFrames={20}
        currentFrame={0}
        onFrameClick={vi.fn()}
      />,
    )

    expect(screen.getByTitle('Jump to frame 2')).toBeInTheDocument()
    expect(screen.getByTitle('Jump to frame 8')).toBeInTheDocument()
    expect(screen.queryByTitle('Jump to frame 15')).not.toBeInTheDocument()
  })

  it('caps detection markers at 50', () => {
    const detections = Array.from({ length: 60 }, (_, i) => buildDetection(i, 1))
    render(
      <DetectionTimeline
        detectionsPerFrame={detections}
        totalFrames={60}
        currentFrame={0}
        onFrameClick={vi.fn()}
      />,
    )

    const markers = screen
      .getAllByRole('button')
      .filter((el) => el.getAttribute('title')?.startsWith('Jump to frame'))
    expect(markers).toHaveLength(50)
  })

  it('clicking a marker fires onFrameClick with that frame and stops propagation', () => {
    stubSliderRect(200)
    const onFrameClick = vi.fn()
    render(
      <DetectionTimeline
        detectionsPerFrame={[buildDetection(3, 1), buildDetection(6, 2)]}
        totalFrames={10}
        currentFrame={0}
        onFrameClick={onFrameClick}
      />,
    )

    fireEvent.click(screen.getByTitle('Jump to frame 6'), { clientX: 100 })

    // Marker handler runs once; slider parent handler is suppressed by stopPropagation
    expect(onFrameClick).toHaveBeenCalledTimes(1)
    expect(onFrameClick).toHaveBeenCalledWith(6)
  })

  it('marker Enter and Space keys trigger onFrameClick with the marker frame', () => {
    const onFrameClick = vi.fn()
    render(
      <DetectionTimeline
        detectionsPerFrame={[buildDetection(4, 1)]}
        totalFrames={10}
        currentFrame={0}
        onFrameClick={onFrameClick}
      />,
    )

    const marker = screen.getByTitle('Jump to frame 4')
    onFrameClick.mockClear()
    fireEvent.keyDown(marker, { key: ' ' })
    fireEvent.keyDown(marker, { key: 'Tab' })

    // Tab is ignored; Space triggers the marker handler exactly once.
    const markerCalls = onFrameClick.mock.calls.filter(([frame]) => frame === 4)
    expect(markerCalls).toHaveLength(1)
  })

  it('renders one quick-nav button per detection frame, sorted ascending', () => {
    render(
      <DetectionTimeline
        detectionsPerFrame={[buildDetection(7, 1), buildDetection(2, 1), buildDetection(4, 1)]}
        totalFrames={10}
        currentFrame={0}
        onFrameClick={vi.fn()}
      />,
    )

    const navButtons = screen
      .getAllByRole('button')
      .filter((el) => /^F\d+$/.test(el.textContent ?? ''))
      .map((el) => el.textContent)

    expect(navButtons).toEqual(['F2', 'F4', 'F7'])
  })

  it('caps quick-nav buttons at 10 and shows the "+N more" overflow label', () => {
    const detections = Array.from({ length: 14 }, (_, i) => buildDetection(i, 1))
    render(
      <DetectionTimeline
        detectionsPerFrame={detections}
        totalFrames={20}
        currentFrame={0}
        onFrameClick={vi.fn()}
      />,
    )

    const navButtons = screen
      .getAllByRole('button')
      .filter((el) => /^F\d+$/.test(el.textContent ?? ''))
    expect(navButtons).toHaveLength(10)
    expect(screen.getByText('+4 more')).toBeInTheDocument()
  })

  it('clicking a quick-nav button fires onFrameClick with that frame', () => {
    const onFrameClick = vi.fn()
    render(
      <DetectionTimeline
        detectionsPerFrame={[buildDetection(3, 1)]}
        totalFrames={10}
        currentFrame={0}
        onFrameClick={onFrameClick}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'F3' }))

    expect(onFrameClick).toHaveBeenCalledWith(3)
  })

  it('highlights the active quick-nav button when its frame matches currentFrame', () => {
    render(
      <DetectionTimeline
        detectionsPerFrame={[buildDetection(2, 1), buildDetection(5, 1)]}
        totalFrames={10}
        currentFrame={5}
        onFrameClick={vi.fn()}
      />,
    )

    expect(screen.getByRole('button', { name: 'F2' })).toHaveClass('bg-muted')
    expect(screen.getByRole('button', { name: 'F5' })).toHaveClass('bg-blue-500')
  })
})
