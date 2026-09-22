/**
 * Hook for fetching dashboard statistics.
 */

import { useQuery } from '@tanstack/react-query'

import { apiRequest, transformKeys } from '@/lib/api-client'

/** Dashboard statistics */
export interface DashboardStats {
  totalEpisodes: number
  annotatedEpisodes: number
  pendingEpisodes: number
  annotationRate: number
  ratingDistribution: Record<string, number>
  qualityDistribution: Record<string, number>
  annotatorStats: AnnotatorStats[]
  recentActivity: ActivityItem[]
  issuesByType: Record<string, number>
  anomaliesByType: Record<string, number>
}

export interface AnnotatorStats {
  annotatorId: string
  annotatorName: string
  episodesAnnotated: number
  averageRating: number
  lastActive: string
}

export interface ActivityItem {
  id: string
  type: 'annotation' | 'review' | 'edit'
  episodeId: string
  annotatorName: string
  timestamp: string
  summary: string
}

/** Query key factory for dashboard */
export const dashboardKeys = {
  all: ['dashboard'] as const,
  stats: (datasetId: string) => [...dashboardKeys.all, 'stats', datasetId] as const,
  progress: (datasetId: string) => [...dashboardKeys.all, 'progress', datasetId] as const,
}

/**
 * Fetch dashboard statistics.
 */
function transformDashboardStats(data: unknown): DashboardStats {
  const raw = data as Record<string, unknown>
  const stats = transformKeys<DashboardStats>(raw)
  const issuesByType = raw.issues_by_type
  const anomaliesByType = raw.anomalies_by_type

  if (issuesByType && typeof issuesByType === 'object') {
    stats.issuesByType = { ...(issuesByType as Record<string, number>) }
  }
  if (anomaliesByType && typeof anomaliesByType === 'object') {
    stats.anomaliesByType = { ...(anomaliesByType as Record<string, number>) }
  }

  return stats
}

async function fetchDashboardStats(datasetId: string): Promise<DashboardStats> {
  return apiRequest<DashboardStats>(`/datasets/${datasetId}/stats`, {}, transformDashboardStats)
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
          (data.annotatedEpisodes / Math.max(data.totalEpisodes, 1)) * 100,
        ),
        averageRating: calculateAverageRating(data.ratingDistribution),
        averageQuality: calculateAverageRating(data.qualityDistribution),
        episodesPerHour: calculateEpisodesPerHour(data.recentActivity),
        topIssues: getTopItems(data.issuesByType, 5),
        topAnomalies: getTopItems(data.anomaliesByType, 5),
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
