import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { JOINT_COLORS } from '../joint-constants'
import { JointSelector } from '../JointSelector'

function renderSelector(overrides: Partial<React.ComponentProps<typeof JointSelector>> = {}) {
  const onSelectJoints = vi.fn()
  const props: React.ComponentProps<typeof JointSelector> = {
    jointCount: 16,
    selectedJoints: [],
    onSelectJoints,
    colors: JOINT_COLORS,
    ...overrides,
  }
  render(<JointSelector {...props} />)
  return { onSelectJoints, props }
}

describe('JointSelector', () => {
  it('renders empty state when jointCount is 0', () => {
    renderSelector({ jointCount: 0 })
    expect(screen.getByText('No joints available')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'All' })).not.toBeInTheDocument()
  })

  it('renders all default joint groups when jointCount is 16', () => {
    renderSelector()
    expect(screen.getByTestId('joint-group-right-pos')).toBeInTheDocument()
    expect(screen.getByTestId('joint-group-right-orient')).toBeInTheDocument()
    expect(screen.getByTestId('joint-group-right-grip')).toBeInTheDocument()
    expect(screen.getByTestId('joint-group-left-pos')).toBeInTheDocument()
    expect(screen.getByTestId('joint-group-left-orient')).toBeInTheDocument()
    expect(screen.getByTestId('joint-group-left-grip')).toBeInTheDocument()
  })

  it('toggles a joint chip and emits a sorted updated selection', async () => {
    const user = userEvent.setup()
    const { onSelectJoints } = renderSelector({ selectedJoints: [2] })

    const chips = screen
      .getAllByRole('button')
      .filter((b) => b.getAttribute('data-joint-chip') !== null)
    // Chip ordering follows JOINT_GROUPS: right-pos [0,1,2] then right-orient [3..6] etc.
    await user.click(chips[0])

    expect(onSelectJoints).toHaveBeenCalledWith([0, 2])
  })

  it('selects all joints when the All button is clicked', async () => {
    const user = userEvent.setup()
    const { onSelectJoints } = renderSelector({ jointCount: 4 })

    await user.click(screen.getByRole('button', { name: 'All' }))

    expect(onSelectJoints).toHaveBeenCalledWith([0, 1, 2, 3])
  })

  it('clears the selection when the None button is clicked', async () => {
    const user = userEvent.setup()
    const { onSelectJoints } = renderSelector({ selectedJoints: [0, 1, 2] })

    await user.click(screen.getByRole('button', { name: 'None' }))

    expect(onSelectJoints).toHaveBeenCalledWith([])
  })

  it('only shows the defaults settings button when onOpenDefaults is provided', () => {
    const { unmount } = render(
      <JointSelector
        jointCount={4}
        selectedJoints={[]}
        onSelectJoints={vi.fn()}
        colors={JOINT_COLORS}
      />,
    )
    expect(screen.queryByLabelText('Edit joint defaults')).not.toBeInTheDocument()
    unmount()

    const onOpenDefaults = vi.fn()
    render(
      <JointSelector
        jointCount={4}
        selectedJoints={[]}
        onSelectJoints={vi.fn()}
        colors={JOINT_COLORS}
        onOpenDefaults={onOpenDefaults}
      />,
    )
    expect(screen.getByLabelText('Edit joint defaults')).toBeInTheDocument()
  })
})
