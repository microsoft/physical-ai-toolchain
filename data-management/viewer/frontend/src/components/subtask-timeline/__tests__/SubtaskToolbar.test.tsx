import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { SubtaskToolbar } from '@/components/subtask-timeline/SubtaskToolbar'
import { useEpisodeStore, usePlaybackControls, useSubtaskState } from '@/stores'
import type { SubtaskSegment } from '@/types/episode-edit'
import * as episodeEdit from '@/types/episode-edit'

vi.mock('@/stores', () => ({
  useEpisodeStore: vi.fn(),
  usePlaybackControls: vi.fn(),
  useSubtaskState: vi.fn(),
}))

vi.mock('@/components/ui/popover', () => ({
  Popover: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  PopoverTrigger: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
  PopoverContent: ({ children }: { children?: React.ReactNode }) => (
    <div data-testid="popover-content">{children}</div>
  ),
}))

interface MockEpisodeState {
  currentEpisode: { meta: { length: number } } | null
}

interface MockSubtaskState {
  subtasks: SubtaskSegment[]
  addSubtask: ReturnType<typeof vi.fn>
  updateSubtask: ReturnType<typeof vi.fn>
  removeSubtask: ReturnType<typeof vi.fn>
}

interface MockPlaybackState {
  currentFrame: number
}

const segment: SubtaskSegment = {
  id: 'seg-1',
  label: 'Pick',
  frameRange: [10, 50],
  color: '#3b82f6',
  source: 'manual',
}

describe('SubtaskToolbar', () => {
  let episodeState: MockEpisodeState
  let subtaskState: MockSubtaskState
  let playbackState: MockPlaybackState

  beforeEach(() => {
    episodeState = { currentEpisode: { meta: { length: 200 } } }
    subtaskState = {
      subtasks: [],
      addSubtask: vi.fn(),
      updateSubtask: vi.fn(),
      removeSubtask: vi.fn(),
    }
    playbackState = { currentFrame: 25 }

    vi.mocked(useEpisodeStore).mockImplementation((selector: unknown) =>
      (selector as (state: MockEpisodeState) => unknown)(episodeState),
    )
    vi.mocked(useSubtaskState).mockReturnValue(
      subtaskState as unknown as ReturnType<typeof useSubtaskState>,
    )
    vi.mocked(usePlaybackControls).mockReturnValue(
      playbackState as unknown as ReturnType<typeof usePlaybackControls>,
    )

    vi.spyOn(episodeEdit, 'generateSubtaskId').mockReturnValue('generated-id')
    vi.spyOn(episodeEdit, 'getNextSubtaskColor').mockReturnValue('#10b981')
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('renders nothing when no episode is loaded', () => {
    episodeState.currentEpisode = null

    const { container } = render(<SubtaskToolbar />)

    expect(container).toBeEmptyDOMElement()
  })

  it('renders only the Add button when no segment is selected', () => {
    render(<SubtaskToolbar />)

    expect(screen.getByRole('button', { name: /add/i })).toBeInTheDocument()
    expect(screen.queryByPlaceholderText('Label')).toBeNull()
  })

  it('adds a new segment defaulted to current frame and notifies selection', async () => {
    const user = userEvent.setup()
    const onSelectionChange = vi.fn()
    render(<SubtaskToolbar onSelectionChange={onSelectionChange} />)

    await user.click(screen.getByRole('button', { name: /add/i }))

    expect(subtaskState.addSubtask).toHaveBeenCalledTimes(1)
    expect(subtaskState.addSubtask).toHaveBeenCalledWith({
      id: 'generated-id',
      label: 'Subtask 1',
      frameRange: [25, 125],
      color: '#10b981',
      source: 'manual',
    })
    expect(onSelectionChange).toHaveBeenCalledWith('generated-id')
  })

  it('clamps the default end frame to totalFrames - 1', async () => {
    const user = userEvent.setup()
    playbackState.currentFrame = 195
    render(<SubtaskToolbar />)

    await user.click(screen.getByRole('button', { name: /add/i }))

    expect(subtaskState.addSubtask).toHaveBeenCalledWith(
      expect.objectContaining({ frameRange: [195, 199] }),
    )
  })

  it('numbers new segments by current subtask count', async () => {
    const user = userEvent.setup()
    subtaskState.subtasks = [{ ...segment }, { ...segment, id: 'seg-2' }]
    render(<SubtaskToolbar />)

    await user.click(screen.getByRole('button', { name: /add/i }))

    expect(subtaskState.addSubtask).toHaveBeenCalledWith(
      expect.objectContaining({ label: 'Subtask 3' }),
    )
  })

  it('renders edit controls when a segment is selected', () => {
    subtaskState.subtasks = [segment]
    render(<SubtaskToolbar selectedSegmentId="seg-1" />)

    expect(screen.getByDisplayValue('Pick')).toBeInTheDocument()
    expect(screen.getByText('10 - 50')).toBeInTheDocument()
    expect(screen.getByTestId('popover-content')).toBeInTheDocument()
  })

  it('updates the segment label as the input changes', async () => {
    const user = userEvent.setup()
    subtaskState.subtasks = [segment]
    render(<SubtaskToolbar selectedSegmentId="seg-1" />)

    const input = screen.getByDisplayValue('Pick')
    await user.type(input, '!')

    expect(subtaskState.updateSubtask).toHaveBeenLastCalledWith('seg-1', { label: 'Pick!' })
  })

  it('updates the segment color when a swatch is clicked', async () => {
    const user = userEvent.setup()
    subtaskState.subtasks = [segment]
    const popover = screen.queryByTestId('popover-content')
    expect(popover).toBeNull()

    const { container } = render(<SubtaskToolbar selectedSegmentId="seg-1" />)

    const swatchButtons = Array.from(
      container.querySelectorAll('[data-testid="popover-content"] button'),
    ) as HTMLElement[]
    const violetSwatch = swatchButtons.find((button) =>
      button.getAttribute('style')?.includes('#8b5cf6'),
    )
    expect(violetSwatch).toBeDefined()

    await user.click(violetSwatch!)

    expect(subtaskState.updateSubtask).toHaveBeenCalledWith('seg-1', { color: '#8b5cf6' })
  })

  it('removes the selected segment and clears selection on delete', async () => {
    const user = userEvent.setup()
    const onSelectionChange = vi.fn()
    subtaskState.subtasks = [segment]
    const { container } = render(
      <SubtaskToolbar selectedSegmentId="seg-1" onSelectionChange={onSelectionChange} />,
    )

    const trashButton = container.querySelector('button.text-destructive') as HTMLElement
    expect(trashButton).toBeTruthy()
    await user.click(trashButton)

    expect(subtaskState.removeSubtask).toHaveBeenCalledWith('seg-1')
    expect(onSelectionChange).toHaveBeenCalledWith(null)
  })

  it('does nothing on delete when no segment is selected', async () => {
    const user = userEvent.setup()
    subtaskState.subtasks = [segment]
    render(<SubtaskToolbar />)

    expect(screen.queryByText('10 - 50')).toBeNull()
    expect(subtaskState.removeSubtask).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: /add/i }))
    expect(subtaskState.removeSubtask).not.toHaveBeenCalled()
  })

  it('merges consumer className onto the wrapper', () => {
    const { container } = render(<SubtaskToolbar className="custom-toolbar" />)

    const wrapper = container.firstElementChild as HTMLElement
    expect(wrapper.className).toContain('custom-toolbar')
  })
})
