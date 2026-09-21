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

function isRecord(payload: unknown): payload is Record<string, unknown> {
  return payload !== null && typeof payload === 'object' && !Array.isArray(payload)
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function isExportProgress(payload: unknown): payload is ExportProgress {
  return (
    isRecord(payload) &&
    isFiniteNumber(payload.currentEpisode) &&
    isFiniteNumber(payload.totalEpisodes) &&
    isFiniteNumber(payload.currentFrame) &&
    isFiniteNumber(payload.totalFrames) &&
    isFiniteNumber(payload.percentage) &&
    typeof payload.status === 'string'
  )
}

function isExportResult(payload: unknown): payload is ExportResult {
  return (
    isRecord(payload) &&
    typeof payload.success === 'boolean' &&
    Array.isArray(payload.outputFiles) &&
    payload.outputFiles.every((value) => typeof value === 'string') &&
    (payload.error === null || typeof payload.error === 'string') &&
    isRecord(payload.stats) &&
    isFiniteNumber(payload.stats.totalEpisodes) &&
    isFiniteNumber(payload.stats.totalFrames) &&
    isFiniteNumber(payload.stats.removedFrames) &&
    isFiniteNumber(payload.stats.durationMs)
  )
}

function exportErrorMessage(payload: unknown): string {
  switch (isRecord(payload) ? payload.code : undefined) {
    case 'EXPORT_UNAVAILABLE':
      return 'Export is unavailable'
    case 'EXPORT_FAILED':
    default:
      return 'Export failed'
  }
}

function publicExportResult(result: ExportResult): ExportResult {
  return { ...result, error: result.success ? null : 'Export failed' }
}

function transformExportResult(data: unknown): ExportResult {
  const result = transformKeys<unknown>(data)
  if (!isExportResult(result)) {
    throw new Error('Invalid export response')
  }
  return publicExportResult(result)
}

/**
 * Start a synchronous export operation
 */
export async function exportEpisodes(
  datasetId: string,
  request: ExportRequestWithEdits,
): Promise<ExportResult> {
  return apiRequest<ExportResult>(
    `/datasets/${datasetId}/export`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    },
    transformExportResult,
  )
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
      let currentEventType = 'message'
      let receivedTerminalEvent = false

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() ?? ''

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
            let parsed: unknown
            try {
              parsed = transformKeys<unknown>(JSON.parse(data))
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
              receivedTerminalEvent = true
              onError(exportErrorMessage(parsed))
            } else if (currentEventType === 'progress') {
              if (isExportProgress(parsed)) {
                onProgress(parsed)
              } else {
                recordDiagnosticEvent('export', 'stream-schema-error', {
                  eventType: currentEventType,
                })
              }
            } else if (currentEventType === 'complete') {
              receivedTerminalEvent = true
              if (isExportResult(parsed)) {
                onComplete(publicExportResult(parsed))
              } else {
                recordDiagnosticEvent('export', 'stream-schema-error', {
                  eventType: currentEventType,
                })
                onError('Export failed')
              }
            }
          }
        }
      }

      if (!receivedTerminalEvent) {
        recordDiagnosticEvent('export', 'stream-incomplete', {})
        onError('Export failed')
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
