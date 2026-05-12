import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { FramePreview } from '../FramePreview'

vi.mock('@/lib/css-filters', () => ({
  buildCssFilter: vi.fn(() => 'brightness(1.2)'),
}))

import { buildCssFilter } from '@/lib/css-filters'

interface MockImageInstance {
  src: string
  crossOrigin: string | null
  onload: (() => void) | null
  onerror: (() => void) | null
  naturalWidth: number
  naturalHeight: number
}

const imageInstances: MockImageInstance[] = []

class MockImage {
  src = ''
  crossOrigin: string | null = null
  onload: (() => void) | null = null
  onerror: (() => void) | null = null
  naturalWidth = 1920
  naturalHeight = 1080

  constructor() {
    imageInstances.push(this)
  }
}

const drawImageMock = vi.fn()
const getContextMock = vi.fn(() => ({ drawImage: drawImageMock }))

beforeEach(() => {
  imageInstances.length = 0
  drawImageMock.mockClear()
  getContextMock.mockClear()
  vi.mocked(buildCssFilter).mockClear()
  vi.mocked(buildCssFilter).mockReturnValue('brightness(1.2)')

  vi.stubGlobal('Image', MockImage)
  HTMLCanvasElement.prototype.getContext = getContextMock as never
})

afterEach(() => {
  vi.unstubAllGlobals()
})

const latestImage = (): MockImageInstance => {
  const img = imageInstances[imageInstances.length - 1]
  if (!img) throw new Error('No Image instance created')
  return img
}

describe('FramePreview', () => {
  it('renders loading overlay before image loads', () => {
    render(<FramePreview frameUrl="/api/frames/0" />)
    expect(screen.getByText('Loading...')).toBeInTheDocument()
  })

  it('sets image src and crossOrigin on mount', () => {
    render(<FramePreview frameUrl="/api/frames/42" />)
    const img = latestImage()
    expect(img.src).toBe('/api/frames/42')
    expect(img.crossOrigin).toBe('anonymous')
  })

  it('hides loading and shows dimensions after image loads', () => {
    render(<FramePreview frameUrl="/api/frames/0" />)
    const img = latestImage()
    img.naturalWidth = 800
    img.naturalHeight = 600
    act(() => {
      img.onload?.()
    })
    expect(screen.queryByText('Loading...')).not.toBeInTheDocument()
    expect(screen.getByText(/Original: 800 × 600/)).toBeInTheDocument()
    expect(screen.getByText(/Output: 800 × 600/)).toBeInTheDocument()
  })

  it('shows error overlay when image fails to load', () => {
    render(<FramePreview frameUrl="/bad" />)
    const img = latestImage()
    act(() => {
      img.onerror?.()
    })
    expect(screen.getByText('Failed to load frame')).toBeInTheDocument()
  })

  it('passes transform color settings to buildCssFilter', () => {
    const transform = {
      colorAdjustment: { brightness: 1.5, contrast: 1, saturation: 1 },
      colorFilter: 'sepia' as const,
    }
    render(<FramePreview frameUrl="/api/frames/0" transform={transform} />)
    expect(buildCssFilter).toHaveBeenCalledWith(transform.colorAdjustment, transform.colorFilter)
  })

  it('calls buildCssFilter with undefined values when no transform', () => {
    render(<FramePreview frameUrl="/api/frames/0" />)
    expect(buildCssFilter).toHaveBeenCalledWith(undefined, undefined)
  })

  it('hides dimensions footer when showDimensions is false', () => {
    render(<FramePreview frameUrl="/api/frames/0" showDimensions={false} />)
    const img = latestImage()
    act(() => {
      img.onload?.()
    })
    expect(screen.queryByText(/Original:/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Output:/)).not.toBeInTheDocument()
  })

  it('applies crop transform to source region', () => {
    const transform = {
      crop: { x: 100, y: 50, width: 400, height: 300 },
    }
    render(<FramePreview frameUrl="/api/frames/0" transform={transform} />)
    const img = latestImage()
    act(() => {
      img.onload?.()
    })
    expect(drawImageMock).toHaveBeenCalled()
    const args = drawImageMock.mock.calls[0]
    expect(args[1]).toBe(100)
    expect(args[2]).toBe(50)
    expect(args[3]).toBe(400)
    expect(args[4]).toBe(300)
  })

  it('uses resize dimensions for output sizing', () => {
    const transform = {
      resize: { width: 320, height: 240 },
    }
    render(
      <FramePreview
        frameUrl="/api/frames/0"
        transform={transform}
        maxWidth={400}
        maxHeight={400}
      />,
    )
    const img = latestImage()
    act(() => {
      img.onload?.()
    })
    expect(screen.getByText(/Output: 320 × 240/)).toBeInTheDocument()
  })

  it('shows Cropped indicator when crop transform is active', () => {
    render(
      <FramePreview
        frameUrl="/api/frames/0"
        transform={{ crop: { x: 0, y: 0, width: 100, height: 100 } }}
      />,
    )
    const img = latestImage()
    act(() => {
      img.onload?.()
    })
    expect(screen.getByText('Cropped')).toBeInTheDocument()
  })

  it('shows Resized indicator when resize transform is active', () => {
    render(
      <FramePreview frameUrl="/api/frames/0" transform={{ resize: { width: 200, height: 200 } }} />,
    )
    const img = latestImage()
    act(() => {
      img.onload?.()
    })
    expect(screen.getByText('Resized')).toBeInTheDocument()
  })

  it('shows Color adjusted indicator when color settings present', () => {
    render(
      <FramePreview
        frameUrl="/api/frames/0"
        transform={{ colorAdjustment: { brightness: 1.2, contrast: 1, saturation: 1 } }}
      />,
    )
    const img = latestImage()
    act(() => {
      img.onload?.()
    })
    expect(screen.getByText('Color adjusted')).toBeInTheDocument()
  })

  it('applies custom className to wrapper', () => {
    const { container } = render(<FramePreview frameUrl="/api/frames/0" className="custom-class" />)
    expect(container.firstChild).toHaveClass('custom-class')
  })
})
