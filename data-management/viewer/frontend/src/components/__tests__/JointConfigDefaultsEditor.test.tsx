import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import { JOINT_COLORS } from '@/components/episode-viewer/joint-constants'
import { JointConfigDefaultsEditor } from '@/components/episode-viewer/JointConfigDefaultsEditor'

beforeAll(() => {
  vi.spyOn(Element.prototype, 'scrollIntoView')
})

afterEach(cleanup)

const defaultGroups = [
  { id: 'right-pos', label: 'Right Arm', indices: [0, 1, 2] },
  { id: 'right-orient', label: 'Right Orientation', indices: [3, 4, 5, 6] },
  { id: 'left-pos', label: 'Left Arm', indices: [7, 8, 9] },
]

const defaultLabels: Record<string, string> = {
  '0': 'Right X',
  '1': 'Right Y',
  '2': 'Right Z',
  '3': 'Right Qx',
  '4': 'Right Qy',
  '5': 'Right Qz',
  '6': 'Right Qw',
  '7': 'Left X',
  '8': 'Left Y',
  '9': 'Left Z',
}

const baseProps = {
  open: true,
  onOpenChange: vi.fn(),
  groups: defaultGroups,
  labels: defaultLabels,
  onSave: vi.fn(),
  colors: JOINT_COLORS,
}

describe('JointConfigDefaultsEditor', () => {
  it('renders dialog when open', () => {
    render(<JointConfigDefaultsEditor {...baseProps} />)
    expect(screen.getByText('Joint Configuration Defaults')).toBeInTheDocument()
  })

  it('renders a dedicated scroll region for the dialog body', () => {
    render(<JointConfigDefaultsEditor {...baseProps} />)
    const scrollArea = screen.getByTestId('joint-config-scroll-area')
    expect(scrollArea).toBeInTheDocument()
    expect(scrollArea).toHaveClass('overflow-y-auto')
  })

  it('does not render dialog content when closed', () => {
    render(<JointConfigDefaultsEditor {...baseProps} open={false} />)
    expect(screen.queryByText('Joint Configuration Defaults')).not.toBeInTheDocument()
  })

  it('displays all groups with their labels', () => {
    render(<JointConfigDefaultsEditor {...baseProps} />)
    expect(screen.getByText('Right Arm')).toBeInTheDocument()
    expect(screen.getByText('Right Orientation')).toBeInTheDocument()
    expect(screen.getByText('Left Arm')).toBeInTheDocument()
  })

  it('displays joint labels within their groups', () => {
    render(<JointConfigDefaultsEditor {...baseProps} />)
    expect(screen.getByText('Right X')).toBeInTheDocument()
    expect(screen.getByText('Right Y')).toBeInTheDocument()
    expect(screen.getByText('Right Z')).toBeInTheDocument()
    expect(screen.getByText('Right Qx')).toBeInTheDocument()
    expect(screen.getByText('Left X')).toBeInTheDocument()
  })

  it('renders joint color indicators', () => {
    render(<JointConfigDefaultsEditor {...baseProps} />)
    const colorDots = document.querySelectorAll('[data-joint-color]')
    expect(colorDots.length).toBe(10)
  })

  it('allows editing a joint label', async () => {
    const user = userEvent.setup()
    render(<JointConfigDefaultsEditor {...baseProps} />)
    const editButtons = screen.getAllByLabelText('Edit joint label')
    await user.click(editButtons[0])
    const input = screen.getByRole('textbox')
    await user.clear(input)
    await user.type(input, 'Custom Joint{Enter}')
    expect(screen.getByText('Custom Joint')).toBeInTheDocument()
  })

  it('reveals joint actions when keyboard focus enters a chip', () => {
    render(<JointConfigDefaultsEditor {...baseProps} />)

    const editButton = screen.getAllByLabelText('Edit joint label')[0]
    const chip = editButton.closest('.group\\/chip')

    expect(chip).toHaveClass('focus-within:ring-2')
    expect(editButton).toHaveClass('group-focus-within/chip:opacity-100')
    expect(screen.getAllByLabelText('Move to group')[0]).toHaveClass(
      'group-focus-within/chip:opacity-100',
    )
    expect(screen.getAllByLabelText('Remove joint from group')[0]).toHaveClass(
      'group-focus-within/chip:opacity-100',
    )
  })

  it('allows editing a group label', async () => {
    const user = userEvent.setup()
    render(<JointConfigDefaultsEditor {...baseProps} />)
    const editButtons = screen.getAllByLabelText('Edit group label')
    await user.click(editButtons[0])
    const input = screen.getByRole('textbox')
    await user.clear(input)
    await user.type(input, 'Custom Group{Enter}')
    expect(screen.getByText('Custom Group')).toBeInTheDocument()
  })

  it('scrolls a new group into view and opens it for rename', async () => {
    const user = userEvent.setup()
    const scrollIntoView = vi.mocked(Element.prototype.scrollIntoView)
    render(<JointConfigDefaultsEditor {...baseProps} />)

    await user.click(screen.getByText('Add Group'))

    expect(screen.getByDisplayValue('New Group')).toBeInTheDocument()
    expect(scrollIntoView).toHaveBeenCalled()
  })

  it('allows deleting a group', async () => {
    const user = userEvent.setup()
    render(<JointConfigDefaultsEditor {...baseProps} />)
    const deleteButtons = screen.getAllByLabelText('Delete group')
    await user.click(deleteButtons[0])
    expect(screen.queryByText('Right Arm')).not.toBeInTheDocument()
  })

  it('moves joints to ungrouped when their group is deleted', async () => {
    const user = userEvent.setup()
    render(<JointConfigDefaultsEditor {...baseProps} />)
    const deleteButtons = screen.getAllByLabelText('Delete group')
    await user.click(deleteButtons[0])
    // Right X, Y, Z should now appear in Ungrouped section
    const ungrouped = screen.getByTestId('ungrouped-joints')
    expect(within(ungrouped).getByText('Right X')).toBeInTheDocument()
  })

  it('allows assigning an ungrouped joint to a group', async () => {
    const user = userEvent.setup()
    const propsWithUngrouped = {
      ...baseProps,
      labels: { ...defaultLabels, '10': 'Extra Joint' },
    }
    render(<JointConfigDefaultsEditor {...propsWithUngrouped} />)
    const ungrouped = screen.getByTestId('ungrouped-joints')
    const addButtons = within(ungrouped).getAllByLabelText('Assign to group')
    await user.click(addButtons[0])
    // Should show group selection buttons inside the ungrouped section
    const assignButtons = within(ungrouped).getAllByRole('button')
    const groupOptionLabels = assignButtons.map((b) => b.textContent)
    expect(groupOptionLabels).toEqual(expect.arrayContaining(['Right Arm', 'Left Arm']))
  })

  it('calls onSave with updated config when Save is clicked', async () => {
    const user = userEvent.setup()
    const onSave = vi.fn()
    render(<JointConfigDefaultsEditor {...baseProps} onSave={onSave} />)
    await user.click(screen.getByText('Save'))
    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({
        groups: defaultGroups,
        labels: defaultLabels,
      }),
    )
  })

  it('closes dialog and discards changes when Cancel is clicked', async () => {
    const user = userEvent.setup()
    const onOpenChange = vi.fn()
    render(<JointConfigDefaultsEditor {...baseProps} onOpenChange={onOpenChange} />)
    // Make a change first
    const deleteButtons = screen.getAllByLabelText('Delete group')
    await user.click(deleteButtons[0])
    // Cancel
    await user.click(screen.getByText('Cancel'))
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('resets to built-in defaults when Reset is clicked', async () => {
    const user = userEvent.setup()
    render(<JointConfigDefaultsEditor {...baseProps} />)
    // Delete a group first
    const deleteButtons = screen.getAllByLabelText('Delete group')
    await user.click(deleteButtons[0])
    expect(screen.queryByText('Right Arm')).not.toBeInTheDocument()
    // Reset
    await user.click(screen.getByText('Reset'))
    // Built-in defaults from joint-constants.ts include 6 groups
    expect(screen.getByText('Right Arm')).toBeInTheDocument()
  })

  it('shows saving state during save operation', () => {
    render(<JointConfigDefaultsEditor {...baseProps} isSaving />)
    expect(screen.getByText('Saving…')).toBeInTheDocument()
  })

  it('assigns, removes and reassigns an ungrouped joint before persisting it', async () => {
    const user = userEvent.setup()
    const onSave = vi.fn()
    render(
      <JointConfigDefaultsEditor
        {...baseProps}
        labels={{ ...defaultLabels, '10': 'Extra Joint' }}
        onSave={onSave}
      />,
    )
    const ungrouped = screen.getByTestId('ungrouped-joints')
    await user.click(within(ungrouped).getByLabelText('Assign to group'))
    await user.click(within(ungrouped).getByRole('button', { name: 'Right Arm' }))
    const extra =
      screen.getByText('Extra Joint').closest('[data-joint-id]') ??
      screen.getByText('Extra Joint').parentElement!
    await user.click(within(extra as HTMLElement).getByLabelText('Remove joint from group'))
    expect(within(screen.getByTestId('ungrouped-joints')).getByText('Extra Joint')).toBeVisible()
    await user.click(
      within(screen.getByTestId('ungrouped-joints')).getByLabelText('Assign to group'),
    )
    await user.click(
      within(screen.getByTestId('ungrouped-joints')).getByRole('button', { name: 'Left Arm' }),
    )
    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({
        groups: expect.arrayContaining([
          expect.objectContaining({ id: 'left-pos', indices: [7, 8, 9, 10] }),
        ]),
      }),
    )
  })

  it('cancels empty names and invalid indices and commits changed names on blur', async () => {
    const user = userEvent.setup()
    render(<JointConfigDefaultsEditor {...baseProps} />)
    await user.click(screen.getAllByLabelText('Edit joint label')[0])
    await user.clear(screen.getByRole('textbox'))
    await user.keyboard('{Enter}')
    expect(screen.getByText('Right X')).toBeVisible()
    await user.click(screen.getAllByLabelText('Edit joint label')[0])
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    await user.click(screen.getAllByLabelText('Edit group label')[0])
    await user.clear(screen.getByRole('textbox'))
    await user.type(screen.getByRole('textbox'), 'Renamed')
    await user.tab()
    expect(screen.getByText('Renamed')).toBeVisible()
    await user.click(screen.getAllByLabelText('Edit joint index')[0])
    await user.clear(screen.getByRole('spinbutton'))
    await user.keyboard('{Enter}')
    expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument()
    await user.click(screen.getAllByLabelText('Edit joint index')[0])
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument()
  })

  it('retains the Save control during pending work and conflicts without duplicate submission', async () => {
    const user = userEvent.setup()
    const onSave = vi.fn()
    const { rerender } = render(<JointConfigDefaultsEditor {...baseProps} onSave={onSave} />)
    const save = screen.getByRole('button', { name: 'Save' })
    save.focus()
    await user.keyboard('{Enter}')
    expect(onSave).toHaveBeenCalledOnce()
    rerender(<JointConfigDefaultsEditor {...baseProps} onSave={onSave} isSaving />)
    expect(save).toHaveAttribute('aria-disabled', 'true')
    expect(save).not.toBeDisabled()
    expect(save).toHaveFocus()
    await user.keyboard('{Enter}')
    expect(onSave).toHaveBeenCalledOnce()
    rerender(<JointConfigDefaultsEditor {...baseProps} onSave={onSave} saveError="Conflict" />)
    expect(save).toHaveFocus()
    expect(screen.getByRole('alert')).toHaveTextContent('Joint defaults were not saved.')
  })

  describe('move joint between groups', () => {
    it('shows move-to-group options when clicking move button on a grouped joint', async () => {
      const user = userEvent.setup()
      render(<JointConfigDefaultsEditor {...baseProps} />)
      const moveButtons = screen.getAllByLabelText('Move to group')
      await user.click(moveButtons[0])
      // Should show target group options (excluding the joint's current group)
      const picker = screen.getByTestId('group-picker')
      expect(within(picker).getByText('Right Orientation')).toBeInTheDocument()
      expect(within(picker).getByText('Left Arm')).toBeInTheDocument()
    })

    it('moves a joint from one group to another', async () => {
      const user = userEvent.setup()
      const onSave = vi.fn()
      render(<JointConfigDefaultsEditor {...baseProps} onSave={onSave} />)
      // Move joint 0 (Right X) from Right Arm to Left Arm
      const moveButtons = screen.getAllByLabelText('Move to group')
      await user.click(moveButtons[0])
      // Click Left Arm in the group picker
      const groupPicker = screen.getByTestId('group-picker')
      await user.click(within(groupPicker).getByText('Left Arm'))
      // Save and verify
      await user.click(screen.getByText('Save'))
      const savedConfig = onSave.mock.calls[0][0]
      const rightArm = savedConfig.groups.find((g: { id: string }) => g.id === 'right-pos')
      const leftArm = savedConfig.groups.find((g: { id: string }) => g.id === 'left-pos')
      expect(rightArm.indices).not.toContain(0)
      expect(leftArm.indices).toContain(0)
    })
  })

  describe('edit joint index', () => {
    it('displays joint indices next to labels', () => {
      render(<JointConfigDefaultsEditor {...baseProps} />)
      const indexBadges = screen.getAllByTestId('joint-index')
      expect(indexBadges.length).toBe(10)
      expect(indexBadges[0].textContent).toBe('0')
    })

    it('commits a joint index edit when the field loses focus after a valid change', async () => {
      const user = userEvent.setup()
      render(<JointConfigDefaultsEditor {...baseProps} />)
      const editIndexButtons = screen.getAllByLabelText('Edit joint index')
      await user.click(editIndexButtons[0])
      const input = screen.getByRole('spinbutton')
      await user.clear(input)
      await user.type(input, '20')
      await user.tab()
      const indexBadges = screen.getAllByTestId('joint-index')
      expect(indexBadges[0].textContent).toBe('20')
    })

    it('saves updated indices correctly', async () => {
      const user = userEvent.setup()
      const onSave = vi.fn()
      render(<JointConfigDefaultsEditor {...baseProps} onSave={onSave} />)
      // Edit index of first joint from 0 to 42
      const editIndexButtons = screen.getAllByLabelText('Edit joint index')
      await user.click(editIndexButtons[0])
      const input = screen.getByRole('spinbutton')
      await user.clear(input)
      await user.type(input, '42{Enter}')
      await user.click(screen.getByText('Save'))
      const savedConfig = onSave.mock.calls[0][0]
      const rightArm = savedConfig.groups.find((g: { id: string }) => g.id === 'right-pos')
      expect(rightArm.indices).toContain(42)
      expect(rightArm.indices).not.toContain(0)
      // Label should transfer to new index
      expect(savedConfig.labels['42']).toBe('Right X')
    })

    it('shows a blocking warning when duplicate indices are introduced', async () => {
      const user = userEvent.setup()
      render(<JointConfigDefaultsEditor {...baseProps} />)
      const editIndexButtons = screen.getAllByLabelText('Edit joint index')
      await user.click(editIndexButtons[0])
      const input = screen.getByRole('spinbutton')
      await user.clear(input)
      await user.type(input, '1{Enter}')

      expect(screen.getByRole('alert')).toHaveTextContent(
        'One or more joint labels now share the same index. Fix duplicate indices before saving.',
      )
      expect(screen.getByText('Save')).toBeDisabled()
    })

    it('does not save while duplicate indices remain', async () => {
      const user = userEvent.setup()
      const onSave = vi.fn()
      render(<JointConfigDefaultsEditor {...baseProps} onSave={onSave} />)

      const editIndexButtons = screen.getAllByLabelText('Edit joint index')
      await user.click(editIndexButtons[0])
      const input = screen.getByRole('spinbutton')
      await user.clear(input)
      await user.type(input, '1{Enter}')

      await user.click(screen.getByText('Save'))
      expect(onSave).not.toHaveBeenCalled()
    })
  })
  it('uses minimum target dimensions for compact joint actions', () => {
    render(<JointConfigDefaultsEditor {...baseProps} />)

    for (const name of ['Edit joint label', 'Move to group', 'Remove joint from group']) {
      expect(screen.getAllByRole('button', { name })[0]).toHaveClass('h-6', 'w-6')
    }
  })
})
