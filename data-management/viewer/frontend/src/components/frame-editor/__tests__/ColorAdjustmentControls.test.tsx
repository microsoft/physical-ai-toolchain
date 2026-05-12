import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useTransformState } from '@/stores'

import { ColorAdjustmentControls } from '../ColorAdjustmentControls'

vi.mock('@/stores', () => ({
  useTransformState: vi.fn(),
}))

const mockedUseTransformState = vi.mocked(useTransformState)

interface TransformState {
  globalTransform: unknown
  setGlobalTransform: ReturnType<typeof vi.fn>
  setCameraTransform: ReturnType<typeof vi.fn>
}

function setup(overrides: Partial<TransformState> = {}) {
  const setGlobalTransform = vi.fn()
  const setCameraTransform = vi.fn()
  const state: TransformState = {
    globalTransform: undefined,
    setGlobalTransform,
    setCameraTransform,
    ...overrides,
  }
  mockedUseTransformState.mockReturnValue(state as unknown as ReturnType<typeof useTransformState>)
  return { setGlobalTransform, setCameraTransform }
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('ColorAdjustmentControls', () => {
  it('renders 5 adjustment sliders', () => {
    setup()
    render(<ColorAdjustmentControls />)

    expect(screen.getAllByRole('slider')).toHaveLength(5)
  })

  it('renders all slider labels', () => {
    setup()
    render(<ColorAdjustmentControls />)

    expect(screen.getByText('Brightness')).toBeInTheDocument()
    expect(screen.getByText('Contrast')).toBeInTheDocument()
    expect(screen.getByText('Saturation')).toBeInTheDocument()
    expect(screen.getByText('Gamma')).toBeInTheDocument()
    expect(screen.getByText('Hue')).toBeInTheDocument()
  })

  it('renders 6 filter preset buttons', () => {
    setup()
    render(<ColorAdjustmentControls />)

    expect(screen.getByRole('button', { name: 'None' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Grayscale' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Sepia' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Invert' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Warm' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Cool' })).toBeInTheDocument()
  })

  it('renders default formatted values', () => {
    setup()
    render(<ColorAdjustmentControls />)

    expect(screen.getAllByText('0%')).toHaveLength(3)
    expect(screen.getByText('1.0')).toBeInTheDocument()
    expect(screen.getByText('0°')).toBeInTheDocument()
  })

  it('disables the Reset button when no adjustments are made', () => {
    setup()
    render(<ColorAdjustmentControls />)

    expect(screen.getByRole('button', { name: /reset/i })).toBeDisabled()
  })

  it('calls setGlobalTransform with brightness adjustment when slider changes', () => {
    const { setGlobalTransform } = setup()
    render(<ColorAdjustmentControls />)

    const sliders = screen.getAllByRole('slider')
    fireEvent.change(sliders[0], { target: { value: '0.5' } })

    expect(setGlobalTransform).toHaveBeenCalledWith(
      expect.objectContaining({
        colorAdjustment: expect.objectContaining({ brightness: 0.5 }),
      }),
    )
  })

  it('calls setGlobalTransform with contrast adjustment when slider changes', () => {
    const { setGlobalTransform } = setup()
    render(<ColorAdjustmentControls />)

    const sliders = screen.getAllByRole('slider')
    fireEvent.change(sliders[1], { target: { value: '-0.5' } })

    expect(setGlobalTransform).toHaveBeenCalledWith(
      expect.objectContaining({
        colorAdjustment: expect.objectContaining({ contrast: -0.5 }),
      }),
    )
  })

  it('calls setGlobalTransform with saturation adjustment when slider changes', () => {
    const { setGlobalTransform } = setup()
    render(<ColorAdjustmentControls />)

    const sliders = screen.getAllByRole('slider')
    fireEvent.change(sliders[2], { target: { value: '0.75' } })

    expect(setGlobalTransform).toHaveBeenCalledWith(
      expect.objectContaining({
        colorAdjustment: expect.objectContaining({ saturation: 0.75 }),
      }),
    )
  })

  it('calls setGlobalTransform with gamma adjustment when slider changes', () => {
    const { setGlobalTransform } = setup()
    render(<ColorAdjustmentControls />)

    const sliders = screen.getAllByRole('slider')
    fireEvent.change(sliders[3], { target: { value: '2' } })

    expect(setGlobalTransform).toHaveBeenCalledWith(
      expect.objectContaining({
        colorAdjustment: expect.objectContaining({ gamma: 2 }),
      }),
    )
  })

  it('calls setGlobalTransform with hue adjustment when slider changes', () => {
    const { setGlobalTransform } = setup()
    render(<ColorAdjustmentControls />)

    const sliders = screen.getAllByRole('slider')
    fireEvent.change(sliders[4], { target: { value: '90' } })

    expect(setGlobalTransform).toHaveBeenCalledWith(
      expect.objectContaining({
        colorAdjustment: expect.objectContaining({ hue: 90 }),
      }),
    )
  })

  it('routes adjustments to setCameraTransform when cameraName is provided', () => {
    const { setGlobalTransform, setCameraTransform } = setup()
    render(<ColorAdjustmentControls cameraName="top" />)

    const sliders = screen.getAllByRole('slider')
    fireEvent.change(sliders[0], { target: { value: '0.5' } })

    expect(setCameraTransform).toHaveBeenCalledWith(
      'top',
      expect.objectContaining({
        colorAdjustment: expect.objectContaining({ brightness: 0.5 }),
      }),
    )
    expect(setGlobalTransform).not.toHaveBeenCalled()
  })

  it('applies the selected filter preset when clicked', async () => {
    const user = userEvent.setup()
    const { setGlobalTransform } = setup()
    render(<ColorAdjustmentControls />)

    await user.click(screen.getByRole('button', { name: 'Grayscale' }))

    expect(setGlobalTransform).toHaveBeenCalledWith(
      expect.objectContaining({ colorFilter: 'grayscale' }),
    )
  })

  it('clears state to null when None preset is clicked from a filter-only state', async () => {
    const user = userEvent.setup()
    const { setGlobalTransform } = setup({
      globalTransform: { colorFilter: 'sepia' },
    })
    render(<ColorAdjustmentControls />)

    await user.click(screen.getByRole('button', { name: 'None' }))

    expect(setGlobalTransform).toHaveBeenLastCalledWith(null)
  })

  it('renders Active Color Settings panel with adjustment values', () => {
    setup({
      globalTransform: {
        colorAdjustment: {
          brightness: 0.25,
          contrast: -0.1,
          saturation: 0.5,
          gamma: 1.5,
          hue: 45,
        },
      },
    })
    render(<ColorAdjustmentControls />)

    expect(screen.getByText('Active Color Settings:')).toBeInTheDocument()
    expect(screen.getByText('Brightness: 25%')).toBeInTheDocument()
    expect(screen.getByText('Contrast: -10%')).toBeInTheDocument()
    expect(screen.getByText('Saturation: 50%')).toBeInTheDocument()
    expect(screen.getByText('Gamma: 1.5')).toBeInTheDocument()
    expect(screen.getByText('Hue: 45°')).toBeInTheDocument()
  })

  it('renders Active Color Settings filter row when colorFilter is set', () => {
    setup({
      globalTransform: { colorFilter: 'sepia' },
    })
    render(<ColorAdjustmentControls />)

    expect(screen.getByText('Active Color Settings:')).toBeInTheDocument()
    expect(screen.getByText('Filter: sepia')).toBeInTheDocument()
  })

  it('hides Active Color Settings panel when no transform is set', () => {
    setup()
    render(<ColorAdjustmentControls />)

    expect(screen.queryByText('Active Color Settings:')).not.toBeInTheDocument()
  })

  it('Reset clears global transform to null when no resize or crop exists', async () => {
    const user = userEvent.setup()
    const { setGlobalTransform } = setup({
      globalTransform: {
        colorAdjustment: { brightness: 0.5 },
      },
    })
    render(<ColorAdjustmentControls />)

    const resetButton = screen.getByRole('button', { name: /reset/i })
    expect(resetButton).not.toBeDisabled()

    await user.click(resetButton)

    expect(setGlobalTransform).toHaveBeenLastCalledWith(null)
  })

  it('preserves existing crop and resize when applying a color adjustment', () => {
    const existingCrop = { x: 10, y: 20, width: 100, height: 100 }
    const existingResize = { width: 800, height: 600 }
    const { setGlobalTransform } = setup({
      globalTransform: { crop: existingCrop, resize: existingResize },
    })
    render(<ColorAdjustmentControls />)

    const sliders = screen.getAllByRole('slider')
    fireEvent.change(sliders[0], { target: { value: '0.5' } })

    expect(setGlobalTransform).toHaveBeenCalledWith(
      expect.objectContaining({
        crop: existingCrop,
        resize: existingResize,
        colorAdjustment: expect.objectContaining({ brightness: 0.5 }),
      }),
    )
  })

  it('applies className prop to the outer container', () => {
    setup()
    const { container } = render(<ColorAdjustmentControls className="custom-class" />)

    expect(container.firstChild).toHaveClass('custom-class')
  })
})
