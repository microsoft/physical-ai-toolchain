import { useCallback, useRef, useState } from 'react'

import {
  createExportStream,
  type ExportPreviewStats,
  type ExportRequestWithEdits,
  getExportPreview,
} from '@/api/export'
import type { ExportProgress, ExportResult } from '@/types'

interface UseExportOptions {
  datasetId: string | undefined
}

interface UseExportReturn {
  isExporting: boolean
  progress: ExportProgress | null
  result: ExportResult | null
  error: string | null
  previewStats: ExportPreviewStats | null
  isLoadingPreview: boolean
  startExport: (request: ExportRequestWithEdits) => void
  cancelExport: () => void
  fetchPreview: (episodeIndices: number[], removedFrames?: number[]) => Promise<void>
  reset: () => void
}

/**
 * Hook for managing export operations with SSE progress streaming
 */
export function useExport({ datasetId }: UseExportOptions): UseExportReturn {
  const [isExporting, setIsExporting] = useState(false)
  const [progress, setProgress] = useState<ExportProgress | null>(null)
  const [result, setResult] = useState<ExportResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [previewStats, setPreviewStats] = useState<ExportPreviewStats | null>(null)
  const [isLoadingPreview, setIsLoadingPreview] = useState(false)

  const cancelRef = useRef<(() => void) | null>(null)

  const startExport = useCallback(
    (request: ExportRequestWithEdits) => {
      if (!datasetId) return

      setIsExporting(true)
      setProgress(null)
      setResult(null)
      setError(null)

      const cancel = createExportStream(
        datasetId,
        request,
        (prog) => setProgress(prog),
        (res) => {
          setResult(res)
          setIsExporting(false)
        },
        (err) => {
          setError(err)
          setIsExporting(false)
        },
      )

      cancelRef.current = cancel
    },
    [datasetId],
  )

  const cancelExport = useCallback(() => {
    cancelRef.current?.()
    cancelRef.current = null
    setIsExporting(false)
    setError('Export cancelled')
  }, [])

  const fetchPreview = useCallback(
    async (episodeIndices: number[], removedFrames?: number[]) => {
      if (!datasetId) return

      setIsLoadingPreview(true)
      try {
        const stats = await getExportPreview(datasetId, episodeIndices, removedFrames)
        setPreviewStats(stats)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to fetch preview')
      } finally {
        setIsLoadingPreview(false)
      }
    },
    [datasetId],
  )

  const reset = useCallback(() => {
    setProgress(null)
    setResult(null)
    setError(null)
    setPreviewStats(null)
  }, [])

  return {
    isExporting,
    progress,
    result,
    error,
    previewStats,
    isLoadingPreview,
    startExport,
    cancelExport,
    fetchPreview,
    reset,
  }
}
