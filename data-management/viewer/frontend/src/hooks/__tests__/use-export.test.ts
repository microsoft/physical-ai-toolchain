import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useExport } from '@/hooks/use-export'

vi.mock('@/api/export', () => ({
  createExportStream: vi.fn(),
  getExportPreview: vi.fn(),
}))

import { createExportStream, getExportPreview } from '@/api/export'

const mockedCreateStream = vi.mocked(createExportStream)
const mockedPreview = vi.mocked(getExportPreview)

describe('useExport', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('startExport is a no-op without a datasetId', () => {
    const { result } = renderHook(() => useExport({ datasetId: undefined }))
    act(() => {
      result.current.startExport({
        format: 'lerobot',
        episodes: [],
        edits: { removed_frames: {}, edited_annotations: {} },
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
      } as any)
    })
    expect(mockedCreateStream).not.toHaveBeenCalled()
    expect(result.current.isExporting).toBe(false)
  })

  it('streams progress and result via createExportStream', async () => {
    let progressCb: ((p: unknown) => void) | undefined
    let completeCb: ((r: unknown) => void) | undefined
    mockedCreateStream.mockImplementation((_d, _r, onProgress, onComplete) => {
      progressCb = onProgress as (p: unknown) => void
      completeCb = onComplete as (r: unknown) => void
      return () => {}
    })

    const { result } = renderHook(() => useExport({ datasetId: 'ds' }))

    act(() => {
      result.current.startExport({
        format: 'lerobot',
        episodes: [0, 1],
        edits: { removed_frames: {}, edited_annotations: {} },
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
      } as any)
    })

    expect(result.current.isExporting).toBe(true)
    expect(mockedCreateStream).toHaveBeenCalledTimes(1)

    act(() => {
      progressCb?.({ stage: 'processing', percent: 50 })
    })
    expect(result.current.progress).toEqual({ stage: 'processing', percent: 50 })

    act(() => {
      completeCb?.({ downloadUrl: 'https://example.com/out.zip' })
    })
    expect(result.current.isExporting).toBe(false)
    expect(result.current.result).toEqual({ downloadUrl: 'https://example.com/out.zip' })
  })

  it('reports errors from the export stream', () => {
    let errorCb: ((e: string) => void) | undefined
    mockedCreateStream.mockImplementation((_d, _r, _p, _c, onError) => {
      errorCb = onError
      return () => {}
    })

    const { result } = renderHook(() => useExport({ datasetId: 'ds' }))

    act(() => {
      result.current.startExport({
        format: 'lerobot',
        episodes: [0],
        edits: { removed_frames: {}, edited_annotations: {} },
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
      } as any)
    })

    act(() => {
      errorCb?.('boom')
    })

    expect(result.current.isExporting).toBe(false)
    expect(result.current.error).toBe('boom')
  })

  it('cancelExport cancels the active stream and sets cancellation message', () => {
    const cancel = vi.fn()
    mockedCreateStream.mockReturnValue(cancel)

    const { result } = renderHook(() => useExport({ datasetId: 'ds' }))

    act(() => {
      result.current.startExport({
        format: 'lerobot',
        episodes: [0],
        edits: { removed_frames: {}, edited_annotations: {} },
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
      } as any)
    })

    act(() => {
      result.current.cancelExport()
    })

    expect(cancel).toHaveBeenCalledTimes(1)
    expect(result.current.isExporting).toBe(false)
    expect(result.current.error).toBe('Export cancelled')
  })

  it('fetchPreview populates previewStats on success', async () => {
    mockedPreview.mockResolvedValueOnce({
      total_episodes: 2,
      total_frames: 100,
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any)

    const { result } = renderHook(() => useExport({ datasetId: 'ds' }))

    await act(async () => {
      await result.current.fetchPreview([0, 1])
    })

    expect(mockedPreview).toHaveBeenCalledWith('ds', [0, 1], undefined)
    expect(result.current.previewStats).toEqual({ total_episodes: 2, total_frames: 100 })
    expect(result.current.isLoadingPreview).toBe(false)
  })

  it('fetchPreview captures error messages from rejection', async () => {
    mockedPreview.mockRejectedValueOnce(new Error('preview failed'))

    const { result } = renderHook(() => useExport({ datasetId: 'ds' }))

    await act(async () => {
      await result.current.fetchPreview([0])
    })

    await waitFor(() => expect(result.current.isLoadingPreview).toBe(false))
    expect(result.current.error).toBe('preview failed')
  })

  it('fetchPreview is a no-op without datasetId', async () => {
    const { result } = renderHook(() => useExport({ datasetId: undefined }))
    await act(async () => {
      await result.current.fetchPreview([0])
    })
    expect(mockedPreview).not.toHaveBeenCalled()
  })

  it('reset clears progress, result, error, and previewStats', async () => {
    let errorCb: ((e: string) => void) | undefined
    mockedCreateStream.mockImplementation((_d, _r, _p, _c, onError) => {
      errorCb = onError
      return () => {}
    })

    const { result } = renderHook(() => useExport({ datasetId: 'ds' }))

    act(() => {
      result.current.startExport({
        format: 'lerobot',
        episodes: [0],
        edits: { removed_frames: {}, edited_annotations: {} },
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
      } as any)
    })
    act(() => {
      errorCb?.('failed')
    })

    expect(result.current.error).toBe('failed')

    act(() => {
      result.current.reset()
    })

    expect(result.current.progress).toBeNull()
    expect(result.current.result).toBeNull()
    expect(result.current.error).toBeNull()
    expect(result.current.previewStats).toBeNull()
  })
})
