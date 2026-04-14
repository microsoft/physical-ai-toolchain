import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ExportRequestWithEdits } from '@/api/export'
import type { ExportProgress, ExportResult } from '@/types'

const mockCreateExportStream = vi.fn()
const mockGetExportPreview = vi.fn()

vi.mock('@/api/export', () => ({
  createExportStream: mockCreateExportStream,
  getExportPreview: mockGetExportPreview,
}))

const { useExport } = await import('../use-export')

describe('useExport', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('initializes with default state', () => {
    const { result } = renderHook(() => useExport({ datasetId: 'ds-1' }))

    expect(result.current.isExporting).toBe(false)
    expect(result.current.progress).toBeNull()
    expect(result.current.result).toBeNull()
    expect(result.current.error).toBeNull()
    expect(result.current.previewStats).toBeNull()
    expect(result.current.isLoadingPreview).toBe(false)
  })

  it('does not start export when datasetId is undefined', async () => {
    const { result } = renderHook(() => useExport({ datasetId: undefined }))

    await act(async () => {
      await result.current.startExport({ episodes: [0, 1] } as unknown as ExportRequestWithEdits)
    })

    expect(mockCreateExportStream).not.toHaveBeenCalled()
  })

  it('starts export and receives progress callbacks', async () => {
    mockCreateExportStream.mockImplementation(
      (
        _datasetId: string,
        _request: ExportRequestWithEdits,
        onProgress: (p: ExportProgress) => void,
        onResult: (r: ExportResult) => void,
      ) => {
        onProgress({
          currentEpisode: 5,
          totalEpisodes: 10,
          currentFrame: 0,
          totalFrames: 100,
          percentage: 50,
          status: 'Processing',
        })
        onResult({
          success: true,
          outputFiles: ['/export/result.zip'],
          stats: { totalEpisodes: 10, totalFrames: 100, removedFrames: 0, durationMs: 500 },
        })
        return vi.fn()
      },
    )

    const { result } = renderHook(() => useExport({ datasetId: 'ds-1' }))

    await act(async () => {
      await result.current.startExport({ episodes: [0, 1] } as unknown as ExportRequestWithEdits)
    })

    expect(mockCreateExportStream).toHaveBeenCalledWith(
      'ds-1',
      { episodes: [0, 1] },
      expect.any(Function),
      expect.any(Function),
      expect.any(Function),
    )
    expect(result.current.progress).toEqual({
      currentEpisode: 5,
      totalEpisodes: 10,
      currentFrame: 0,
      totalFrames: 100,
      percentage: 50,
      status: 'Processing',
    })
    expect(result.current.result).toEqual({
      success: true,
      outputFiles: ['/export/result.zip'],
      stats: { totalEpisodes: 10, totalFrames: 100, removedFrames: 0, durationMs: 500 },
    })
  })

  it('handles export error callback', async () => {
    mockCreateExportStream.mockImplementation(
      (
        _datasetId: string,
        _request: ExportRequestWithEdits,
        _onProgress: (p: ExportProgress) => void,
        _onResult: (r: ExportResult) => void,
        onError: (e: string) => void,
      ) => {
        onError('Export failed')
        return vi.fn()
      },
    )

    const { result } = renderHook(() => useExport({ datasetId: 'ds-1' }))

    await act(async () => {
      await result.current.startExport({ episodes: [0] } as unknown as ExportRequestWithEdits)
    })

    expect(result.current.error).toBe('Export failed')
    expect(result.current.isExporting).toBe(false)
  })

  it('cancels an active export', async () => {
    const cancelFn = vi.fn()
    mockCreateExportStream.mockReturnValue(cancelFn)

    const { result } = renderHook(() => useExport({ datasetId: 'ds-1' }))

    await act(async () => {
      await result.current.startExport({ episodes: [0] } as unknown as ExportRequestWithEdits)
    })

    act(() => {
      result.current.cancelExport()
    })

    expect(cancelFn).toHaveBeenCalled()
    expect(result.current.isExporting).toBe(false)
    expect(result.current.error).toBe('Export cancelled')
  })

  it('fetches export preview', async () => {
    mockGetExportPreview.mockResolvedValue({
      totalFrames: 1000,
      estimatedSize: '50MB',
    })

    const { result } = renderHook(() => useExport({ datasetId: 'ds-1' }))

    await act(async () => {
      await result.current.fetchPreview([0, 1, 2])
    })

    expect(mockGetExportPreview).toHaveBeenCalledWith('ds-1', [0, 1, 2], undefined)
    expect(result.current.previewStats).toEqual({
      totalFrames: 1000,
      estimatedSize: '50MB',
    })
  })

  it('fetches preview with removedFrames', async () => {
    mockGetExportPreview.mockResolvedValue({ totalFrames: 800 })

    const { result } = renderHook(() => useExport({ datasetId: 'ds-1' }))
    const removedFrames = [1, 2]

    await act(async () => {
      await result.current.fetchPreview([0], removedFrames)
    })

    expect(mockGetExportPreview).toHaveBeenCalledWith('ds-1', [0], removedFrames)
  })

  it('does not fetch preview when datasetId is undefined', async () => {
    const { result } = renderHook(() => useExport({ datasetId: undefined }))

    await act(async () => {
      await result.current.fetchPreview([0])
    })

    expect(mockGetExportPreview).not.toHaveBeenCalled()
  })

  it('resets state', async () => {
    mockCreateExportStream.mockImplementation(
      (
        _ds: string,
        _req: ExportRequestWithEdits,
        onProgress: (p: ExportProgress) => void,
        onResult: (r: ExportResult) => void,
      ) => {
        onProgress({
          currentEpisode: 5,
          totalEpisodes: 10,
          currentFrame: 0,
          totalFrames: 100,
          percentage: 50,
          status: 'Processing',
        })
        onResult({
          success: true,
          outputFiles: ['/export.zip'],
          stats: { totalEpisodes: 10, totalFrames: 100, removedFrames: 0, durationMs: 300 },
        })
        return vi.fn()
      },
    )

    const { result } = renderHook(() => useExport({ datasetId: 'ds-1' }))

    await act(async () => {
      await result.current.startExport({ episodes: [0] } as unknown as ExportRequestWithEdits)
    })

    act(() => {
      result.current.reset()
    })

    expect(result.current.progress).toBeNull()
    expect(result.current.result).toBeNull()
    expect(result.current.error).toBeNull()
    expect(result.current.previewStats).toBeNull()
  })
})
