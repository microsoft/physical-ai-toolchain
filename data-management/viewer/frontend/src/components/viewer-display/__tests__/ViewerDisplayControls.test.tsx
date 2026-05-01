import { act, fireEvent, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'

import { useViewerSettingsStore } from '@/stores/viewer-settings-store'
import { renderWithQuery } from '@/test/test-utils'

import { ViewerDisplayControls } from '../ViewerDisplayControls'

describe('ViewerDisplayControls', () => {
  beforeEach(() => {
    act(() => {
      useViewerSettingsStore.getState().resetAdjustments()
    })
  })

  it('renders collapsed by default with no sliders or active badge', () => {
    renderWithQuery(<ViewerDisplayControls />)
    expect(screen.getByText('Display Settings')).toBeInTheDocument()
    expect(screen.queryByRole('slider')).not.toBeInTheDocument()
    expect(screen.queryByText('active')).not.toBeInTheDocument()
  })

  it('expands to show four sliders and a disabled Reset button', async () => {
    const user = userEvent.setup()
    renderWithQuery(<ViewerDisplayControls />)

    await user.click(screen.getByText('Display Settings'))

    expect(screen.getAllByRole('slider')).toHaveLength(4)
    const reset = screen.getByRole('button', { name: /reset/i })
    expect(reset).toBeDisabled()
  })

  it('updates the store and shows the active badge when a slider changes', async () => {
    const user = userEvent.setup()
    renderWithQuery(<ViewerDisplayControls />)

    await user.click(screen.getByText('Display Settings'))
    fireEvent.change(screen.getAllByRole('slider')[0], { target: { value: '0.5' } })

    expect(useViewerSettingsStore.getState().displayAdjustment.brightness).toBeCloseTo(0.5)
    expect(useViewerSettingsStore.getState().isActive).toBe(true)
    expect(screen.getByText('active')).toBeInTheDocument()
  })

  it('enables Reset once an adjustment is non-default and resets state when clicked', async () => {
    const user = userEvent.setup()
    renderWithQuery(<ViewerDisplayControls />)

    await user.click(screen.getByText('Display Settings'))
    fireEvent.change(screen.getAllByRole('slider')[1], { target: { value: '0.25' } })

    const reset = screen.getByRole('button', { name: /reset/i })
    expect(reset).toBeEnabled()

    await user.click(reset)

    expect(useViewerSettingsStore.getState().displayAdjustment.contrast).toBe(0)
    expect(useViewerSettingsStore.getState().isActive).toBe(false)
    expect(screen.queryByText('active')).not.toBeInTheDocument()
  })

  it('exposes Brightness, Contrast, Saturation, and Gamma slider ranges', async () => {
    const user = userEvent.setup()
    renderWithQuery(<ViewerDisplayControls />)

    await user.click(screen.getByText('Display Settings'))
    const sliders = screen.getAllByRole('slider') as HTMLInputElement[]

    expect(sliders[0]).toHaveAttribute('min', '-1')
    expect(sliders[0]).toHaveAttribute('max', '1')
    expect(sliders[3]).toHaveAttribute('min', '0.1')
    expect(sliders[3]).toHaveAttribute('max', '3')
  })
})
