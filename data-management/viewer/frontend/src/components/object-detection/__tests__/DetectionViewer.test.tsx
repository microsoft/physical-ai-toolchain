import { render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { Detection } from '@/types/detection'

import { DetectionViewer } from '../DetectionViewer'

interface MockCtx {
  drawImage: ReturnType<typeof vi.fn>
  strokeRect: ReturnType<typeof vi.fn>
  fillRect: ReturnType<typeof vi.fn>
  fillText: ReturnType<typeof vi.fn>
  measureText: ReturnType<typeof vi.fn>
  strokeStyleHistory: string[]
  fillStyleHistory: string[]
  globalAlphaHistory: number[]
  fontHistory: string[]
  lineWidthHistory: number[]
  _strokeStyle: string
  _fillStyle: string
  _globalAlpha: number
  _font: string
  _lineWidth: number
}

let ctxMock: MockCtx
let originalGetContext: typeof HTMLCanvasElement.prototype.getContext
let originalImage: typeof globalThis.Image
let originalClientWidth: PropertyDescriptor | undefined
let originalClientHeight: PropertyDescriptor | undefined
let pendingTimers: ReturnType<typeof setTimeout>[] = []

const createCtxMock = (): MockCtx => {
  const ctx = {
    drawImage: vi.fn(),
    strokeRect: vi.fn(),
    fillRect: vi.fn(),
    fillText: vi.fn(),
    measureText: vi.fn(() => ({ width: 50 })),
    strokeStyleHistory: [] as string[],
    fillStyleHistory: [] as string[],
    globalAlphaHistory: [] as number[],
    fontHistory: [] as string[],
    lineWidthHistory: [] as number[],
    _strokeStyle: '',
    _fillStyle: '',
    _globalAlpha: 1,
    _font: '',
    _lineWidth: 0,
  } as MockCtx

  Object.defineProperties(ctx, {
    strokeStyle: {
      get() {
        return this._strokeStyle
      },
      set(v: string) {
        this._strokeStyle = v
        this.strokeStyleHistory.push(v)
      },
    },
    fillStyle: {
      get() {
        return this._fillStyle
      },
      set(v: string) {
        this._fillStyle = v
        this.fillStyleHistory.push(v)
      },
    },
    globalAlpha: {
      get() {
        return this._globalAlpha
      },
      set(v: number) {
        this._globalAlpha = v
        this.globalAlphaHistory.push(v)
      },
    },
    font: {
      get() {
        return this._font
      },
      set(v: string) {
        this._font = v
        this.fontHistory.push(v)
      },
    },
    lineWidth: {
      get() {
        return this._lineWidth
      },
      set(v: number) {
        this._lineWidth = v
        this.lineWidthHistory.push(v)
      },
    },
  })

  return ctx
}

beforeEach(() => {
  ctxMock = createCtxMock()

  originalGetContext = HTMLCanvasElement.prototype.getContext
  HTMLCanvasElement.prototype.getContext = vi.fn(
    () => ctxMock,
  ) as unknown as typeof originalGetContext

  originalImage = globalThis.Image
  pendingTimers = []
  class MockImage {
    onload: (() => void) | null = null
    width = 200
    height = 100
    private _src = ''
    get src(): string {
      return this._src
    }
    set src(value: string) {
      this._src = value
      const timer = setTimeout(() => this.onload?.(), 0)
      pendingTimers.push(timer)
    }
  }
  globalThis.Image = MockImage as unknown as typeof globalThis.Image

  originalClientWidth = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientWidth')
  originalClientHeight = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientHeight')
  Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
    configurable: true,
    get: () => 400,
  })
  Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
    configurable: true,
    get: () => 300,
  })
})

afterEach(() => {
  pendingTimers.forEach(clearTimeout)
  pendingTimers = []
  HTMLCanvasElement.prototype.getContext = originalGetContext
  globalThis.Image = originalImage
  if (originalClientWidth) {
    Object.defineProperty(HTMLElement.prototype, 'clientWidth', originalClientWidth)
  } else {
    delete (HTMLElement.prototype as unknown as Record<string, unknown>).clientWidth
  }
  if (originalClientHeight) {
    Object.defineProperty(HTMLElement.prototype, 'clientHeight', originalClientHeight)
  } else {
    delete (HTMLElement.prototype as unknown as Record<string, unknown>).clientHeight
  }
  vi.restoreAllMocks()
})

const makeDetection = (overrides: Partial<Detection> = {}): Detection => ({
  class_id: 0,
  class_name: 'person',
  confidence: 0.95,
  bbox: [10, 20, 110, 120],
  ...overrides,
})

describe('DetectionViewer', () => {
  it('omits the detection count badge when detections is empty', () => {
    const { container } = render(<DetectionViewer imageUrl="/img.png" detections={[]} />)
    expect(container.textContent).not.toMatch(/detection/i)
  })

  it('renders a singular badge when exactly one detection is present', () => {
    const { getByText } = render(
      <DetectionViewer imageUrl="/img.png" detections={[makeDetection()]} />,
    )
    expect(getByText('1 detection')).toBeInTheDocument()
  })

  it('renders a plural badge when more than one detection is present', () => {
    const detections = [makeDetection(), makeDetection({ class_name: 'car' })]
    const { getByText } = render(<DetectionViewer imageUrl="/img.png" detections={detections} />)
    expect(getByText('2 detections')).toBeInTheDocument()
  })

  it('does not call canvas drawing APIs when imageUrl is null', async () => {
    render(<DetectionViewer imageUrl={null} detections={[makeDetection()]} />)
    await new Promise((resolve) => setTimeout(resolve, 5))
    expect(ctxMock.drawImage).not.toHaveBeenCalled()
    expect(ctxMock.strokeRect).not.toHaveBeenCalled()
  })

  it('draws the image and one strokeRect per detection after the image loads', async () => {
    const detections = [makeDetection(), makeDetection({ class_name: 'car', bbox: [0, 0, 50, 50] })]
    render(<DetectionViewer imageUrl="/img.png" detections={detections} />)
    await waitFor(() => expect(ctxMock.drawImage).toHaveBeenCalledTimes(1))
    expect(ctxMock.strokeRect).toHaveBeenCalledTimes(2)
  })

  it('draws labels when showLabels is true (default)', async () => {
    render(
      <DetectionViewer imageUrl="/img.png" detections={[makeDetection({ confidence: 0.95 })]} />,
    )
    await waitFor(() => expect(ctxMock.fillText).toHaveBeenCalled())
    expect(ctxMock.fillText).toHaveBeenCalledWith(
      'person 95%',
      expect.any(Number),
      expect.any(Number),
    )
    expect(ctxMock.measureText).toHaveBeenCalledWith('person 95%')
  })

  it('does not draw labels when showLabels is false', async () => {
    render(
      <DetectionViewer imageUrl="/img.png" detections={[makeDetection()]} showLabels={false} />,
    )
    await waitFor(() => expect(ctxMock.strokeRect).toHaveBeenCalled())
    expect(ctxMock.fillText).not.toHaveBeenCalled()
    expect(ctxMock.measureText).not.toHaveBeenCalled()
  })

  it('rounds confidence to nearest percent in the label', async () => {
    render(
      <DetectionViewer
        imageUrl="/img.png"
        detections={[makeDetection({ class_name: 'dog', confidence: 0.876 })]}
      />,
    )
    await waitFor(() => expect(ctxMock.fillText).toHaveBeenCalled())
    expect(ctxMock.fillText).toHaveBeenCalledWith('dog 88%', expect.any(Number), expect.any(Number))
  })

  it('uses the class palette color for known class names', async () => {
    render(
      <DetectionViewer imageUrl="/img.png" detections={[makeDetection({ class_name: 'car' })]} />,
    )
    await waitFor(() => expect(ctxMock.strokeRect).toHaveBeenCalled())
    expect(ctxMock.strokeStyleHistory).toContain('#4ECDC4')
  })

  it('falls back to the default color for unknown class names', async () => {
    render(
      <DetectionViewer
        imageUrl="/img.png"
        detections={[makeDetection({ class_name: 'spaceship' })]}
      />,
    )
    await waitFor(() => expect(ctxMock.strokeRect).toHaveBeenCalled())
    expect(ctxMock.strokeStyleHistory).toContain('#74B9FF')
  })

  it('honors the boxOpacity prop when stroking the bounding box', async () => {
    render(<DetectionViewer imageUrl="/img.png" detections={[makeDetection()]} boxOpacity={0.4} />)
    await waitFor(() => expect(ctxMock.strokeRect).toHaveBeenCalled())
    expect(ctxMock.globalAlphaHistory).toContain(0.4)
  })

  it('sets the 12px sans-serif font and lineWidth=2 while drawing', async () => {
    render(<DetectionViewer imageUrl="/img.png" detections={[makeDetection()]} />)
    await waitFor(() => expect(ctxMock.fillText).toHaveBeenCalled())
    expect(ctxMock.lineWidthHistory).toContain(2)
    expect(ctxMock.fontHistory).toContain('12px sans-serif')
  })

  it('redraws when the imageUrl prop changes', async () => {
    const { rerender } = render(
      <DetectionViewer imageUrl="/img1.png" detections={[makeDetection()]} />,
    )
    await waitFor(() => expect(ctxMock.drawImage).toHaveBeenCalledTimes(1))
    rerender(<DetectionViewer imageUrl="/img2.png" detections={[makeDetection()]} />)
    await waitFor(() => expect(ctxMock.drawImage).toHaveBeenCalledTimes(2))
  })

  it('redraws when the detections array changes', async () => {
    const { rerender } = render(
      <DetectionViewer imageUrl="/img.png" detections={[makeDetection()]} />,
    )
    await waitFor(() => expect(ctxMock.strokeRect).toHaveBeenCalledTimes(1))
    rerender(
      <DetectionViewer
        imageUrl="/img.png"
        detections={[makeDetection(), makeDetection({ class_name: 'car' })]}
      />,
    )
    await waitFor(() => expect(ctxMock.strokeRect).toHaveBeenCalledTimes(3))
  })
})
