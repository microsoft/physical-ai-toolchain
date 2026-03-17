/**
 * Hook for fetching dashboard statistics.
 */

import { useQuery } from '@tanstack/react-query'

import { handleResponse, requestHeaders } from '@/lib/api-client'

/** Dashboard statistics */
export interface DashboardStats {
  total_episodes: number
  annotated_episodes: number
  pending_episodes: number
  annotation_rate: number
  rating_distribution: Record<string, number>
  quality_distribution: Record<string, number>
  annotator_stats: AnnotatorStats[]
  recent_activity: ActivityItem[]
  issues_by_type: Record<string, number>
  anomalies_by_type: Record<string, number>
}

export interface AnnotatorStats {
  annotator_id: string
  annotator_name: string
  episodes_annotated: number
  average_rating: number
  last_active: string
}

export interface ActivityItem {
  id: string
  type: 'annotation' | 'review' | 'edit'
  episode_id: string
  annotator_name: string
  timestamp: string
  summary: string
}

/** Query key factory for dashboard */
export const dashboardKeys = {
  all: ['dashboard'] as const,
  stats: (datasetId: string) => [...dashboardKeys.all, 'stats', datasetId] as const,
  progress: (datasetId: string) => [...dashboardKeys.all, 'progress', datasetId] as const,
}

const API_BASE = '/api'

/**
 * Fetch dashboard statistics.
 */
async function fetchDashboardStats(datasetId: string): Promise<DashboardStats> {
  const response = await fetch(`${API_BASE}/datasets/${datasetId}/stats`, {
    headers: await requestHeaders(),
  })
  return handleResponse<DashboardStats>(response)
}

/**
 * Hook for fetching dashboard statistics.
 */
export function useDashboardStats(datasetId: string, enabled = true) {
  return useQuery({
    queryKey: dashboardKeys.stats(datasetId),
    queryFn: () => fetchDashboardStats(datasetId),
    enabled: enabled && !!datasetId,
    staleTime: 30 * 1000, // 30 seconds
    refetchInterval: 60 * 1000, // 1 minute
  })
}

/**
 * Hook for computed dashboard metrics.
 */
export function useDashboardMetrics(datasetId: string) {
  const { data, ...rest } = useDashboardStats(datasetId)

  const metrics = data
    ? {
        completionPercent: Math.round(
          (data.annotated_episodes / Math.max(data.total_episodes, 1)) * 100,
        ),
        averageRating: calculateAverageRating(data.rating_distribution),
        averageQuality: calculateAverageRating(data.quality_distribution),
        episodesPerHour: calculateEpisodesPerHour(data.recent_activity),
        topIssues: getTopItems(data.issues_by_type, 5),
        topAnomalies: getTopItems(data.anomalies_by_type, 5),
      }
    : null

  return { data, metrics, ...rest }
}

/** Calculate weighted average rating from distribution */
function calculateAverageRating(distribution: Record<string, number>): number {
  let total = 0
  let count = 0

  for (const [rating, num] of Object.entries(distribution)) {
    const ratingNum = parseInt(rating, 10)
    if (!isNaN(ratingNum)) {
      total += ratingNum * num
      count += num
    }
  }

  return count > 0 ? Math.round((total / count) * 10) / 10 : 0
}

/** Calculate episodes annotated per hour from recent activity */
function calculateEpisodesPerHour(activity: ActivityItem[]): number {
  if (activity.length < 2) return 0

  const annotations = activity.filter((a) => a.type === 'annotation')
  if (annotations.length < 2) return 0

  const timestamps = annotations.map((a) => new Date(a.timestamp).getTime()).sort((a, b) => a - b)

  const firstTime = timestamps[0]
  const lastTime = timestamps[timestamps.length - 1]
  const hoursDiff = (lastTime - firstTime) / (1000 * 60 * 60)

  if (hoursDiff < 0.1) return 0

  return Math.round((annotations.length / hoursDiff) * 10) / 10
}

/** Get top N items from a record sorted by count */
function getTopItems(
  items: Record<string, number>,
  limit: number,
): Array<{ name: string; count: number }> {
  return Object.entries(items)
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => b.count - a.count)
    .slice(0, limit)
}
