import { CheckCircle2, Loader2,XCircle } from 'lucide-react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Progress } from '@/components/ui/progress';
import type { ExportProgress as ExportProgressType, ExportResult } from '@/types';

interface ExportProgressProps {
  progress: ExportProgressType | null;
  result: ExportResult | null;
  error: string | null;
}

/**
 * Displays export progress from SSE stream with status indicators
 */
export function ExportProgress({ progress, result, error }: ExportProgressProps) {
  if (error) {
    return (
      <Alert variant="destructive">
        <XCircle className="h-4 w-4" />
        <AlertTitle>Export Failed</AlertTitle>
        <AlertDescription>{error}</AlertDescription>
      </Alert>
    );
  }

  if (result?.success) {
    return (
      <Alert>
        <CheckCircle2 className="h-4 w-4 text-green-500" />
        <AlertTitle>Export Complete</AlertTitle>
        <AlertDescription>
          Successfully exported {result.stats?.totalEpisodes ?? 0} episode(s) to{' '}
          {result.outputFiles?.length ?? 0} file(s).
        </AlertDescription>
      </Alert>
    );
  }

  if (result && !result.success) {
    return (
      <Alert variant="destructive">
        <XCircle className="h-4 w-4" />
        <AlertTitle>Export Failed</AlertTitle>
        <AlertDescription>{result.error ?? 'Unknown error'}</AlertDescription>
      </Alert>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <Loader2 className="h-4 w-4 animate-spin" />
        <span className="text-sm text-muted-foreground">
          {progress?.status ?? 'Preparing export...'}
        </span>
      </div>

      {progress && (
        <>
          <div className="space-y-1">
            <div className="flex justify-between text-xs text-muted-foreground">
              <span>
                Episode {progress.currentEpisode} of {progress.totalEpisodes}
              </span>
              <span>{Math.round(progress.percentage)}%</span>
            </div>
            <Progress value={progress.percentage} className="h-2" />
          </div>

          <div className="text-xs text-muted-foreground">
            Frame {progress.currentFrame} of {progress.totalFrames}
          </div>
        </>
      )}
    </div>
  );
}
