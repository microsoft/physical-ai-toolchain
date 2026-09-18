import { apiFetch, apiRequest, handleResponse, transformKeys } from '@/lib/api-client'
import { recordDiagnosticEvent } from '@/lib/playback-diagnostics'
import type { EpisodeEditOperations, ExportProgress, ExportResult } from '@/types'

export interface ExportPreviewStats {
  totalEpisodes: number
  totalFrames: number
  estimatedOutputSize: string
  removedFramesCount: number
}

/** Extended export request with per-episode edits */
export interface ExportRequestWithEdits {
  episodeIndices: number[]
  outputPath: string
  applyEdits: boolean
  includeSubtasks: boolean
  format: 'hdf5' | 'parquet'
  edits?: Record<number, EpisodeEditOperations>
}

function isExportProgress(
  payload: Record<string, unknown>,
): payload is Record<string, unknown> & ExportProgress {
  return typeof payload.percentage === 'number'
}

function isExportResult(
  payload: Record<string, unknown>,
): payload is Record<string, unknown> & ExportResult {
  return typeof payload.success === 'boolean'
}

/**
 * Start a synchronous export operation
 */
export async function exportEpisodes(
  datasetId: string,
  request: ExportRequestWithEdits,
): Promise<ExportResult> {
  return apiRequest<ExportResult>(`/datasets/${datasetId}/export`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  })
}

/**
 * Get preview statistics for an export operation
 */
export async function getExportPreview(
  datasetId: string,
  episodeIndices: number[],
  removedFrames?: number[],
): Promise<ExportPreviewStats> {
  const params = new URLSearchParams()
  params.set('episode_indices', episodeIndices.join(','))
  if (removedFrames?.length) {
    params.set('removed_frames', removedFrames.join(','))
  }
  return apiRequest<ExportPreviewStats>(
    `/datasets/${datasetId}/export/preview?${params.toString()}`,
  )
}

/**
 * Start a streaming export with SSE progress updates
 * Returns a cleanup function to close the connection
 */
export function createExportStream(
  datasetId: string,
  request: ExportRequestWithEdits,
  onProgress: (progress: ExportProgress) => void,
  onComplete: (result: ExportResult) => void,
  onError: (error: string) => void,
): () => void {
  const abortController = new AbortController()

  async function startStream() {
    try {
      const response = await apiFetch(`/datasets/${datasetId}/export/stream`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'text/event-stream',
        },
        body: JSON.stringify(request),
        signal: abortController.signal,
      })

      if (!response.ok) {
        await handleResponse(response)
      }

      const reader = response.body?.getReader()
      if (!reader) {
        throw new Error('No response body')
      }

      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() ?? ''

        let currentEventType = 'message'
        for (const line of lines) {
          if (line.trim() === '') {
            currentEventType = 'message'
            continue
          }
          if (line.startsWith('event: ')) {
            currentEventType = line.slice(7).trim()
            continue
          }
          if (line.startsWith('data: ')) {
            const data = line.slice(6)
            let parsed: Record<string, unknown>
            try {
              parsed = transformKeys<Record<string, unknown>>(JSON.parse(data))
            } catch (error) {
              const diagnostic = {
                eventType: currentEventType,
                message: error instanceof Error ? error.message : 'Invalid JSON payload',
                payload: data.slice(0, 200),
              }
              recordDiagnosticEvent('export', 'stream-parse-error', diagnostic)
              console.warn('Failed to parse export stream event', diagnostic)
              continue
            }

            if (currentEventType === 'error') {
              const message =
                typeof parsed.error === 'string'
                  ? parsed.error
                  : typeof parsed.message === 'string'
                    ? parsed.message
                    : 'Export failed'
              onError(message)
            } else if (isExportProgress(parsed)) {
              onProgress(parsed)
            } else if (isExportResult(parsed)) {
              onComplete(parsed)
            }
          }
        }
      }
    } catch (error) {
      if (error instanceof Error && error.name === 'AbortError') {
        return
      }
      onError(error instanceof Error ? error.message : 'Export failed')
    }
  }

  startStream()

  return () => {
    abortController.abort()
  }
}
