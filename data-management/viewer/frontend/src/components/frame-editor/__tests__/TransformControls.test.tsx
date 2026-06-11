import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useTransformState } from '@/stores'

import { TransformControls } from '../TransformControls'

vi.mock('@/stores', () => ({
  useTransformState: vi.fn(),
}))

const mockedUseTransformState = vi.mocked(useTransformState)

let setGlobalTransform: ReturnType<typeof vi.fn>
let setCameraTransform: ReturnType<typeof vi.fn>

function setup(globalTransform: unknown = null) {
  setGlobalTransform = vi.fn()
  setCameraTransform = vi.fn()
  mockedUseTransformState.mockReturnValue({
    globalTransform,
    setGlobalTransform,
    setCameraTransform,
  } as unknown as ReturnType<typeof useTransformState>)
}

beforeEach(() => {
  setup()
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('TransformControls', () => {
  it('renders width and height inputs and four preset buttons', () => {
    render(<TransformControls originalDimensions={{ width: 640, height: 480 }} />)

    expect(screen.getByLabelText('Width')).toBeInTheDocument()
    expect(screen.getByLabelText('Height')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '640×480' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '320×240' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '224×224' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '256×256' })).toBeInTheDocument()
  })

  it('uses originalDimensions for input placeholders when empty', () => {
    render(<TransformControls originalDimensions={{ width: 640, height: 480 }} />)

    expect(screen.getByPlaceholderText('640')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('480')).toBeInTheDocument()
  })

  it('falls back to placeholder text when originalDimensions is not provided', () => {
    render(<TransformControls />)

    expect(screen.getByPlaceholderText('Width')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Height')).toBeInTheDocument()
  })

  it('clicking a preset populates width and height inputs', async () => {
    const user = userEvent.setup()
    render(<TransformControls originalDimensions={{ width: 640, height: 480 }} />)

    await user.click(screen.getByRole('button', { name: '320×240' }))

    expect(screen.getByLabelText('Width')).toHaveValue(320)
    expect(screen.getByLabelText('Height')).toHaveValue(240)
  })

  it('width change auto-updates height when aspect ratio is locked', () => {
    render(<TransformControls originalDimensions={{ width: 640, height: 480 }} />)

    fireEvent.change(screen.getByLabelText('Width'), { target: { value: '800' } })

    expect(screen.getByLabelText('Width')).toHaveValue(800)
    expect(screen.getByLabelText('Height')).toHaveValue(600)
  })

  it('height change auto-updates width when aspect ratio is locked', () => {
    render(<TransformControls originalDimensions={{ width: 640, height: 480 }} />)

    fireEvent.change(screen.getByLabelText('Height'), { target: { value: '600' } })

    expect(screen.getByLabelText('Height')).toHaveValue(600)
    expect(screen.getByLabelText('Width')).toHaveValue(800)
  })

  it('does not auto-update height when aspect ratio is unlocked', async () => {
    const user = userEvent.setup()
    render(<TransformControls originalDimensions={{ width: 640, height: 480 }} />)

    await user.click(screen.getByTitle('Unlock aspect ratio'))
    expect(screen.getByTitle('Lock aspect ratio')).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Width'), { target: { value: '800' } })

    expect(screen.getByLabelText('Width')).toHaveValue(800)
    expect(screen.getByLabelText('Height')).toHaveValue(null)
  })

  it('Apply Resize button is disabled when no dimensions are set', () => {
    render(<TransformControls originalDimensions={{ width: 640, height: 480 }} />)

    expect(screen.getByRole('button', { name: /Apply Resize/i })).toBeDisabled()
  })

  it('Apply Resize calls setGlobalTransform with new resize dimensions when no cameraName', async () => {
    const user = userEvent.setup()
    render(<TransformControls originalDimensions={{ width: 640, height: 480 }} />)

    await user.click(screen.getByRole('button', { name: '320×240' }))
    await user.click(screen.getByRole('button', { name: /Apply Resize/i }))

    expect(setGlobalTransform).toHaveBeenCalledWith({
      resize: { width: 320, height: 240 },
    })
    expect(setCameraTransform).not.toHaveBeenCalled()
  })

  it('Apply Resize merges with existing globalTransform when no cameraName', async () => {
    setup({ crop: { x: 1, y: 2, width: 100, height: 100 } })
    const user = userEvent.setup()
    render(<TransformControls originalDimensions={{ width: 640, height: 480 }} />)

    await user.click(screen.getByRole('button', { name: '320×240' }))
    await user.click(screen.getByRole('button', { name: /Apply Resize/i }))

    expect(setGlobalTransform).toHaveBeenCalledWith({
      crop: { x: 1, y: 2, width: 100, height: 100 },
      resize: { width: 320, height: 240 },
    })
  })

  it('Apply Resize calls setCameraTransform when cameraName is provided', async () => {
    const user = userEvent.setup()
    render(<TransformControls originalDimensions={{ width: 640, height: 480 }} cameraName="top" />)

    await user.click(screen.getByRole('button', { name: '320×240' }))
    await user.click(screen.getByRole('button', { name: /Apply Resize/i }))

    expect(setCameraTransform).toHaveBeenCalledWith('top', {
      resize: { width: 320, height: 240 },
    })
    expect(setGlobalTransform).not.toHaveBeenCalled()
  })

  it('Apply Resize does nothing for invalid (zero) dimensions', () => {
    render(<TransformControls originalDimensions={{ width: 640, height: 480 }} />)

    fireEvent.change(screen.getByLabelText('Width'), { target: { value: '0' } })
    fireEvent.change(screen.getByLabelText('Height'), { target: { value: '0' } })

    const apply = screen.getByRole('button', { name: /Apply Resize/i })
    fireEvent.click(apply)

    expect(setGlobalTransform).not.toHaveBeenCalled()
    expect(setCameraTransform).not.toHaveBeenCalled()
  })

  it('Reset Size clears inputs and calls setGlobalTransform(null) when no crop or cameraName', async () => {
    const user = userEvent.setup()
    render(<TransformControls originalDimensions={{ width: 640, height: 480 }} />)

    await user.click(screen.getByRole('button', { name: '320×240' }))
    await user.click(screen.getByRole('button', { name: 'Reset Size' }))

    expect(screen.getByLabelText('Width')).toHaveValue(null)
    expect(screen.getByLabelText('Height')).toHaveValue(null)
    expect(setGlobalTransform).toHaveBeenCalledWith(null)
  })

  it('Reset Size preserves existing crop in globalTransform', async () => {
    setup({ crop: { x: 5, y: 10, width: 50, height: 60 } })
    const user = userEvent.setup()
    render(<TransformControls originalDimensions={{ width: 640, height: 480 }} />)

    await user.click(screen.getByRole('button', { name: 'Reset Size' }))

    expect(setGlobalTransform).toHaveBeenCalledWith({
      crop: { x: 5, y: 10, width: 50, height: 60 },
    })
  })

  it('Reset Size calls setCameraTransform(name, null) when cameraName is provided', async () => {
    const user = userEvent.setup()
    render(
      <TransformControls originalDimensions={{ width: 640, height: 480 }} cameraName="wrist" />,
    )

    await user.click(screen.getByRole('button', { name: 'Reset Size' }))

    expect(setCameraTransform).toHaveBeenCalledWith('wrist', null)
    expect(setGlobalTransform).not.toHaveBeenCalled()
  })

  it('does not render the current transform panel when globalTransform is null', () => {
    render(<TransformControls originalDimensions={{ width: 640, height: 480 }} />)

    expect(screen.queryByText('Current Transform:')).not.toBeInTheDocument()
  })

  it('renders the current transform crop summary when globalTransform.crop is present', () => {
    setup({ crop: { x: 10, y: 20, width: 320, height: 240 } })
    render(<TransformControls />)

    expect(screen.getByText('Current Transform:')).toBeInTheDocument()
    expect(screen.getByText(/Crop:\s*320×240\s*at\s*\(\s*10,\s*20\s*\)/)).toBeInTheDocument()
  })

  it('renders the current transform resize summary when globalTransform.resize is present', () => {
    setup({ resize: { width: 800, height: 600 } })
    render(<TransformControls />)

    expect(screen.getByText('Current Transform:')).toBeInTheDocument()
    expect(screen.getByText(/Resize:\s*800×600/)).toBeInTheDocument()
  })

  it('applies the className prop to the root element', () => {
    const { container } = render(<TransformControls className="my-extra-class" />)

    expect(container.firstChild).toHaveClass('my-extra-class')
  })
})
