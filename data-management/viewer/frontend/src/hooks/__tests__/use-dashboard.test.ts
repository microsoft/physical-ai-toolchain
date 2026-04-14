import { renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ActivityItem, DashboardStats } from '@/hooks/use-dashboard'

import { createQueryWrapper } from './test-utils'

// ============================================================================
// Mocks
// ============================================================================

const mockFetch = vi.fn()

vi.mock('@/lib/api-client', () => ({
  requestHeaders: vi.fn().mockResolvedValue({ 'Content-Type': 'application/json' }),
  handleResponse: vi.fn(async (res: { ok: boolean; json: () => Promise<unknown> }) => {
    if (!res.ok) throw new Error('Request failed')
    return res.json()
  }),
}))

// ============================================================================
// Helpers
// ============================================================================

function jsonResponse(data: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? 'OK' : 'Error',
    json: () => Promise.resolve(data),
  }
}

function makeActivity(overrides: Partial<ActivityItem>[]): ActivityItem[] {
  return overrides.map((o, i) => ({
    id: `act-${i}`,
    type: 'annotation' as const,
    episode_id: `ep-${i}`,
    annotator_name: 'tester',
    timestamp: new Date(2025, 0, 1, 10 + i).toISOString(),
    summary: 'test',
    ...o,
  }))
}

function makeStats(overrides: Partial<DashboardStats> = {}): DashboardStats {
  return {
    total_episodes: 100,
    annotated_episodes: 50,
    pending_episodes: 50,
    annotation_rate: 0.5,
    rating_distribution: { '1': 5, '3': 10, '5': 35 },
    quality_distribution: { '2': 10, '4': 40 },
    annotator_stats: [],
    recent_activity: makeActivity([
      { timestamp: '2025-01-01T10:00:00Z' },
      { timestamp: '2025-01-01T12:00:00Z' },
      { timestamp: '2025-01-01T14:00:00Z' },
    ]),
    issues_by_type: { collision: 3, timeout: 7, drift: 1 },
    anomalies_by_type: { spike: 10, flatline: 2 },
    ...overrides,
  }
}

// ============================================================================
// Tests
// ============================================================================

describe('dashboardKeys', () => {
  it('generates hierarchical dashboard query keys', async () => {
    const { dashboardKeys } = await import('@/hooks/use-dashboard')

    expect(dashboardKeys.all).toEqual(['dashboard'])
    expect(dashboardKeys.stats('ds-1')).toEqual(['dashboard', 'stats', 'ds-1'])
    expect(dashboardKeys.progress('ds-1')).toEqual(['dashboard', 'progress', 'ds-1'])
  })
})

// ============================================================================
// useDashboardStats
// ============================================================================

describe('useDashboardStats', () => {
  let wrapper: ReturnType<typeof createQueryWrapper>

  beforeEach(() => {
    wrapper = createQueryWrapper()
    mockFetch.mockReset()
    vi.stubGlobal('fetch', mockFetch)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('fetches stats for a dataset', async () => {
    const stats = makeStats()
    mockFetch.mockResolvedValue(jsonResponse(stats))
    const { useDashboardStats } = await import('@/hooks/use-dashboard')
    const { result } = renderHook(() => useDashboardStats('ds-1'), { wrapper })

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true)
    })

    expect(result.current.data?.total_episodes).toBe(100)
  })

  it('is disabled when datasetId is empty', async () => {
    const { useDashboardStats } = await import('@/hooks/use-dashboard')
    const { result } = renderHook(() => useDashboardStats(''), { wrapper })

    expect(result.current.fetchStatus).toBe('idle')
    expect(mockFetch).not.toHaveBeenCalled()
  })

  it('is disabled when enabled is false', async () => {
    const { useDashboardStats } = await import('@/hooks/use-dashboard')
    const { result } = renderHook(() => useDashboardStats('ds-1', false), { wrapper })

    expect(result.current.fetchStatus).toBe('idle')
    expect(mockFetch).not.toHaveBeenCalled()
  })
})

// ============================================================================
// useDashboardMetrics
// ============================================================================

describe('useDashboardMetrics', () => {
  let wrapper: ReturnType<typeof createQueryWrapper>

  beforeEach(() => {
    wrapper = createQueryWrapper()
    mockFetch.mockReset()
    vi.stubGlobal('fetch', mockFetch)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('computes completion percentage', async () => {
    mockFetch.mockResolvedValue(
      jsonResponse(makeStats({ total_episodes: 200, annotated_episodes: 50 })),
    )
    const { useDashboardMetrics } = await import('@/hooks/use-dashboard')
    const { result } = renderHook(() => useDashboardMetrics('ds-1'), { wrapper })

    await waitFor(() => {
      expect(result.current.metrics).not.toBeNull()
    })

    expect(result.current.metrics?.completionPercent).toBe(25)
  })

  it('handles zero total episodes without division by zero', async () => {
    mockFetch.mockResolvedValue(
      jsonResponse(makeStats({ total_episodes: 0, annotated_episodes: 0 })),
    )
    const { useDashboardMetrics } = await import('@/hooks/use-dashboard')
    const { result } = renderHook(() => useDashboardMetrics('ds-1'), { wrapper })

    await waitFor(() => {
      expect(result.current.metrics).not.toBeNull()
    })

    expect(result.current.metrics?.completionPercent).toBe(0)
  })

  it('computes weighted average rating', async () => {
    mockFetch.mockResolvedValue(
      jsonResponse(makeStats({ rating_distribution: { '2': 2, '4': 2 } })),
    )
    const { useDashboardMetrics } = await import('@/hooks/use-dashboard')
    const { result } = renderHook(() => useDashboardMetrics('ds-1'), { wrapper })

    await waitFor(() => {
      expect(result.current.metrics).not.toBeNull()
    })

    // (2*2 + 4*2) / 4 = 3.0
    expect(result.current.metrics?.averageRating).toBe(3)
  })

  it('returns 0 for average rating with empty distribution', async () => {
    mockFetch.mockResolvedValue(jsonResponse(makeStats({ rating_distribution: {} })))
    const { useDashboardMetrics } = await import('@/hooks/use-dashboard')
    const { result } = renderHook(() => useDashboardMetrics('ds-1'), { wrapper })

    await waitFor(() => {
      expect(result.current.metrics).not.toBeNull()
    })

    expect(result.current.metrics?.averageRating).toBe(0)
  })

  it('computes episodes per hour from activity', async () => {
    const activity = makeActivity([
      { timestamp: '2025-01-01T10:00:00Z' },
      { timestamp: '2025-01-01T11:00:00Z' },
      { timestamp: '2025-01-01T12:00:00Z' },
    ])
    mockFetch.mockResolvedValue(jsonResponse(makeStats({ recent_activity: activity })))
    const { useDashboardMetrics } = await import('@/hooks/use-dashboard')
    const { result } = renderHook(() => useDashboardMetrics('ds-1'), { wrapper })

    await waitFor(() => {
      expect(result.current.metrics).not.toBeNull()
    })

    // 3 annotations over 2 hours = 1.5
    expect(result.current.metrics?.episodesPerHour).toBe(1.5)
  })

  it('returns 0 episodes per hour with fewer than 2 annotations', async () => {
    const activity = makeActivity([{ timestamp: '2025-01-01T10:00:00Z' }])
    mockFetch.mockResolvedValue(jsonResponse(makeStats({ recent_activity: activity })))
    const { useDashboardMetrics } = await import('@/hooks/use-dashboard')
    const { result } = renderHook(() => useDashboardMetrics('ds-1'), { wrapper })

    await waitFor(() => {
      expect(result.current.metrics).not.toBeNull()
    })

    expect(result.current.metrics?.episodesPerHour).toBe(0)
  })

  it('returns 0 episodes per hour when time span is under 0.1 hours', async () => {
    const activity = makeActivity([
      { timestamp: '2025-01-01T10:00:00Z' },
      { timestamp: '2025-01-01T10:00:01Z' },
    ])
    mockFetch.mockResolvedValue(jsonResponse(makeStats({ recent_activity: activity })))
    const { useDashboardMetrics } = await import('@/hooks/use-dashboard')
    const { result } = renderHook(() => useDashboardMetrics('ds-1'), { wrapper })

    await waitFor(() => {
      expect(result.current.metrics).not.toBeNull()
    })

    expect(result.current.metrics?.episodesPerHour).toBe(0)
  })

  it('filters non-annotation activity for episodesPerHour', async () => {
    const activity = makeActivity([
      { type: 'review', timestamp: '2025-01-01T10:00:00Z' },
      { type: 'annotation', timestamp: '2025-01-01T10:00:00Z' },
      { type: 'edit', timestamp: '2025-01-01T12:00:00Z' },
    ])
    mockFetch.mockResolvedValue(jsonResponse(makeStats({ recent_activity: activity })))
    const { useDashboardMetrics } = await import('@/hooks/use-dashboard')
    const { result } = renderHook(() => useDashboardMetrics('ds-1'), { wrapper })

    await waitFor(() => {
      expect(result.current.metrics).not.toBeNull()
    })

    // Only 1 annotation type — below threshold of 2
    expect(result.current.metrics?.episodesPerHour).toBe(0)
  })

  it('sorts top issues by count descending', async () => {
    mockFetch.mockResolvedValue(jsonResponse(makeStats({ issues_by_type: { a: 1, b: 5, c: 3 } })))
    const { useDashboardMetrics } = await import('@/hooks/use-dashboard')
    const { result } = renderHook(() => useDashboardMetrics('ds-1'), { wrapper })

    await waitFor(() => {
      expect(result.current.metrics).not.toBeNull()
    })

    expect(result.current.metrics?.topIssues).toEqual([
      { name: 'b', count: 5 },
      { name: 'c', count: 3 },
      { name: 'a', count: 1 },
    ])
  })

  it('returns null metrics when data is undefined', async () => {
    mockFetch.mockResolvedValue(jsonResponse(null, 500))
    const { useDashboardMetrics } = await import('@/hooks/use-dashboard')
    const { result } = renderHook(() => useDashboardMetrics('ds-1'), { wrapper })

    // Metrics should be null when data hasn't loaded
    expect(result.current.metrics).toBeNull()
  })
})
