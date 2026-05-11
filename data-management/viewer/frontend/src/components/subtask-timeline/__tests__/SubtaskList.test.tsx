import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { SubtaskList } from '@/components/subtask-timeline/SubtaskList'
import { usePlaybackControls, useSubtaskState } from '@/stores'
import type { SubtaskSegment } from '@/types/episode-edit'

vi.mock('@/stores', () => ({
  usePlaybackControls: vi.fn(),
  useSubtaskState: vi.fn(),
}))

interface MockSubtaskState {
  subtasks: SubtaskSegment[]
  updateSubtask: ReturnType<typeof vi.fn>
  removeSubtask: ReturnType<typeof vi.fn>
  reorderSubtasks: ReturnType<typeof vi.fn>
}

interface MockPlaybackState {
  setCurrentFrame: ReturnType<typeof vi.fn>
}

const segmentA: SubtaskSegment = {
  id: 'a',
  label: 'Approach',
  frameRange: [10, 30],
  color: '#3b82f6',
  source: 'manual',
}
const segmentB: SubtaskSegment = {
  id: 'b',
  label: 'Grasp',
  frameRange: [40, 60],
  color: '#10b981',
  source: 'manual',
}

describe('SubtaskList', () => {
  let subtaskState: MockSubtaskState
  let playbackState: MockPlaybackState

  beforeEach(() => {
    subtaskState = {
      subtasks: [],
      updateSubtask: vi.fn(),
      removeSubtask: vi.fn(),
      reorderSubtasks: vi.fn(),
    }
    playbackState = { setCurrentFrame: vi.fn() }

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

  it('renders empty-state instructions when there are no subtasks and no draft', () => {
    render(<SubtaskList />)

    expect(screen.getByText('Subtasks')).toBeInTheDocument()
    expect(
      screen.getByText(
        /Drag on the trajectory graph to select a frame range, then right click to create a subtask\./i,
      ),
    ).toBeInTheDocument()
  })

  it('renders subtasks with frame ranges and color swatches', () => {
    subtaskState.subtasks = [segmentA, segmentB]
    render(<SubtaskList />)

    expect(screen.getByText('Approach')).toBeInTheDocument()
    expect(screen.getByText('Frames 10 to 30')).toBeInTheDocument()
    expect(screen.getByText('Grasp')).toBeInTheDocument()
    expect(screen.getByText('Frames 40 to 60')).toBeInTheDocument()
  })

  it('marks the selected subtask with an Active badge', () => {
    subtaskState.subtasks = [segmentA, segmentB]
    render(<SubtaskList selectedSubtaskId="a" />)

    expect(screen.getByText('Active')).toBeInTheDocument()
  })

  it('selecting a subtask navigates to its start frame and notifies', async () => {
    const user = userEvent.setup()
    const onSelectionChange = vi.fn()
    subtaskState.subtasks = [segmentA, segmentB]
    render(<SubtaskList onSelectionChange={onSelectionChange} />)

    await user.click(screen.getByText('Grasp'))

    expect(playbackState.setCurrentFrame).toHaveBeenCalledWith(40)
    expect(onSelectionChange).toHaveBeenCalledWith('b')
  })

  it('disables the Move up button on the first subtask and Move down on the last', () => {
    subtaskState.subtasks = [segmentA, segmentB]
    render(<SubtaskList />)

    expect(screen.getByLabelText('Move Approach up')).toBeDisabled()
    expect(screen.getByLabelText('Move Approach down')).toBeEnabled()
    expect(screen.getByLabelText('Move Grasp up')).toBeEnabled()
    expect(screen.getByLabelText('Move Grasp down')).toBeDisabled()
  })

  it('reorders subtasks when Move up / Move down is clicked', async () => {
    const user = userEvent.setup()
    subtaskState.subtasks = [segmentA, segmentB]
    render(<SubtaskList />)

    await user.click(screen.getByLabelText('Move Grasp up'))
    expect(subtaskState.reorderSubtasks).toHaveBeenCalledWith(1, 0)

    await user.click(screen.getByLabelText('Move Approach down'))
    expect(subtaskState.reorderSubtasks).toHaveBeenCalledWith(0, 1)
  })

  it('deletes a subtask and clears selection when the deleted item was active', async () => {
    const user = userEvent.setup()
    const onSelectionChange = vi.fn()
    subtaskState.subtasks = [segmentA, segmentB]
    render(<SubtaskList selectedSubtaskId="a" onSelectionChange={onSelectionChange} />)

    await user.click(screen.getByLabelText('Delete Approach'))

    expect(subtaskState.removeSubtask).toHaveBeenCalledWith('a')
    expect(onSelectionChange).toHaveBeenCalledWith(null)
  })

  it('does not clear selection when deleting a non-selected subtask', async () => {
    const user = userEvent.setup()
    const onSelectionChange = vi.fn()
    subtaskState.subtasks = [segmentA, segmentB]
    render(<SubtaskList selectedSubtaskId="a" onSelectionChange={onSelectionChange} />)

    await user.click(screen.getByLabelText('Delete Grasp'))

    expect(subtaskState.removeSubtask).toHaveBeenCalledWith('b')
    expect(onSelectionChange).not.toHaveBeenCalled()
  })

  it('updates the subtask label as the inline label input changes', async () => {
    const user = userEvent.setup()
    subtaskState.subtasks = [segmentA]
    render(<SubtaskList />)

    const labelInput = screen.getByLabelText('Approach label')
    await user.type(labelInput, 'X')

    expect(subtaskState.updateSubtask).toHaveBeenLastCalledWith('a', { label: 'ApproachX' })
  })

  it('renders the draft selection panel with frame range text', () => {
    render(<SubtaskList draftRange={[15, 75]} maxFrame={200} />)

    expect(screen.getByText('Draft Selection')).toBeInTheDocument()
    expect(screen.getByText('Frames 15 to 75')).toBeInTheDocument()
  })

  it('invokes onCreateSubtaskFromRange when Create Subtask is clicked', async () => {
    const user = userEvent.setup()
    const onCreate = vi.fn()
    render(<SubtaskList draftRange={[15, 75]} maxFrame={200} onCreateSubtaskFromRange={onCreate} />)

    await user.click(screen.getByRole('button', { name: 'Create Subtask' }))

    expect(onCreate).toHaveBeenCalledWith([15, 75])
  })

  it('nudges the draft start boundary by -1 / +1 within bounds', async () => {
    const user = userEvent.setup()
    const onDraftRangeChange = vi.fn()
    render(
      <SubtaskList draftRange={[10, 50]} maxFrame={100} onDraftRangeChange={onDraftRangeChange} />,
    )

    const startCard = screen.getByText('Start').parentElement!
    const minus = within(startCard).getByRole('button', { name: '-1' })
    const plus = within(startCard).getByRole('button', { name: '+1' })

    await user.click(minus)
    expect(onDraftRangeChange).toHaveBeenLastCalledWith([9, 50])

    await user.click(plus)
    expect(onDraftRangeChange).toHaveBeenLastCalledWith([11, 50])
  })

  it('nudges the draft end boundary by -1 / +1 within bounds', async () => {
    const user = userEvent.setup()
    const onDraftRangeChange = vi.fn()
    render(
      <SubtaskList draftRange={[10, 50]} maxFrame={100} onDraftRangeChange={onDraftRangeChange} />,
    )

    const endCard = screen.getByText('End').parentElement!
    const minus = within(endCard).getByRole('button', { name: '-1' })
    const plus = within(endCard).getByRole('button', { name: '+1' })

    await user.click(minus)
    expect(onDraftRangeChange).toHaveBeenLastCalledWith([10, 49])

    await user.click(plus)
    expect(onDraftRangeChange).toHaveBeenLastCalledWith([10, 51])
  })

  it('clamps draft boundaries to [0, maxFrame] and orders min/max', () => {
    const onDraftRangeChange = vi.fn()
    render(
      <SubtaskList draftRange={[5, 95]} maxFrame={100} onDraftRangeChange={onDraftRangeChange} />,
    )

    const startInput = screen.getByLabelText('Draft selection start frame')
    fireEvent.change(startInput, { target: { value: '999' } })

    expect(onDraftRangeChange).toHaveBeenLastCalledWith([95, 100])
  })

  it('updates the draft end via the end input', () => {
    const onDraftRangeChange = vi.fn()
    render(
      <SubtaskList draftRange={[10, 50]} maxFrame={100} onDraftRangeChange={onDraftRangeChange} />,
    )

    const endInput = screen.getByLabelText('Draft selection end frame')
    fireEvent.change(endInput, { target: { value: '70' } })

    expect(onDraftRangeChange).toHaveBeenLastCalledWith([10, 70])
  })

  it('renders the subtasks panel when a draft exists even with zero subtasks', () => {
    render(<SubtaskList draftRange={[5, 10]} maxFrame={100} />)

    expect(screen.getByText('Subtasks')).toBeInTheDocument()
    expect(screen.getByText('Draft Selection')).toBeInTheDocument()
    expect(screen.queryByText(/right click to create a subtask/i)).toBeNull()
  })

  it('merges consumer className onto the wrapper for both empty and populated states', () => {
    const { container, rerender } = render(<SubtaskList className="custom-list" />)
    let wrapper = container.firstElementChild as HTMLElement
    expect(wrapper.className).toContain('custom-list')

    subtaskState.subtasks = [segmentA]
    rerender(<SubtaskList className="custom-list" />)
    wrapper = container.firstElementChild as HTMLElement
    expect(wrapper.className).toContain('custom-list')
  })
})
