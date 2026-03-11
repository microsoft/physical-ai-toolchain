/**
 * Offline status indicator component.
 */

import {
  AlertTriangle,
  Check,
  Cloud,
  Loader2,
  RefreshCw,
  Wifi,
  WifiOff,
} from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import { Progress } from '@/components/ui/progress';
import { useOfflineAnnotations } from '@/hooks/use-offline-annotations';
import { getSemanticToneClasses, getSyncStatusTone } from '@/lib/semantic-state';
import { cn } from '@/lib/utils';

export interface OfflineIndicatorProps {
  /** Additional class names */
  className?: string;
}

/**
 * Displays offline status and sync information.
 */
export function OfflineIndicator({ className }: OfflineIndicatorProps) {
  const {
    isOnline,
    pendingCount,
    isSyncing,
    lastSyncResult,
    sync,
  } = useOfflineAnnotations();

  const handleSync = async () => {
    await sync();
  };

  // Determine status
  const hasErrors = lastSyncResult?.failedCount && lastSyncResult.failedCount > 0;
  const hasPending = pendingCount > 0;
  const pendingTone = getSyncStatusTone('pending');

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          className={cn('gap-2', className)}
        >
          {!isOnline ? (
            <>
              <WifiOff className={cn('h-4 w-4', getSemanticToneClasses('icon', 'danger'))} />
              <span className={getSemanticToneClasses('text', 'danger')}>Offline</span>
            </>
          ) : isSyncing ? (
            <>
              <Loader2
                className={cn(
                  'h-4 w-4 animate-spin',
                  getSemanticToneClasses('icon', pendingTone)
                )}
              />
              <span className={getSemanticToneClasses('text', pendingTone)}>Syncing...</span>
            </>
          ) : hasPending ? (
            <>
              <Cloud className={cn('h-4 w-4', getSemanticToneClasses('icon', pendingTone))} />
              <Badge variant="status" tone={pendingTone} className="h-5 px-1.5">
                {pendingCount}
              </Badge>
            </>
          ) : hasErrors ? (
            <>
              <AlertTriangle className={cn('h-4 w-4', getSemanticToneClasses('icon', 'warning'))} />
              <span className={getSemanticToneClasses('text', 'warning')}>Sync errors</span>
            </>
          ) : (
            <>
              <Wifi className={cn('h-4 w-4', getSemanticToneClasses('icon', 'success'))} />
              <Check className={cn('h-3 w-3', getSemanticToneClasses('icon', 'success'))} />
            </>
          )}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-72">
        <div className="space-y-3">
          {/* Connection status */}
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium">Connection Status</span>
            <Badge variant="status" tone={isOnline ? 'success' : 'danger'}>
              {isOnline ? (
                <>
                  <Wifi className="h-3 w-3 mr-1" />
                  Online
                </>
              ) : (
                <>
                  <WifiOff className="h-3 w-3 mr-1" />
                  Offline
                </>
              )}
            </Badge>
          </div>

          {/* Pending changes */}
          <div className="space-y-1">
            <div className="flex items-center justify-between text-sm">
              <span className="text-muted-foreground">Pending changes</span>
              <span className="font-medium">{pendingCount}</span>
            </div>
            {hasPending && (
              <Progress value={0} className="h-1.5" />
            )}
          </div>

          {/* Last sync result */}
          {lastSyncResult && (
            <div className="text-xs text-muted-foreground space-y-1">
              <p>
                Last sync: {lastSyncResult.syncedCount} synced
                {lastSyncResult.failedCount > 0 && (
                  <span className={getSemanticToneClasses('text', 'danger')}>
                    , {lastSyncResult.failedCount} failed
                  </span>
                )}
              </p>
              {lastSyncResult.errors.length > 0 && (
                <div
                  className={cn(
                    'max-h-20 overflow-auto rounded border p-2',
                    getSemanticToneClasses('surface', 'danger'),
                    'text-xs'
                  )}
                >
                  {lastSyncResult.errors.slice(0, 3).map((err) => (
                    <p key={err.id} className="truncate">
                      {err.error}
                    </p>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Sync button */}
          <Button
            onClick={handleSync}
            disabled={!isOnline || isSyncing || pendingCount === 0}
            className="w-full"
            size="sm"
          >
            {isSyncing ? (
              <>
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                Syncing...
              </>
            ) : (
              <>
                <RefreshCw className="h-4 w-4 mr-2" />
                Sync Now
              </>
            )}
          </Button>

          {/* Offline mode info */}
          {!isOnline && (
            <p className="text-xs text-muted-foreground">
              Changes are saved locally and will sync when you're back online.
            </p>
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}
