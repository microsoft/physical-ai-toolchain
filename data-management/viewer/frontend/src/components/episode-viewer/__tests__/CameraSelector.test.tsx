import { fireEvent, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { renderWithQuery } from '@/test/test-utils'

import { CameraSelector } from '../CameraSelector'

describe('CameraSelector', () => {
  it('renders an empty state when no cameras are provided', () => {
    renderWithQuery(<CameraSelector cameras={[]} selectedCamera="" onSelectCamera={vi.fn()} />)
    expect(screen.getByText('No cameras available')).toBeInTheDocument()
  })

  it('renders the formatted camera name without a dropdown when there is exactly one camera', () => {
    renderWithQuery(
      <CameraSelector
        cameras={['observation.images.front_left']}
        selectedCamera="observation.images.front_left"
        onSelectCamera={vi.fn()}
      />,
    )
    expect(screen.getByText('Front Left')).toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('opens the dropdown and lists every camera when the toggle is clicked', async () => {
    const user = userEvent.setup()
    renderWithQuery(
      <CameraSelector
        cameras={['observation.images.front', 'observation.images.wrist']}
        selectedCamera="observation.images.front"
        onSelectCamera={vi.fn()}
      />,
    )

    await user.click(screen.getByRole('button'))
    expect(screen.getAllByText('Front').length).toBeGreaterThan(0)
    expect(screen.getByText('Wrist')).toBeInTheDocument()
  })

  it('invokes onSelectCamera and closes the dropdown when an option is selected', async () => {
    const user = userEvent.setup()
    const onSelectCamera = vi.fn()
    renderWithQuery(
      <CameraSelector
        cameras={['observation.images.front', 'observation.images.wrist']}
        selectedCamera="observation.images.front"
        onSelectCamera={onSelectCamera}
      />,
    )

    await user.click(screen.getByRole('button'))
    await user.click(screen.getByText('Wrist'))

    expect(onSelectCamera).toHaveBeenCalledWith('observation.images.wrist')
    expect(screen.queryByText('Wrist')).not.toBeInTheDocument()
  })

  it('closes the dropdown on outside mousedown', async () => {
    const user = userEvent.setup()
    renderWithQuery(
      <CameraSelector
        cameras={['observation.images.front', 'observation.images.wrist']}
        selectedCamera="observation.images.front"
        onSelectCamera={vi.fn()}
      />,
    )

    await user.click(screen.getByRole('button'))
    expect(screen.getByText('Wrist')).toBeInTheDocument()

    fireEvent.mouseDown(document.body)
    expect(screen.queryByText('Wrist')).not.toBeInTheDocument()
  })
})
