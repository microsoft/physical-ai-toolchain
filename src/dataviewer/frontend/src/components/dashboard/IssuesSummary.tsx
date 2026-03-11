/**
 * Issues summary card showing top issues by frequency.
 */

import { AlertCircle, AlertTriangle, Flag } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Progress } from '@/components/ui/progress';
import { cn } from '@/lib/utils';

export interface IssueItem {
  name: string;
  count: number;
}

export interface IssuesSummaryProps {
  /** Issues grouped by type */
  issues: IssueItem[];
  /** Anomalies grouped by type */
  anomalies?: IssueItem[];
  /** Total episodes for percentage calculation */
  totalEpisodes?: number;
  /** Additional class names */
  className?: string;
}

/**
 * Displays summary of issues and anomalies.
 */
export function IssuesSummary({
  issues,
  anomalies = [],
  totalEpisodes = 0,
  className,
}: IssuesSummaryProps) {
  const totalIssues = issues.reduce((sum, i) => sum + i.count, 0);
  const totalAnomalies = anomalies.reduce((sum, a) => sum + a.count, 0);

  const maxCount = Math.max(
    ...issues.map((i) => i.count),
    ...anomalies.map((a) => a.count),
    1
  );

  const formatName = (name: string) =>
    name
      .replace(/_/g, ' ')
      .replace(/\b\w/g, (c) => c.toUpperCase());

  return (
    <Card className={cn('', className)}>
      <CardHeader className="pb-2">
        <CardTitle className="text-lg">Issues & Anomalies</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Summary badges */}
        <div className="flex gap-3">
          <Badge variant="outline" className="gap-1.5 py-1">
            <Flag className="h-3.5 w-3.5 text-orange-500" />
            <span>{totalIssues} Issues</span>
          </Badge>
          <Badge variant="outline" className="gap-1.5 py-1">
            <AlertTriangle className="h-3.5 w-3.5 text-red-500" />
            <span>{totalAnomalies} Anomalies</span>
          </Badge>
        </div>

        {/* Issues list */}
        {issues.length > 0 && (
          <div className="space-y-2">
            <h4 className="flex items-center gap-2 text-sm font-medium">
              <Flag className="h-4 w-4 text-orange-500" />
              Top Issues
            </h4>
            <div className="space-y-2">
              {issues.slice(0, 5).map((issue) => (
                <div key={issue.name} className="space-y-1">
                  <div className="flex items-center justify-between text-sm">
                    <span className="truncate">{formatName(issue.name)}</span>
                    <span className="text-muted-foreground">
                      {issue.count}
                      {totalEpisodes > 0 && (
                        <span className="ml-1">
                          ({Math.round((issue.count / totalEpisodes) * 100)}%)
                        </span>
                      )}
                    </span>
                  </div>
                  <Progress
                    value={(issue.count / maxCount) * 100}
                    className="h-1.5"
                  />
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Anomalies list */}
        {anomalies.length > 0 && (
          <div className="space-y-2">
            <h4 className="flex items-center gap-2 text-sm font-medium">
              <AlertTriangle className="h-4 w-4 text-red-500" />
              Top Anomalies
            </h4>
            <div className="space-y-2">
              {anomalies.slice(0, 5).map((anomaly) => (
                <div key={anomaly.name} className="space-y-1">
                  <div className="flex items-center justify-between text-sm">
                    <span className="truncate">{formatName(anomaly.name)}</span>
                    <span className="text-muted-foreground">
                      {anomaly.count}
                      {totalEpisodes > 0 && (
                        <span className="ml-1">
                          ({Math.round((anomaly.count / totalEpisodes) * 100)}%)
                        </span>
                      )}
                    </span>
                  </div>
                  <Progress
                    value={(anomaly.count / maxCount) * 100}
                    className="h-1.5 [&>div]:bg-red-500"
                  />
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Empty state */}
        {issues.length === 0 && anomalies.length === 0 && (
          <div className="flex flex-col items-center gap-2 py-6 text-center">
            <AlertCircle className="h-8 w-8 text-muted-foreground/50" />
            <p className="text-sm text-muted-foreground">
              No issues or anomalies detected yet
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
