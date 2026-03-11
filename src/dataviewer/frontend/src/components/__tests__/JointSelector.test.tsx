import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { JOINT_COLORS } from '@/components/episode-viewer/joint-constants'
import { JointSelector } from '@/components/episode-viewer/JointSelector'

afterEach(cleanup)

const baseProps = {
  jointCount: 16,
  selectedJoints: [0, 1, 2],
  onSelectJoints: vi.fn(),
  colors: JOINT_COLORS,
}

describe('JointSelector', () => {
  it('renders group sections for joint categories', () => {
    render(<JointSelector {...baseProps} />)
    expect(screen.getByTestId('joint-group-right-pos')).toBeInTheDocument()
    expect(screen.getByTestId('joint-group-right-orient')).toBeInTheDocument()
    expect(screen.getByTestId('joint-group-right-grip')).toBeInTheDocument()
    expect(screen.getByTestId('joint-group-left-pos')).toBeInTheDocument()
    expect(screen.getByTestId('joint-group-left-orient')).toBeInTheDocument()
    expect(screen.getByTestId('joint-group-left-grip')).toBeInTheDocument()
  })

  it('renders All and None global controls', () => {
    render(<JointSelector {...baseProps} />)
    expect(screen.getByText('All')).toBeInTheDocument()
    expect(screen.getByText('None')).toBeInTheDocument()
  })

  it('renders joint chips within their group sections', () => {
    render(<JointSelector {...baseProps} selectedJoints={[0, 1]} />)
    const rightArmGroup = screen.getByTestId('joint-group-right-pos')
    expect(within(rightArmGroup).getByText('Right X')).toBeInTheDocument()
    expect(within(rightArmGroup).getByText('Right Y')).toBeInTheDocument()
    expect(within(rightArmGroup).getByText('Right Z')).toBeInTheDocument()
  })

  it('clicking group label toggles all joints in that group', () => {
    const onSelect = vi.fn()
    render(<JointSelector {...baseProps} selectedJoints={[]} onSelectJoints={onSelect} />)
    fireEvent.click(screen.getByText('Right Arm'))
    expect(onSelect).toHaveBeenCalledWith([0, 1, 2])
  })

  it('clicking group label deselects all when group is fully selected', () => {
    const onSelect = vi.fn()
    render(<JointSelector {...baseProps} onSelectJoints={onSelect} />)
    fireEvent.click(screen.getByText('Right Arm'))
    expect(onSelect).toHaveBeenCalledWith([])
  })

  it('joints not in any group render in Other section', () => {
    render(<JointSelector {...baseProps} jointCount={18} selectedJoints={[]} />)
    const otherGroup = screen.getByTestId('joint-group-other')
    expect(within(otherGroup).getByText('Ch 16')).toBeInTheDocument()
    expect(within(otherGroup).getByText('Ch 17')).toBeInTheDocument()
  })

  it('selected joints have data attribute for styling', () => {
    const { container } = render(
      <JointSelector {...baseProps} jointCount={4} selectedJoints={[0]} />,
    )
    const chips = container.querySelectorAll('[data-joint-chip]')
    expect(chips).toHaveLength(4)
  })

  it('clicking a joint chip toggles selection', () => {
    const onSelect = vi.fn()
    render(
      <JointSelector {...baseProps} jointCount={4} selectedJoints={[0]} onSelectJoints={onSelect} />,
    )
    fireEvent.click(screen.getByText('Right Y'))
    expect(onSelect).toHaveBeenCalledWith([0, 1])
  })

  it('clicking a selected joint chip deselects it', () => {
    const onSelect = vi.fn()
    render(
      <JointSelector {...baseProps} jointCount={4} selectedJoints={[0, 1]} onSelectJoints={onSelect} />,
    )
    fireEvent.click(screen.getByText('Right X'))
    expect(onSelect).toHaveBeenCalledWith([1])
  })

  it('All button selects every joint', () => {
    const onSelect = vi.fn()
    render(
      <JointSelector {...baseProps} jointCount={4} selectedJoints={[]} onSelectJoints={onSelect} />,
    )
    fireEvent.click(screen.getByText('All'))
    expect(onSelect).toHaveBeenCalledWith([0, 1, 2, 3])
  })

  it('None button clears all selections', () => {
    const onSelect = vi.fn()
    render(
      <JointSelector {...baseProps} jointCount={4} selectedJoints={[0, 1, 2]} onSelectJoints={onSelect} />,
    )
    fireEvent.click(screen.getByText('None'))
    expect(onSelect).toHaveBeenCalledWith([])
  })

  it('shows no joints message when jointCount is 0', () => {
    render(
      <JointSelector {...baseProps} jointCount={0} selectedJoints={[]} />,
    )
    expect(screen.getByText('No joints available')).toBeInTheDocument()
  })

  describe('inline editing', () => {
    it('double-clicking a joint label does not enter edit mode', async () => {
      const user = userEvent.setup()
      const onEditLabel = vi.fn()
      render(<JointSelector {...baseProps} editable onEditJointLabel={onEditLabel} />)
      await user.dblClick(screen.getByText('Right X'))
      expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    })

    it('right-clicking a joint label exposes the edit action', async () => {
      const onEditLabel = vi.fn()
      render(<JointSelector {...baseProps} editable onEditJointLabel={onEditLabel} />)

      fireEvent.contextMenu(screen.getByText('Right X'))
      expect(await screen.findByText('Edit Name')).toBeInTheDocument()
    })

    it('right-click editing a joint label focuses the input immediately', async () => {
      const user = userEvent.setup()
      render(<JointSelector {...baseProps} editable onEditJointLabel={vi.fn()} />)

      fireEvent.contextMenu(screen.getByText('Right X'))
      await user.click(await screen.findByText('Edit Name'))

      expect(await screen.findByRole('textbox')).toHaveFocus()
    })

    it('typing spaces while editing a joint label does not toggle selection', async () => {
      const user = userEvent.setup()
      const onSelectJoints = vi.fn()
      render(
        <JointSelector
          {...baseProps}
          editable
          onEditJointLabel={vi.fn()}
          onSelectJoints={onSelectJoints}
        />,
      )

      fireEvent.contextMenu(screen.getByText('Right X'))
      await user.click(await screen.findByText('Edit Name'))

      const input = await screen.findByRole('textbox')
      await user.type(input, ' A B')

      expect(input).toHaveValue('Right X A B')
      expect(input).toHaveFocus()
      expect(onSelectJoints).not.toHaveBeenCalled()
    })

    it('double-clicking a group label does not enter edit mode', async () => {
      const user = userEvent.setup()
      const onEditGroupLabel = vi.fn()
      render(<JointSelector {...baseProps} editable onEditGroupLabel={onEditGroupLabel} />)
      await user.dblClick(screen.getByText('Right Arm'))
      expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    })

    it('right-clicking a group label exposes the edit action', async () => {
      const onEditGroupLabel = vi.fn()
      render(<JointSelector {...baseProps} editable onEditGroupLabel={onEditGroupLabel} />)

      fireEvent.contextMenu(screen.getByText('Right Arm'))
      expect(await screen.findByText('Edit Name')).toBeInTheDocument()
    })

    it('right-click editing a group label focuses the input immediately', async () => {
      const user = userEvent.setup()
      render(<JointSelector {...baseProps} editable onEditGroupLabel={vi.fn()} />)

      fireEvent.contextMenu(screen.getByText('Right Arm'))
      await user.click(await screen.findByText('Edit Name'))

      expect(await screen.findByRole('textbox')).toHaveFocus()
    })
  })

  describe('group management', () => {
    it('calls onCreateGroup when creating a new group', () => {
      const onCreateGroup = vi.fn()
      render(<JointSelector {...baseProps} editable onCreateGroup={onCreateGroup} />)
      expect(onCreateGroup).toBeDefined()
    })

    it('calls onDeleteGroup callback', () => {
      const onDeleteGroup = vi.fn()
      render(<JointSelector {...baseProps} editable onDeleteGroup={onDeleteGroup} />)
      expect(onDeleteGroup).toBeDefined()
    })

    it('each group is a droppable container with data-group-id', () => {
      const { container } = render(<JointSelector {...baseProps} editable onMoveJoint={vi.fn()} />)
      const droppables = container.querySelectorAll('[data-group-id]')
      expect(droppables.length).toBeGreaterThanOrEqual(6)
      expect(container.querySelector('[data-group-id="right-pos"]')).toBeInTheDocument()
      expect(container.querySelector('[data-group-id="left-pos"]')).toBeInTheDocument()
    })
  })
})
