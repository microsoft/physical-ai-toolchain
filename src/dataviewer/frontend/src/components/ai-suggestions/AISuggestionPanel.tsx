/**
 * Panel displaying AI suggestions for the current episode.
 */

import { AlertCircle,RefreshCw, Sparkles } from 'lucide-react';
import { useCallback,useState } from 'react';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import {
  type SuggestAnnotationRequest,
  useAISuggestion,
  useRequestAISuggestion,
} from '@/hooks/use-ai-analysis';
import { cn } from '@/lib/utils';

import { AISuggestionBadge } from './AISuggestionBadge';
import { SuggestionCard, type SuggestionField } from './SuggestionCard';

export interface AISuggestionPanelProps {
  /** Dataset identifier */
  datasetId: string;
  /** Episode identifier */
  episodeId: string;
  /** Trajectory data for analysis */
  trajectoryData?: SuggestAnnotationRequest;
  /** Handler for applying suggestions */
  onApplySuggestion?: (fields: SuggestionField[], values: Record<string, unknown>) => void;
  /** Whether AI suggestions are enabled */
  enabled?: boolean;
  /** Additional class names */
  className?: string;
}

/**
 * Displays AI suggestion panel with loading states and actions.
 */
export function AISuggestionPanel({
  datasetId,
  episodeId,
  trajectoryData,
  onApplySuggestion,
  enabled = true,
  className,
}: AISuggestionPanelProps) {
  const [suggestionStatus, setSuggestionStatus] = useState<
    'pending' | 'accepted' | 'rejected'
  >('pending');
  const [isApplying, setIsApplying] = useState(false);

  const {
    data: suggestion,
    isLoading,
    error,
    refetch,
  } = useAISuggestion({
    datasetId,
    episodeId,
    trajectoryData,
    enabled: enabled && !!trajectoryData,
  });

  const refreshMutation = useRequestAISuggestion();

  const handleRefresh = useCallback(() => {
    if (trajectoryData) {
      refreshMutation.mutate(trajectoryData, {
        onSuccess: () => {
          refetch();
          setSuggestionStatus('pending');
        },
      });
    }
  }, [trajectoryData, refreshMutation, refetch]);

  const handleAccept = useCallback(() => {
    if (!suggestion || !onApplySuggestion) return;

    setIsApplying(true);
    onApplySuggestion(
      ['task_completion', 'trajectory_quality', 'flags', 'anomalies'],
      {
        task_completion_rating: suggestion.task_completion_rating,
        trajectory_quality_score: suggestion.trajectory_quality_score,
        suggested_flags: suggestion.suggested_flags,
        detected_anomalies: suggestion.detected_anomalies,
      }
    );
    setSuggestionStatus('accepted');
    setIsApplying(false);
  }, [suggestion, onApplySuggestion]);

  const handleReject = useCallback(() => {
    setSuggestionStatus('rejected');
  }, []);

  const handlePartialAccept = useCallback(
    (fields: SuggestionField[]) => {
      if (!suggestion || !onApplySuggestion) return;

      setIsApplying(true);
      const values: Record<string, unknown> = {};

      if (fields.includes('task_completion')) {
        values.task_completion_rating = suggestion.task_completion_rating;
      }
      if (fields.includes('trajectory_quality')) {
        values.trajectory_quality_score = suggestion.trajectory_quality_score;
      }
      if (fields.includes('flags')) {
        values.suggested_flags = suggestion.suggested_flags;
      }
      if (fields.includes('anomalies')) {
        values.detected_anomalies = suggestion.detected_anomalies;
      }

      onApplySuggestion(fields, values);
      setSuggestionStatus('accepted');
      setIsApplying(false);
    },
    [suggestion, onApplySuggestion]
  );

  // No trajectory data
  if (!trajectoryData) {
    return (
      <Card className={cn('', className)}>
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-2 text-base">
            <Sparkles className="h-4 w-4 text-gray-400" />
            AI Suggestions
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <AlertCircle className="h-4 w-4" />
            <span>No trajectory data available for analysis</span>
          </div>
        </CardContent>
      </Card>
    );
  }

  // Loading state
  if (isLoading) {
    return (
      <Card className={cn('', className)}>
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-2 text-base">
            <Sparkles className="h-4 w-4 text-blue-500 animate-pulse" />
            AI Suggestions
            <AISuggestionBadge isLoading />
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <Skeleton className="h-6 w-full" />
          <Skeleton className="h-6 w-3/4" />
          <Skeleton className="h-6 w-1/2" />
          <Skeleton className="h-20 w-full" />
        </CardContent>
      </Card>
    );
  }

  // Error state
  if (error) {
    return (
      <Card className={cn('border-red-200', className)}>
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between">
            <CardTitle className="flex items-center gap-2 text-base">
              <Sparkles className="h-4 w-4 text-red-500" />
              AI Suggestions
              <AISuggestionBadge hasError />
            </CardTitle>
            <Button
              variant="ghost"
              size="sm"
              onClick={handleRefresh}
              disabled={refreshMutation.isPending}
            >
              <RefreshCw
                className={cn('h-4 w-4', refreshMutation.isPending && 'animate-spin')}
              />
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          <div className="flex items-center gap-2 text-sm text-red-600">
            <AlertCircle className="h-4 w-4" />
            <span>Failed to get AI suggestions. Try refreshing.</span>
          </div>
        </CardContent>
      </Card>
    );
  }

  // No suggestion
  if (!suggestion) {
    return (
      <Card className={cn('', className)}>
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between">
            <CardTitle className="flex items-center gap-2 text-base">
              <Sparkles className="h-4 w-4 text-gray-400" />
              AI Suggestions
            </CardTitle>
            <Button
              variant="outline"
              size="sm"
              onClick={handleRefresh}
              disabled={refreshMutation.isPending}
            >
              <RefreshCw
                className={cn('h-4 w-4 mr-1', refreshMutation.isPending && 'animate-spin')}
              />
              Analyze
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            Click "Analyze" to get AI suggestions for this episode.
          </p>
        </CardContent>
      </Card>
    );
  }

  // Suggestion available
  return (
    <div className={cn('space-y-2', className)}>
      <div className="flex items-center justify-between">
        <AISuggestionBadge
          confidence={suggestion.confidence}
          isAccepted={suggestionStatus === 'accepted'}
        />
        <Button
          variant="ghost"
          size="sm"
          onClick={handleRefresh}
          disabled={refreshMutation.isPending}
          className="h-7"
        >
          <RefreshCw
            className={cn('h-4 w-4', refreshMutation.isPending && 'animate-spin')}
          />
        </Button>
      </div>
      <SuggestionCard
        suggestion={suggestion}
        isApplying={isApplying}
        isAccepted={suggestionStatus === 'accepted'}
        isRejected={suggestionStatus === 'rejected'}
        onAccept={handleAccept}
        onReject={handleReject}
        onPartialAccept={handlePartialAccept}
      />
    </div>
  );
}
