import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useTransformState } from '@/stores'

import { FrameCropTool } from '../FrameCropTool'

vi.mock('react-image-crop/dist/ReactCrop.css', () => ({}))

vi.mock('react-image-crop', () => ({
  __esModule: true,
  default: ({
    children,
    crop,
    aspect,
    onChange,
    onComplete,
  }: {
    children: React.ReactNode
    crop?: { unit: string; x: number; y: number; width: number; height: number }
    aspect?: number
    onChange: (c: { unit: string; x: number; y: number; width: number; height: number }) => void
    onComplete: (c: { unit: string; x: number; y: number; width: number; height: number }) => void
  }) => (
    <div
      data-testid="react-crop"
      data-aspect={aspect == null ? 'none' : String(aspect)}
      data-crop={crop ? JSON.stringify(crop) : 'none'}
    >
      <button
        type="button"
        data-testid="trigger-change"
        onClick={() => onChange({ unit: 'px', x: 5, y: 6, width: 50, height: 60 })}
      />
      <button
        type="button"
        data-testid="trigger-complete"
        onClick={() => onComplete({ unit: 'px', x: 10.4, y: 20.6, width: 100.2, height: 80.5 })}
      />
      {children}
    </div>
  ),
}))

vi.mock('@/stores', () => ({
  useTransformState: vi.fn(),
}))

interface TransformStateMock {
  globalTransform:
    | {
        crop?: { x: number; y: number; width: number; height: number }
        resize?: { width: number; height: number }
      }
    | undefined
  setGlobalTransform: ReturnType<typeof vi.fn>
  setCameraTransform: ReturnType<typeof vi.fn>
}

let transformState: TransformStateMock

beforeEach(() => {
  transformState = {
    globalTransform: undefined,
    setGlobalTransform: vi.fn(),
    setCameraTransform: vi.fn(),
  }
  vi.mocked(useTransformState).mockReturnValue(
    transformState as unknown as ReturnType<typeof useTransformState>,
  )
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('FrameCropTool', () => {
  it('renders the crop area with the provided frame URL', () => {
    render(<FrameCropTool frameUrl="/api/frames/0" />)

    const img = screen.getByAltText('Frame to crop') as HTMLImageElement
    expect(img.getAttribute('src')).toBe('/api/frames/0')
    expect(screen.getByTestId('react-crop')).toBeInTheDocument()
  })

  it('renders Lock aspect ratio checkbox and Reset / Apply Crop buttons', () => {
    render(<FrameCropTool frameUrl="/api/frames/0" />)

    expect(screen.getByLabelText(/Lock aspect ratio/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Reset/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Apply Crop/i })).toBeInTheDocument()
  })

  it('disables Apply Crop until a completed crop exists', () => {
    render(<FrameCropTool frameUrl="/api/frames/0" />)

    const applyButton = screen.getByRole('button', { name: /Apply Crop/i })
    expect(applyButton).toBeDisabled()

    fireEvent.click(screen.getByTestId('trigger-complete'))

    expect(applyButton).not.toBeDisabled()
  })

  it('passes initial crop from globalTransform when present', () => {
    transformState.globalTransform = { crop: { x: 1, y: 2, width: 30, height: 40 } }
    vi.mocked(useTransformState).mockReturnValue(
      transformState as unknown as ReturnType<typeof useTransformState>,
    )

    render(<FrameCropTool frameUrl="/api/frames/0" />)

    const cropAttr = screen.getByTestId('react-crop').getAttribute('data-crop')
    expect(cropAttr).not.toBeNull()
    const parsed = JSON.parse(cropAttr as string)
    expect(parsed).toMatchObject({ x: 1, y: 2, width: 30, height: 40, unit: 'px' })
  })

  it('does not pass globalTransform crop when cameraName is provided', () => {
    transformState.globalTransform = { crop: { x: 1, y: 2, width: 30, height: 40 } }
    vi.mocked(useTransformState).mockReturnValue(
      transformState as unknown as ReturnType<typeof useTransformState>,
    )

    render(<FrameCropTool frameUrl="/api/frames/0" cameraName="top" />)

    expect(screen.getByTestId('react-crop').getAttribute('data-crop')).toBe('none')
  })

  it('updates the local crop state when ReactCrop fires onChange', () => {
    render(<FrameCropTool frameUrl="/api/frames/0" />)

    fireEvent.click(screen.getByTestId('trigger-change'))

    const cropAttr = screen.getByTestId('react-crop').getAttribute('data-crop')
    expect(cropAttr).not.toBeNull()
    expect(JSON.parse(cropAttr as string)).toMatchObject({ x: 5, y: 6, width: 50, height: 60 })
  })

  it('shows the selection size after a completed crop', () => {
    render(<FrameCropTool frameUrl="/api/frames/0" />)

    fireEvent.click(screen.getByTestId('trigger-complete'))

    expect(screen.getByText(/Selection: 100 × 81 px at \(10, 21\)/)).toBeInTheDocument()
  })

  it('applies a global crop with rounded coordinates when no cameraName is set', () => {
    const onCropApplied = vi.fn()
    render(<FrameCropTool frameUrl="/api/frames/0" onCropApplied={onCropApplied} />)

    fireEvent.click(screen.getByTestId('trigger-complete'))
    fireEvent.click(screen.getByRole('button', { name: /Apply Crop/i }))

    expect(transformState.setGlobalTransform).toHaveBeenCalledWith({
      crop: { x: 10, y: 21, width: 100, height: 81 },
    })
    expect(transformState.setCameraTransform).not.toHaveBeenCalled()
    expect(onCropApplied).toHaveBeenCalledWith({ x: 10, y: 21, width: 100, height: 81 })
  })

  it('applies a per-camera crop when cameraName is provided', () => {
    render(<FrameCropTool frameUrl="/api/frames/0" cameraName="top" />)

    fireEvent.click(screen.getByTestId('trigger-complete'))
    fireEvent.click(screen.getByRole('button', { name: /Apply Crop/i }))

    expect(transformState.setCameraTransform).toHaveBeenCalledWith('top', {
      crop: { x: 10, y: 21, width: 100, height: 81 },
    })
    expect(transformState.setGlobalTransform).not.toHaveBeenCalled()
  })

  it('preserves the existing resize when resetting the global transform', () => {
    transformState.globalTransform = { resize: { width: 640, height: 480 } }
    vi.mocked(useTransformState).mockReturnValue(
      transformState as unknown as ReturnType<typeof useTransformState>,
    )

    render(<FrameCropTool frameUrl="/api/frames/0" />)

    fireEvent.click(screen.getByRole('button', { name: /Reset/i }))

    expect(transformState.setGlobalTransform).toHaveBeenCalledWith({
      resize: { width: 640, height: 480 },
    })
  })

  it('clears the global transform on reset when no resize is set', () => {
    render(<FrameCropTool frameUrl="/api/frames/0" />)

    fireEvent.click(screen.getByRole('button', { name: /Reset/i }))

    expect(transformState.setGlobalTransform).toHaveBeenCalledWith(null)
  })

  it('clears the per-camera transform on reset when cameraName is provided', () => {
    render(<FrameCropTool frameUrl="/api/frames/0" cameraName="wrist" />)

    fireEvent.click(screen.getByRole('button', { name: /Reset/i }))

    expect(transformState.setCameraTransform).toHaveBeenCalledWith('wrist', null)
  })

  it('does not call setGlobalTransform when Apply Crop is clicked without a completed crop', () => {
    render(<FrameCropTool frameUrl="/api/frames/0" />)

    const apply = screen.getByRole('button', { name: /Apply Crop/i })
    expect(apply).toBeDisabled()
    fireEvent.click(apply)

    expect(transformState.setGlobalTransform).not.toHaveBeenCalled()
  })

  it('toggles aspect ratio lock and uses the image natural dimensions when locked', () => {
    render(<FrameCropTool frameUrl="/api/frames/0" />)

    const img = screen.getByAltText('Frame to crop') as HTMLImageElement
    Object.defineProperty(img, 'naturalWidth', { value: 800, configurable: true })
    Object.defineProperty(img, 'naturalHeight', { value: 400, configurable: true })

    expect(screen.getByTestId('react-crop').getAttribute('data-aspect')).toBe('none')

    fireEvent.click(screen.getByLabelText(/Lock aspect ratio/i))

    expect(screen.getByTestId('react-crop').getAttribute('data-aspect')).toBe('2')

    fireEvent.click(screen.getByLabelText(/Lock aspect ratio/i))

    expect(screen.getByTestId('react-crop').getAttribute('data-aspect')).toBe('none')
  })

  it('updates aspect ratio on image load when lock is active before the image loads', () => {
    render(<FrameCropTool frameUrl="/api/frames/0" />)

    const img = screen.getByAltText('Frame to crop') as HTMLImageElement
    Object.defineProperty(img, 'naturalWidth', { value: 1600, configurable: true })
    Object.defineProperty(img, 'naturalHeight', { value: 800, configurable: true })

    fireEvent.click(screen.getByLabelText(/Lock aspect ratio/i))
    fireEvent.load(img)

    expect(screen.getByTestId('react-crop').getAttribute('data-aspect')).toBe('2')
  })

  it('applies the className prop to the wrapper', () => {
    const { container } = render(
      <FrameCropTool frameUrl="/api/frames/0" className="custom-frame-crop" />,
    )

    expect(container.firstChild).toHaveClass('custom-frame-crop')
  })
})
