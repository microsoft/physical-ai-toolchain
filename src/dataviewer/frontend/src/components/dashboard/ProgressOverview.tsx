/**
 * Progress overview card showing annotation completion status.
 */

import { CheckCircle2, Clock, FileText, TrendingUp } from 'lucide-react';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Progress } from '@/components/ui/progress';
import { cn } from '@/lib/utils';

export interface ProgressOverviewProps {
  /** Total number of episodes */
  totalEpisodes: number;
  /** Number of annotated episodes */
  annotatedEpisodes: number;
  /** Number of pending episodes */
  pendingEpisodes: number;
  /** Episodes per hour rate */
  episodesPerHour?: number;
  /** Additional class names */
  className?: string;
}

/**
 * Displays annotation progress overview with completion metrics.
 */
export function ProgressOverview({
  totalEpisodes,
  annotatedEpisodes,
  pendingEpisodes,
  episodesPerHour = 0,
  className,
}: ProgressOverviewProps) {
  const completionPercent =
    totalEpisodes > 0 ? Math.round((annotatedEpisodes / totalEpisodes) * 100) : 0;

  const estimatedHoursRemaining =
    episodesPerHour > 0 ? Math.ceil(pendingEpisodes / episodesPerHour) : 0;

  return (
    <Card className={cn('', className)}>
      <CardHeader className="pb-2">
        <CardTitle className="text-lg">Annotation Progress</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Progress bar */}
        <div className="space-y-2">
          <div className="flex justify-between text-sm">
            <span className="text-muted-foreground">Completion</span>
            <span className="font-medium">{completionPercent}%</span>
          </div>
          <Progress value={completionPercent} className="h-3" />
        </div>

        {/* Stats grid */}
        <div className="grid grid-cols-2 gap-4">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-green-100">
              <CheckCircle2 className="h-5 w-5 text-green-600" />
            </div>
            <div>
              <p className="text-2xl font-semibold">{annotatedEpisodes.toLocaleString()}</p>
              <p className="text-xs text-muted-foreground">Completed</p>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-yellow-100">
              <Clock className="h-5 w-5 text-yellow-600" />
            </div>
            <div>
              <p className="text-2xl font-semibold">{pendingEpisodes.toLocaleString()}</p>
              <p className="text-xs text-muted-foreground">Pending</p>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-blue-100">
              <FileText className="h-5 w-5 text-blue-600" />
            </div>
            <div>
              <p className="text-2xl font-semibold">{totalEpisodes.toLocaleString()}</p>
              <p className="text-xs text-muted-foreground">Total Episodes</p>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-purple-100">
              <TrendingUp className="h-5 w-5 text-purple-600" />
            </div>
            <div>
              <p className="text-2xl font-semibold">{episodesPerHour}</p>
              <p className="text-xs text-muted-foreground">Episodes/Hour</p>
            </div>
          </div>
        </div>

        {/* Time estimate */}
        {estimatedHoursRemaining > 0 && (
          <div className="rounded-lg bg-muted/50 p-3">
            <p className="text-sm text-muted-foreground">
              Estimated time remaining:{' '}
              <span className="font-medium text-foreground">
                {estimatedHoursRemaining} hour{estimatedHoursRemaining !== 1 ? 's' : ''}
              </span>
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
