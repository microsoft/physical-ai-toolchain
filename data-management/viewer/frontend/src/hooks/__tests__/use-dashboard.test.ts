import { waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { _resetCsrfToken } from '@/lib/api-client'
import { renderHookWithQuery } from '@/test/render-hook-with-query'

import {
  useDashboardMetrics,
  useDashboardStats,
  type DashboardStats,
} from '../use-dashboard'

const mockFetch = vi.fn()

function jsonResponse(data: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? 'OK' : 'Error',
    json: () => Promise.resolve(data),
  }
}

function makeStats(overrides: Partial<DashboardStats> = {}): DashboardStats {
  return {
    total_episodes: 10,
    annotated_episodes: 5,
    pending_episodes: 5,
    annotation_rate: 0.5,
    rating_distribution: {},
    quality_distribution: {},
    annotator_stats: [],
    recent_activity: [],
    issues_by_type: {},
    anomalies_by_type: {},
    ...overrides,
  }
}

beforeEach(() => {
  mockFetch.mockReset()
  _resetCsrfToken()
  vi.stubGlobal('fetch', mockFetch)
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('useDashboardStats', () => {
  it('fetches stats from /api/datasets/:id/stats', async () => {
    const stats = makeStats({ total_episodes: 42 })
    mockFetch.mockResolvedValueOnce(jsonResponse(stats))

    const { result } = renderHookWithQuery(() => useDashboardStats('ds-1'))

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual(stats)
    expect(mockFetch).toHaveBeenCalledWith('/api/datasets/ds-1/stats', expect.any(Object))
  })

  it('does not fetch when datasetId is empty', () => {
    const { result } = renderHookWithQuery(() => useDashboardStats(''))
    expect(result.current.fetchStatus).toBe('idle')
    expect(mockFetch).not.toHaveBeenCalled()
  })

  it('does not fetch when enabled is false', () => {
    const { result } = renderHookWithQuery(() => useDashboardStats('ds-1', false))
    expect(result.current.fetchStatus).toBe('idle')
    expect(mockFetch).not.toHaveBeenCalled()
  })
})

describe('useDashboardMetrics', () => {
  it('returns null metrics while data is loading', () => {
    mockFetch.mockReturnValueOnce(new Promise(() => {}))
    const { result } = renderHookWithQuery(() => useDashboardMetrics('ds-1'))
    expect(result.current.metrics).toBeNull()
  })

  it('computes completionPercent rounded against total guarded by Math.max', async () => {
    const stats = makeStats({ total_episodes: 0, annotated_episodes: 0 })
    mockFetch.mockResolvedValueOnce(jsonResponse(stats))

    const { result } = renderHookWithQuery(() => useDashboardMetrics('ds-1'))
    await waitFor(() => expect(result.current.metrics).not.toBeNull())

    expect(result.current.metrics?.completionPercent).toBe(0)
  })

  it('rounds completionPercent for typical values', async () => {
    const stats = makeStats({ total_episodes: 3, annotated_episodes: 1 })
    mockFetch.mockResolvedValueOnce(jsonResponse(stats))

    const { result } = renderHookWithQuery(() => useDashboardMetrics('ds-1'))
    await waitFor(() => expect(result.current.metrics).not.toBeNull())

    expect(result.current.metrics?.completionPercent).toBe(33)
  })

  it('computes weighted averageRating and skips non-numeric keys', async () => {
    const stats = makeStats({
      rating_distribution: { '1': 1, '5': 3, 'invalid': 99 },
    })
    mockFetch.mockResolvedValueOnce(jsonResponse(stats))

    const { result } = renderHookWithQuery(() => useDashboardMetrics('ds-1'))
    await waitFor(() => expect(result.current.metrics).not.toBeNull())

    expect(result.current.metrics?.averageRating).toBe(4)
  })

  it('returns averageRating 0 when distribution is empty', async () => {
    const stats = makeStats({ rating_distribution: {} })
    mockFetch.mockResolvedValueOnce(jsonResponse(stats))

    const { result } = renderHookWithQuery(() => useDashboardMetrics('ds-1'))
    await waitFor(() => expect(result.current.metrics).not.toBeNull())

    expect(result.current.metrics?.averageRating).toBe(0)
  })

  it('computes averageQuality the same way', async () => {
    const stats = makeStats({
      quality_distribution: { '2': 1, '4': 1 },
    })
    mockFetch.mockResolvedValueOnce(jsonResponse(stats))

    const { result } = renderHookWithQuery(() => useDashboardMetrics('ds-1'))
    await waitFor(() => expect(result.current.metrics).not.toBeNull())

    expect(result.current.metrics?.averageQuality).toBe(3)
  })

  it('returns episodesPerHour=0 when fewer than 2 activity entries', async () => {
    const stats = makeStats({
      recent_activity: [
        {
          id: 'a',
          type: 'annotation',
          episode_id: '1',
          annotator_name: 'u',
          timestamp: new Date().toISOString(),
          summary: 's',
        },
      ],
    })
    mockFetch.mockResolvedValueOnce(jsonResponse(stats))

    const { result } = renderHookWithQuery(() => useDashboardMetrics('ds-1'))
    await waitFor(() => expect(result.current.metrics).not.toBeNull())

    expect(result.current.metrics?.episodesPerHour).toBe(0)
  })

  it('returns episodesPerHour=0 when fewer than 2 annotation entries', async () => {
    const now = Date.now()
    const stats = makeStats({
      recent_activity: [
        {
          id: 'a',
          type: 'review',
          episode_id: '1',
          annotator_name: 'u',
          timestamp: new Date(now).toISOString(),
          summary: 's',
        },
        {
          id: 'b',
          type: 'edit',
          episode_id: '2',
          annotator_name: 'u',
          timestamp: new Date(now + 3_600_000).toISOString(),
          summary: 's',
        },
      ],
    })
    mockFetch.mockResolvedValueOnce(jsonResponse(stats))

    const { result } = renderHookWithQuery(() => useDashboardMetrics('ds-1'))
    await waitFor(() => expect(result.current.metrics).not.toBeNull())

    expect(result.current.metrics?.episodesPerHour).toBe(0)
  })

  it('returns episodesPerHour=0 when annotations span less than 0.1 hours', async () => {
    const now = Date.now()
    const stats = makeStats({
      recent_activity: [
        {
          id: 'a',
          type: 'annotation',
          episode_id: '1',
          annotator_name: 'u',
          timestamp: new Date(now).toISOString(),
          summary: 's',
        },
        {
          id: 'b',
          type: 'annotation',
          episode_id: '2',
          annotator_name: 'u',
          timestamp: new Date(now + 60_000).toISOString(),
          summary: 's',
        },
      ],
    })
    mockFetch.mockResolvedValueOnce(jsonResponse(stats))

    const { result } = renderHookWithQuery(() => useDashboardMetrics('ds-1'))
    await waitFor(() => expect(result.current.metrics).not.toBeNull())

    expect(result.current.metrics?.episodesPerHour).toBe(0)
  })

  it('computes episodesPerHour for spans >= 0.1 hours', async () => {
    const start = new Date('2025-01-01T00:00:00Z').getTime()
    const stats = makeStats({
      recent_activity: [
        {
          id: 'a',
          type: 'annotation',
          episode_id: '1',
          annotator_name: 'u',
          timestamp: new Date(start).toISOString(),
          summary: 's',
        },
        {
          id: 'b',
          type: 'annotation',
          episode_id: '2',
          annotator_name: 'u',
          timestamp: new Date(start + 30 * 60_000).toISOString(),
          summary: 's',
        },
        {
          id: 'c',
          type: 'annotation',
          episode_id: '3',
          annotator_name: 'u',
          timestamp: new Date(start + 60 * 60_000).toISOString(),
          summary: 's',
        },
      ],
    })
    mockFetch.mockResolvedValueOnce(jsonResponse(stats))

    const { result } = renderHookWithQuery(() => useDashboardMetrics('ds-1'))
    await waitFor(() => expect(result.current.metrics).not.toBeNull())

    expect(result.current.metrics?.episodesPerHour).toBe(3)
  })

  it('returns top issues and anomalies sorted desc and limited to 5', async () => {
    const stats = makeStats({
      issues_by_type: { a: 1, b: 5, c: 3, d: 2, e: 4, f: 6, g: 0 },
      anomalies_by_type: { x: 10, y: 1 },
    })
    mockFetch.mockResolvedValueOnce(jsonResponse(stats))

    const { result } = renderHookWithQuery(() => useDashboardMetrics('ds-1'))
    await waitFor(() => expect(result.current.metrics).not.toBeNull())

    expect(result.current.metrics?.topIssues.map((i) => i.name)).toEqual([
      'f',
      'b',
      'e',
      'c',
      'd',
    ])
    expect(result.current.metrics?.topAnomalies).toEqual([
      { name: 'x', count: 10 },
      { name: 'y', count: 1 },
    ])
  })
})
