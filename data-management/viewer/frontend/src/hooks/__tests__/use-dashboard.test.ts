import { waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { DashboardStats } from '@/hooks/use-dashboard'
import { useDashboardMetrics, useDashboardStats } from '@/hooks/use-dashboard'
import { _resetCsrfToken } from '@/lib/api-client'
import { installFetchMock, jsonResponse, mockFetch } from '@/test-utils/fetch-mocks'
import { renderHookWithProviders } from '@/test-utils/render-hook'

function makeStats(overrides: Partial<DashboardStats> = {}): DashboardStats {
  return {
    total_episodes: 100,
    annotated_episodes: 50,
    pending_episodes: 50,
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
  installFetchMock()
  _resetCsrfToken()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('useDashboardStats', () => {
  it('does not fetch when datasetId is empty', () => {
    renderHookWithProviders(() => useDashboardStats(''))
    expect(mockFetch).not.toHaveBeenCalled()
  })

  it('does not fetch when explicitly disabled', () => {
    renderHookWithProviders(() => useDashboardStats('ds-1', false))
    expect(mockFetch).not.toHaveBeenCalled()
  })

  it('returns dashboard stats with snake_case fields preserved', async () => {
    const stats = makeStats({ total_episodes: 42, annotated_episodes: 21 })
    mockFetch.mockResolvedValueOnce(jsonResponse(stats))

    const { result } = renderHookWithProviders(() => useDashboardStats('ds-1'))

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(result.current.data).toEqual(stats)
    expect(mockFetch).toHaveBeenCalledWith('/api/datasets/ds-1/stats', expect.any(Object))
  })

  it('exposes errors from failed requests', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ message: 'stats failed', code: 'ERR' }, 500))

    const { result } = renderHookWithProviders(() => useDashboardStats('ds-1'))

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(result.current.error?.message).toBe('stats failed')
  })
})

describe('useDashboardMetrics', () => {
  it('returns null metrics when no data is available', () => {
    const { result } = renderHookWithProviders(() => useDashboardMetrics(''))
    expect(result.current.metrics).toBeNull()
  })

  it('computes completion percent and protects against zero totals', async () => {
    mockFetch.mockResolvedValueOnce(
      jsonResponse(makeStats({ total_episodes: 0, annotated_episodes: 0 })),
    )

    const { result } = renderHookWithProviders(() => useDashboardMetrics('ds-1'))

    await waitFor(() => expect(result.current.metrics).not.toBeNull())
    expect(result.current.metrics?.completionPercent).toBe(0)
  })

  it('computes weighted average ratings rounded to one decimal', async () => {
    mockFetch.mockResolvedValueOnce(
      jsonResponse(
        makeStats({
          total_episodes: 100,
          annotated_episodes: 50,
          rating_distribution: { '5': 2, '3': 1 },
          quality_distribution: { '4': 4 },
        }),
      ),
    )

    const { result } = renderHookWithProviders(() => useDashboardMetrics('ds-1'))

    await waitFor(() => expect(result.current.metrics).not.toBeNull())
    expect(result.current.metrics?.completionPercent).toBe(50)
    expect(result.current.metrics?.averageRating).toBe(4.3)
    expect(result.current.metrics?.averageQuality).toBe(4)
  })

  it('returns zero episodes per hour when activity is sparse or too close in time', async () => {
    const now = new Date('2025-01-01T00:00:00Z').getTime()
    mockFetch.mockResolvedValueOnce(
      jsonResponse(
        makeStats({
          recent_activity: [
            {
              id: 'a1',
              type: 'annotation',
              episode_id: 'e1',
              annotator_name: 'a',
              timestamp: new Date(now).toISOString(),
              summary: '',
            },
            {
              id: 'a2',
              type: 'annotation',
              episode_id: 'e2',
              annotator_name: 'a',
              timestamp: new Date(now + 60_000).toISOString(),
              summary: '',
            },
          ],
        }),
      ),
    )

    const { result } = renderHookWithProviders(() => useDashboardMetrics('ds-1'))

    await waitFor(() => expect(result.current.metrics).not.toBeNull())
    expect(result.current.metrics?.episodesPerHour).toBe(0)
  })

  it('computes episodes per hour and top issue/anomaly lists', async () => {
    const start = new Date('2025-01-01T00:00:00Z').getTime()
    const hourMs = 60 * 60 * 1000
    mockFetch.mockResolvedValueOnce(
      jsonResponse(
        makeStats({
          recent_activity: [
            {
              id: 'a1',
              type: 'annotation',
              episode_id: 'e1',
              annotator_name: 'a',
              timestamp: new Date(start).toISOString(),
              summary: '',
            },
            {
              id: 'a2',
              type: 'annotation',
              episode_id: 'e2',
              annotator_name: 'a',
              timestamp: new Date(start + hourMs).toISOString(),
              summary: '',
            },
            {
              id: 'a3',
              type: 'annotation',
              episode_id: 'e3',
              annotator_name: 'a',
              timestamp: new Date(start + 2 * hourMs).toISOString(),
              summary: '',
            },
            {
              id: 'a4',
              type: 'annotation',
              episode_id: 'e4',
              annotator_name: 'a',
              timestamp: new Date(start + 2 * hourMs).toISOString(),
              summary: '',
            },
          ],
          issues_by_type: { gripper: 5, motion: 1, vision: 9, force: 2, balance: 4, audio: 3 },
          anomalies_by_type: { drift: 7, jitter: 2 },
        }),
      ),
    )

    const { result } = renderHookWithProviders(() => useDashboardMetrics('ds-1'))

    await waitFor(() => expect(result.current.metrics).not.toBeNull())
    expect(result.current.metrics?.episodesPerHour).toBe(2)
    expect(result.current.metrics?.topIssues).toEqual([
      { name: 'vision', count: 9 },
      { name: 'gripper', count: 5 },
      { name: 'balance', count: 4 },
      { name: 'audio', count: 3 },
      { name: 'force', count: 2 },
    ])
    expect(result.current.metrics?.topAnomalies).toEqual([
      { name: 'drift', count: 7 },
      { name: 'jitter', count: 2 },
    ])
  })
})
