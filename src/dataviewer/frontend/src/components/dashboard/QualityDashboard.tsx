/**
 * Main quality dashboard page component.
 */

import { AlertCircle, RefreshCw } from 'lucide-react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { useDashboardMetrics } from '@/hooks/use-dashboard';
import { cn } from '@/lib/utils';

import { ActivityFeed } from './ActivityFeed';
import { AnnotatorLeaderboard } from './AnnotatorLeaderboard';
import { IssuesSummary } from './IssuesSummary';
import { ProgressOverview } from './ProgressOverview';
import { RatingDistribution } from './RatingDistribution';

export interface QualityDashboardProps {
  /** Dataset identifier */
  datasetId: string;
  /** Additional class names */
  className?: string;
}

/**
 * Main quality dashboard displaying annotation statistics.
 */
export function QualityDashboard({ datasetId, className }: QualityDashboardProps) {
  const { data, metrics, isLoading, error, refetch } = useDashboardMetrics(datasetId);

  if (isLoading) {
    return (
      <div className={cn('space-y-6', className)}>
        <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
          <Skeleton className="h-64" />
          <Skeleton className="h-64" />
          <Skeleton className="h-64" />
        </div>
        <div className="grid gap-6 md:grid-cols-2">
          <Skeleton className="h-80" />
          <Skeleton className="h-80" />
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <Alert variant="destructive" className={className}>
        <AlertCircle className="h-4 w-4" />
        <AlertTitle>Failed to load dashboard</AlertTitle>
        <AlertDescription className="flex items-center gap-2">
          <span>Could not load dashboard statistics.</span>
          <Button variant="outline" size="sm" onClick={() => refetch()}>
            <RefreshCw className="h-4 w-4 mr-1" />
            Retry
          </Button>
        </AlertDescription>
      </Alert>
    );
  }

  if (!data || !metrics) {
    return (
      <Alert className={className}>
        <AlertCircle className="h-4 w-4" />
        <AlertTitle>No data available</AlertTitle>
        <AlertDescription>
          No annotation data is available for this dataset yet.
        </AlertDescription>
      </Alert>
    );
  }

  return (
    <div className={cn('space-y-6', className)}>
      {/* Top row: Progress + Charts */}
      <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
        <ProgressOverview
          totalEpisodes={data.total_episodes}
          annotatedEpisodes={data.annotated_episodes}
          pendingEpisodes={data.pending_episodes}
          episodesPerHour={metrics.episodesPerHour}
        />

        <RatingDistribution
          distribution={data.rating_distribution}
          title="Task Completion Ratings"
          colorScheme="rating"
        />

        <RatingDistribution
          distribution={data.quality_distribution}
          title="Trajectory Quality"
          colorScheme="quality"
        />
      </div>

      {/* Middle row: Issues + Leaderboard */}
      <div className="grid gap-6 md:grid-cols-2">
        <IssuesSummary
          issues={metrics.topIssues}
          anomalies={metrics.topAnomalies}
          totalEpisodes={data.annotated_episodes}
        />

        <AnnotatorLeaderboard annotators={data.annotator_stats} limit={5} />
      </div>

      {/* Bottom row: Activity Feed */}
      <ActivityFeed activities={data.recent_activity} limit={15} maxHeight={350} />
    </div>
  );
}
