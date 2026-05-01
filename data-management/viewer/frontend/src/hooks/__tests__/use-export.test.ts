/**
 * Tests for useExport hook.
 *
 * Covers SSE-style streaming, abort, preview fetching, and reset behavior.
 * The underlying SSE connection is fully abstracted by createExportStream,
 * so we mock that and invoke the captured progress/complete/error callbacks
 * directly to drive state transitions deterministically.
 */

import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ExportPreviewStats, ExportRequestWithEdits } from '@/api/export'
import { useExport } from '@/hooks/use-export'
import type { ExportProgress, ExportResult } from '@/types'

const { mockCreateExportStream, mockGetExportPreview, mockCancel } = vi.hoisted(() => ({
  mockCreateExportStream: vi.fn(),
  mockGetExportPreview: vi.fn(),
  mockCancel: vi.fn(),
}))

vi.mock('@/api/export', () => ({
  createExportStream: mockCreateExportStream,
  getExportPreview: mockGetExportPreview,
  exportEpisodes: vi.fn(),
}))

interface CapturedCallbacks {
  onProgress: (p: ExportProgress) => void
  onComplete: (r: ExportResult) => void
  onError: (e: string) => void
}

function captureStreamCallbacks(): CapturedCallbacks {
  const calls = mockCreateExportStream.mock.calls
  const lastCall = calls[calls.length - 1]
  return {
    onProgress: lastCall[2],
    onComplete: lastCall[3],
    onError: lastCall[4],
  }
}

const sampleRequest: ExportRequestWithEdits = {
  episode_indices: [0, 1],
  format: 'lerobot',
} as unknown as ExportRequestWithEdits

const sampleProgress: ExportProgress = {
  current: 1,
  total: 2,
  status: 'processing',
} as unknown as ExportProgress

const sampleResult: ExportResult = {
  output_path: '/tmp/export.zip',
  episodes_exported: 2,
} as unknown as ExportResult

const samplePreview: ExportPreviewStats = {
  episodeCount: 2,
  totalFrames: 200,
} as unknown as ExportPreviewStats

describe('useExport', () => {
  beforeEach(() => {
    mockCreateExportStream.mockReset()
    mockGetExportPreview.mockReset()
    mockCancel.mockReset()
    mockCreateExportStream.mockReturnValue(mockCancel)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('initializes with empty state', () => {
    const { result } = renderHook(() => useExport({ datasetId: 'ds-1' }))

    expect(result.current.isExporting).toBe(false)
    expect(result.current.progress).toBeNull()
    expect(result.current.result).toBeNull()
    expect(result.current.error).toBeNull()
    expect(result.current.previewStats).toBeNull()
    expect(result.current.isLoadingPreview).toBe(false)
  })

  it('does not start export when datasetId is undefined', () => {
    const { result } = renderHook(() => useExport({ datasetId: undefined }))

    act(() => {
      result.current.startExport(sampleRequest)
    })

    expect(mockCreateExportStream).not.toHaveBeenCalled()
    expect(result.current.isExporting).toBe(false)
  })

  it('starts export and updates state via stream callbacks', () => {
    const { result } = renderHook(() => useExport({ datasetId: 'ds-1' }))

    act(() => {
      result.current.startExport(sampleRequest)
    })

    expect(mockCreateExportStream).toHaveBeenCalledWith(
      'ds-1',
      sampleRequest,
      expect.any(Function),
      expect.any(Function),
      expect.any(Function),
    )
    expect(result.current.isExporting).toBe(true)

    const { onProgress, onComplete } = captureStreamCallbacks()

    act(() => {
      onProgress(sampleProgress)
    })
    expect(result.current.progress).toEqual(sampleProgress)
    expect(result.current.isExporting).toBe(true)

    act(() => {
      onComplete(sampleResult)
    })
    expect(result.current.result).toEqual(sampleResult)
    expect(result.current.isExporting).toBe(false)
  })

  it('captures error from stream and clears exporting flag', () => {
    const { result } = renderHook(() => useExport({ datasetId: 'ds-1' }))

    act(() => {
      result.current.startExport(sampleRequest)
    })

    const { onError } = captureStreamCallbacks()

    act(() => {
      onError('boom')
    })

    expect(result.current.error).toBe('boom')
    expect(result.current.isExporting).toBe(false)
  })

  it('cancelExport invokes cancel ref and sets cancelled error', () => {
    const { result } = renderHook(() => useExport({ datasetId: 'ds-1' }))

    act(() => {
      result.current.startExport(sampleRequest)
    })

    act(() => {
      result.current.cancelExport()
    })

    expect(mockCancel).toHaveBeenCalledTimes(1)
    expect(result.current.isExporting).toBe(false)
    expect(result.current.error).toBe('Export cancelled')
  })

  it('cancelExport is a no-op when no export is in flight', () => {
    const { result } = renderHook(() => useExport({ datasetId: 'ds-1' }))

    act(() => {
      result.current.cancelExport()
    })

    expect(mockCancel).not.toHaveBeenCalled()
    expect(result.current.error).toBe('Export cancelled')
  })

  it('fetchPreview populates previewStats on success', async () => {
    mockGetExportPreview.mockResolvedValueOnce(samplePreview)
    const { result } = renderHook(() => useExport({ datasetId: 'ds-1' }))

    await act(async () => {
      await result.current.fetchPreview([0, 1], [5])
    })

    expect(mockGetExportPreview).toHaveBeenCalledWith('ds-1', [0, 1], [5])
    expect(result.current.previewStats).toEqual(samplePreview)
    expect(result.current.isLoadingPreview).toBe(false)
  })

  it('fetchPreview sets error and clears loading on failure', async () => {
    mockGetExportPreview.mockRejectedValueOnce(new Error('preview failed'))
    const { result } = renderHook(() => useExport({ datasetId: 'ds-1' }))

    await act(async () => {
      await result.current.fetchPreview([0])
    })

    await waitFor(() => {
      expect(result.current.error).toBe('preview failed')
    })
    expect(result.current.isLoadingPreview).toBe(false)
  })

  it('fetchPreview is a no-op when datasetId is undefined', async () => {
    const { result } = renderHook(() => useExport({ datasetId: undefined }))

    await act(async () => {
      await result.current.fetchPreview([0])
    })

    expect(mockGetExportPreview).not.toHaveBeenCalled()
  })

  it('reset clears progress, result, error, and preview', () => {
    const { result } = renderHook(() => useExport({ datasetId: 'ds-1' }))

    act(() => {
      result.current.startExport(sampleRequest)
    })
    const { onProgress, onError } = captureStreamCallbacks()
    act(() => {
      onProgress(sampleProgress)
      onError('oops')
    })

    expect(result.current.progress).not.toBeNull()
    expect(result.current.error).not.toBeNull()

    act(() => {
      result.current.reset()
    })

    expect(result.current.progress).toBeNull()
    expect(result.current.result).toBeNull()
    expect(result.current.error).toBeNull()
    expect(result.current.previewStats).toBeNull()
  })
})
