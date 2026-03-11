/**
 * Preview of filtered episodes for curriculum.
 */

import { AlertTriangle, CheckCircle,FileText, Star } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';

export interface EpisodePreviewItem {
  id: string;
  episode_id: string;
  task_completion_rating?: number;
  trajectory_quality_score?: number;
  has_anomalies: boolean;
  has_issues: boolean;
  thumbnail_url?: string;
}

export interface CurriculumPreviewProps {
  /** List of filtered episodes */
  episodes: EpisodePreviewItem[];
  /** Whether data is loading */
  isLoading?: boolean;
  /** Total matching episodes */
  totalCount: number;
  /** Maximum to display */
  previewLimit?: number;
  /** Additional class names */
  className?: string;
}

/**
 * Displays preview of episodes matching curriculum filters.
 */
export function CurriculumPreview({
  episodes,
  isLoading = false,
  totalCount,
  previewLimit = 50,
  className,
}: CurriculumPreviewProps) {
  const hasMore = totalCount > previewLimit;
  const displayedEpisodes = episodes.slice(0, previewLimit);

  if (isLoading) {
    return (
      <Card className={cn('', className)}>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">Preview</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {['preview-1', 'preview-2', 'preview-3', 'preview-4', 'preview-5'].map((placeholder) => (
            <Skeleton key={placeholder} className="h-12 w-full" />
          ))}
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className={cn('', className)}>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="text-base">Preview</CardTitle>
          <Badge variant="secondary">
            {totalCount.toLocaleString()} episodes
          </Badge>
        </div>
      </CardHeader>
      <CardContent>
        {episodes.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-8">
            <FileText className="h-8 w-8 text-muted-foreground/50 mb-2" />
            <p className="text-sm text-muted-foreground">
              No episodes match the current filters
            </p>
          </div>
        ) : (
          <ScrollArea className="h-80">
            <div className="space-y-2">
              {displayedEpisodes.map((episode) => (
                <div
                  key={episode.id}
                  className="flex items-center gap-3 p-2 rounded-lg border bg-card hover:bg-muted/50 transition-colors"
                >
                  {/* Thumbnail or placeholder */}
                  {episode.thumbnail_url ? (
                    <img
                      src={episode.thumbnail_url}
                      alt=""
                      className="h-10 w-14 rounded object-cover"
                    />
                  ) : (
                    <div className="h-10 w-14 rounded bg-muted flex items-center justify-center">
                      <FileText className="h-4 w-4 text-muted-foreground" />
                    </div>
                  )}

                  {/* Episode info */}
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium truncate">
                      {episode.episode_id}
                    </p>
                    <div className="flex items-center gap-3 text-xs text-muted-foreground">
                      {episode.task_completion_rating && (
                        <span className="flex items-center gap-0.5">
                          <Star className="h-3 w-3 fill-yellow-400 text-yellow-400" />
                          {episode.task_completion_rating}
                        </span>
                      )}
                      {episode.trajectory_quality_score && (
                        <span className="flex items-center gap-0.5">
                          <CheckCircle className="h-3 w-3 text-green-500" />
                          {episode.trajectory_quality_score}
                        </span>
                      )}
                    </div>
                  </div>

                  {/* Status badges */}
                  <div className="flex items-center gap-1.5">
                    {episode.has_anomalies && (
                      <Badge variant="outline" className="text-xs text-orange-600">
                        <AlertTriangle className="h-3 w-3 mr-0.5" />
                        Anomaly
                      </Badge>
                    )}
                    {episode.has_issues && (
                      <Badge variant="outline" className="text-xs text-red-600">
                        Issue
                      </Badge>
                    )}
                  </div>
                </div>
              ))}

              {hasMore && (
                <div className="text-center py-2 text-sm text-muted-foreground">
                  ... and {totalCount - previewLimit} more episodes
                </div>
              )}
            </div>
          </ScrollArea>
        )}
      </CardContent>
    </Card>
  );
}
