import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { SubtaskTimelineTrack } from '@/components/subtask-timeline/SubtaskTimelineTrack'
import { useEpisodeStore, usePlaybackControls, useSubtaskState } from '@/stores'
import type { SubtaskSegment } from '@/types/episode-edit'

vi.mock('@/stores', () => ({
  useEpisodeStore: vi.fn(),
  usePlaybackControls: vi.fn(),
  useSubtaskState: vi.fn(),
}))

vi.mock('@/components/subtask-timeline/SubtaskSegmentSlider', () => ({
  SubtaskSegmentSlider: ({
    segment,
    isActive,
    onClick,
    onRangeChange,
  }: {
    segment: SubtaskSegment
    isActive?: boolean
    onClick?: () => void
    onRangeChange: (range: [number, number]) => void
  }) => (
    <div data-testid={`slider-${segment.id}`} data-active={isActive ? 'true' : 'false'}>
      <button type="button" data-testid={`slider-click-${segment.id}`} onClick={onClick}>
        click
      </button>
      <button
        type="button"
        data-testid={`slider-change-${segment.id}`}
        onClick={() => onRangeChange([5, 25])}
      >
        change
      </button>
    </div>
  ),
}))

interface MockEpisodeState {
  currentEpisode: { meta: { length: number } } | null
}

interface MockSubtaskState {
  subtasks: SubtaskSegment[]
  updateSubtask: ReturnType<typeof vi.fn>
  validationErrors: string[]
}

interface MockPlaybackState {
  setCurrentFrame: ReturnType<typeof vi.fn>
}

const segmentA: SubtaskSegment = {
  id: 'a',
  label: 'Approach',
  frameRange: [60, 90],
  color: '#ff0000',
  source: 'manual',
}
const segmentB: SubtaskSegment = {
  id: 'b',
  label: 'Grasp',
  frameRange: [10, 40],
  color: '#00ff00',
  source: 'manual',
}

describe('SubtaskTimelineTrack', () => {
  let episodeState: MockEpisodeState
  let subtaskState: MockSubtaskState
  let playbackState: MockPlaybackState

  beforeEach(() => {
    episodeState = { currentEpisode: { meta: { length: 100 } } }
    subtaskState = {
      subtasks: [segmentA, segmentB],
      updateSubtask: vi.fn(),
      validationErrors: [],
    }
    playbackState = { setCurrentFrame: vi.fn() }

    vi.mocked(useEpisodeStore).mockImplementation((selector: unknown) =>
      (selector as (state: MockEpisodeState) => unknown)(episodeState),
    )
    vi.mocked(useSubtaskState).mockReturnValue(
      subtaskState as unknown as ReturnType<typeof useSubtaskState>,
    )
    vi.mocked(usePlaybackControls).mockReturnValue(
      playbackState as unknown as ReturnType<typeof usePlaybackControls>,
    )
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('renders nothing when no current episode is loaded', () => {
    episodeState.currentEpisode = null

    const { container } = render(<SubtaskTimelineTrack totalFrames={100} />)

    expect(container).toBeEmptyDOMElement()
  })

  it('renders read-only segment buttons sorted by start frame', () => {
    render(<SubtaskTimelineTrack totalFrames={100} />)

    const graspButton = screen.getByTitle('Grasp (10-40)')
    const approachButton = screen.getByTitle('Approach (60-90)')

    expect(graspButton).toBeInTheDocument()
    expect(approachButton).toBeInTheDocument()
    expect(graspButton.compareDocumentPosition(approachButton)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    )
  })

  it('positions read-only segments using percent-of-total geometry', () => {
    render(<SubtaskTimelineTrack totalFrames={100} />)

    const grasp = screen.getByTitle('Grasp (10-40)')
    expect(grasp).toHaveStyle({ left: '10%', width: '30%' })
    expect(grasp.getAttribute('style')).toContain('#00ff00')
  })

  it('navigates to the segment start frame and notifies on segment click', async () => {
    const user = userEvent.setup()
    const onSegmentClick = vi.fn()
    render(<SubtaskTimelineTrack totalFrames={100} onSegmentClick={onSegmentClick} />)

    await user.click(screen.getByTitle('Grasp (10-40)'))

    expect(playbackState.setCurrentFrame).toHaveBeenCalledWith(10)
    expect(onSegmentClick).toHaveBeenCalledWith(segmentB)
  })

  it('highlights the selected segment with a ring class', () => {
    render(<SubtaskTimelineTrack totalFrames={100} selectedSegmentId="a" />)

    expect(screen.getByTitle('Approach (60-90)').className).toContain('ring-primary')
    expect(screen.getByTitle('Grasp (10-40)').className).not.toContain('ring-primary')
  })

  it('renders editable sliders instead of buttons when editable is true', () => {
    render(<SubtaskTimelineTrack totalFrames={100} editable />)

    expect(screen.getByTestId('slider-a')).toBeInTheDocument()
    expect(screen.getByTestId('slider-b')).toBeInTheDocument()
    expect(screen.queryByTitle('Grasp (10-40)')).toBeNull()
  })

  it('marks the selected slider as active when editable', () => {
    render(<SubtaskTimelineTrack totalFrames={100} editable selectedSegmentId="b" />)

    expect(screen.getByTestId('slider-b')).toHaveAttribute('data-active', 'true')
    expect(screen.getByTestId('slider-a')).toHaveAttribute('data-active', 'false')
  })

  it('propagates editable slider clicks back through onSegmentClick', async () => {
    const user = userEvent.setup()
    const onSegmentClick = vi.fn()
    render(<SubtaskTimelineTrack totalFrames={100} editable onSegmentClick={onSegmentClick} />)

    await user.click(screen.getByTestId('slider-click-b'))

    expect(playbackState.setCurrentFrame).toHaveBeenCalledWith(10)
    expect(onSegmentClick).toHaveBeenCalledWith(segmentB)
  })

  it('forwards slider range changes to updateSubtask', async () => {
    const user = userEvent.setup()
    render(<SubtaskTimelineTrack totalFrames={100} editable />)

    await user.click(screen.getByTestId('slider-change-a'))

    expect(subtaskState.updateSubtask).toHaveBeenCalledWith('a', { frameRange: [5, 25] })
  })

  it('renders a draft range overlay using percent geometry', () => {
    const { container } = render(<SubtaskTimelineTrack totalFrames={200} draftRange={[20, 80]} />)

    const overlay = container.querySelector('div.border-dashed') as HTMLElement
    expect(overlay).toBeTruthy()
    expect(overlay).toHaveStyle({ left: '10%', width: '30%' })
  })

  it('renders a legend entry per segment with a selection highlight', async () => {
    const user = userEvent.setup()
    const onSegmentClick = vi.fn()
    render(
      <SubtaskTimelineTrack
        totalFrames={100}
        selectedSegmentId="b"
        onSegmentClick={onSegmentClick}
      />,
    )

    const legendButtons = screen.getAllByRole('button', { name: /Approach|Grasp/ })
    expect(legendButtons.length).toBeGreaterThanOrEqual(2)

    const legendGrasp = legendButtons.find(
      (button) => button.className.includes('bg-primary/10') && within(button).queryByText('Grasp'),
    )
    expect(legendGrasp).toBeDefined()

    await user.click(legendGrasp!)
    expect(playbackState.setCurrentFrame).toHaveBeenCalledWith(10)
    expect(onSegmentClick).toHaveBeenCalledWith(segmentB)
  })

  it('renders nothing in the legend when there are no segments', () => {
    subtaskState.subtasks = []

    render(<SubtaskTimelineTrack totalFrames={100} />)

    expect(screen.queryByText('Approach')).toBeNull()
    expect(screen.queryByText('Grasp')).toBeNull()
  })

  it('renders validation errors with a warning glyph', () => {
    subtaskState.validationErrors = ['Overlap detected', 'Out of range']

    render(<SubtaskTimelineTrack totalFrames={100} />)

    expect(screen.getByText('⚠ Overlap detected')).toBeInTheDocument()
    expect(screen.getByText('⚠ Out of range')).toBeInTheDocument()
  })

  it('merges consumer className onto the wrapper', () => {
    const { container } = render(
      <SubtaskTimelineTrack totalFrames={100} className="custom-class" />,
    )

    const wrapper = container.firstElementChild as HTMLElement
    expect(wrapper.className).toContain('custom-class')
  })
})
