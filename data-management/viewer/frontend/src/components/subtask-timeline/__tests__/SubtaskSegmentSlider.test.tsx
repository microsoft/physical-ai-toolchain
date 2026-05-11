import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { SubtaskSegmentSlider } from '@/components/subtask-timeline/SubtaskSegmentSlider'
import type { SubtaskSegment } from '@/types/episode-edit'

vi.mock('@/components/ui/slider', () => ({
  Slider: ({
    children,
    onValueChange,
    value,
    min,
    max,
    step,
    minStepsBetweenThumbs,
    className,
  }: {
    children?: React.ReactNode
    onValueChange?: (values: number[]) => void
    value?: number[]
    min?: number
    max?: number
    step?: number
    minStepsBetweenThumbs?: number
    className?: string
  }) => (
    <div
      data-testid="slider-root"
      data-min={min}
      data-max={max}
      data-step={step}
      data-min-steps={minStepsBetweenThumbs}
      data-value={value?.join(',')}
      className={className}
    >
      <button
        type="button"
        data-testid="slider-emit-pair"
        onClick={() => onValueChange?.([(value?.[0] ?? 0) + 1, (value?.[1] ?? 0) + 2])}
      >
        emit-pair
      </button>
      <button type="button" data-testid="slider-emit-single" onClick={() => onValueChange?.([42])}>
        emit-single
      </button>
      {children}
    </div>
  ),
  SliderTrack: ({ children, className }: { children?: React.ReactNode; className?: string }) => (
    <div data-testid="slider-track" className={className}>
      {children}
    </div>
  ),
  SliderRange: ({
    className,
    style,
    onClick,
  }: {
    className?: string
    style?: React.CSSProperties
    onClick?: () => void
  }) => (
    <button
      type="button"
      data-testid="slider-range"
      className={className}
      style={style}
      onClick={onClick}
    />
  ),
  SliderThumb: ({
    className,
    style,
    'aria-label': ariaLabel,
  }: {
    className?: string
    style?: React.CSSProperties
    'aria-label'?: string
  }) => (
    <div
      data-testid="slider-thumb"
      className={className}
      style={style}
      aria-label={ariaLabel}
      aria-valuenow={0}
      role="slider"
    />
  ),
}))

const baseSegment: SubtaskSegment = {
  id: 'seg-1',
  label: 'Pick',
  frameRange: [10, 50],
  color: '#3b82f6',
  source: 'manual',
}

describe('SubtaskSegmentSlider', () => {
  afterEach(() => {
    cleanup()
  })

  it('renders slider with segment range, total frames, and step constraints', () => {
    render(
      <SubtaskSegmentSlider segment={baseSegment} totalFrames={1000} onRangeChange={vi.fn()} />,
    )

    const root = screen.getByTestId('slider-root')
    expect(root).toHaveAttribute('data-min', '0')
    expect(root).toHaveAttribute('data-max', '1000')
    expect(root).toHaveAttribute('data-step', '1')
    expect(root).toHaveAttribute('data-min-steps', '1')
    expect(root).toHaveAttribute('data-value', '10,50')
  })

  it('renders both thumbs with segment-labeled aria-labels and color borders', () => {
    render(<SubtaskSegmentSlider segment={baseSegment} totalFrames={100} onRangeChange={vi.fn()} />)

    const startThumb = screen.getByLabelText('Pick start frame')
    const endThumb = screen.getByLabelText('Pick end frame')
    expect(startThumb).toBeInTheDocument()
    expect(endThumb).toBeInTheDocument()
    expect(startThumb.getAttribute('style')).toContain('#3b82f6')
    expect(endThumb.getAttribute('style')).toContain('#3b82f6')
  })

  it('paints the slider range with the segment color', () => {
    render(<SubtaskSegmentSlider segment={baseSegment} totalFrames={100} onRangeChange={vi.fn()} />)

    expect(screen.getByTestId('slider-range').getAttribute('style')).toContain('#3b82f6')
  })

  it('applies active ring styling when isActive is true', () => {
    render(
      <SubtaskSegmentSlider
        segment={baseSegment}
        totalFrames={100}
        onRangeChange={vi.fn()}
        isActive
      />,
    )

    expect(screen.getByTestId('slider-range').className).toContain('ring-primary')
  })

  it('omits active ring styling when isActive is false', () => {
    render(<SubtaskSegmentSlider segment={baseSegment} totalFrames={100} onRangeChange={vi.fn()} />)

    expect(screen.getByTestId('slider-range').className).not.toContain('ring-primary')
  })

  it('forwards a paired value change to onRangeChange as a tuple', async () => {
    const user = userEvent.setup()
    const onRangeChange = vi.fn()
    render(
      <SubtaskSegmentSlider
        segment={baseSegment}
        totalFrames={100}
        onRangeChange={onRangeChange}
      />,
    )

    await user.click(screen.getByTestId('slider-emit-pair'))

    expect(onRangeChange).toHaveBeenCalledTimes(1)
    expect(onRangeChange).toHaveBeenCalledWith([11, 52])
  })

  it('ignores onValueChange events that are not pairs', async () => {
    const user = userEvent.setup()
    const onRangeChange = vi.fn()
    render(
      <SubtaskSegmentSlider
        segment={baseSegment}
        totalFrames={100}
        onRangeChange={onRangeChange}
      />,
    )

    await user.click(screen.getByTestId('slider-emit-single'))

    expect(onRangeChange).not.toHaveBeenCalled()
  })

  it('invokes onClick when the range bar is clicked', async () => {
    const user = userEvent.setup()
    const onClick = vi.fn()
    render(
      <SubtaskSegmentSlider
        segment={baseSegment}
        totalFrames={100}
        onRangeChange={vi.fn()}
        onClick={onClick}
      />,
    )

    await user.click(screen.getByTestId('slider-range'))

    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it('merges the consumer className onto the slider root', () => {
    render(
      <SubtaskSegmentSlider
        segment={baseSegment}
        totalFrames={100}
        onRangeChange={vi.fn()}
        className="custom-segment"
      />,
    )

    expect(screen.getByTestId('slider-root').className).toContain('custom-segment')
  })
})
