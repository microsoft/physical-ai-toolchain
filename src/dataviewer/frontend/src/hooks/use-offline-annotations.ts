/**
 * Hook for offline-first annotation with sync queue.
 */

import { useQueryClient } from '@tanstack/react-query';
import { useCallback,useEffect, useState } from 'react';

import {
  addToSyncQueue,
  deleteAnnotationLocal,
  getAnnotationLocal,
  getAnnotationsBySyncStatus,
  saveAnnotationLocal,
} from '@/lib/offline-storage';
import {
  isOnline,
  syncQueueManager,
  type SyncResult,
} from '@/lib/sync-queue';

import { annotationKeys } from './use-annotations';

export interface OfflineAnnotation {
  id: string;
  datasetId: string;
  episodeId: string;
  data: unknown;
  syncStatus: 'synced' | 'pending' | 'conflict';
  localUpdatedAt: string;
}

export interface UseOfflineAnnotationsResult {
  /** Whether the device is online */
  isOnline: boolean;
  /** Number of pending sync items */
  pendingCount: number;
  /** Whether sync is in progress */
  isSyncing: boolean;
  /** Last sync result */
  lastSyncResult: SyncResult | null;
  /** Save annotation locally */
  saveLocal: (
    datasetId: string,
    episodeId: string,
    annotationId: string,
    data: unknown
  ) => Promise<void>;
  /** Get local annotation */
  getLocal: (annotationId: string) => Promise<OfflineAnnotation | undefined>;
  /** Get pending annotations */
  getPending: () => Promise<OfflineAnnotation[]>;
  /** Delete local annotation */
  deleteLocal: (annotationId: string) => Promise<void>;
  /** Trigger manual sync */
  sync: () => Promise<SyncResult>;
  /** Start automatic sync */
  startSync: () => void;
  /** Stop automatic sync */
  stopSync: () => void;
}

/**
 * Hook for offline-first annotation management.
 */
export function useOfflineAnnotations(): UseOfflineAnnotationsResult {
  const queryClient = useQueryClient();
  const [online, setOnline] = useState(isOnline());
  const [pendingCount, setPendingCount] = useState(0);
  const [isSyncing, setIsSyncing] = useState(false);
  const [lastSyncResult, setLastSyncResult] = useState<SyncResult | null>(null);

  // Listen for online/offline events
  useEffect(() => {
    const handleOnline = () => setOnline(true);
    const handleOffline = () => setOnline(false);

    window.addEventListener('online', handleOnline);
    window.addEventListener('offline', handleOffline);

    return () => {
      window.removeEventListener('online', handleOnline);
      window.removeEventListener('offline', handleOffline);
    };
  }, []);

  // Update pending count
  const updatePendingCount = useCallback(async () => {
    try {
      const pending = await getAnnotationsBySyncStatus('pending');
      setPendingCount(pending.length);
    } catch {
      // Silently ignore count refresh failures
    }
  }, []);

  // Initial count
  useEffect(() => {
    updatePendingCount();
  }, [updatePendingCount]);

  // Listen for sync results
  useEffect(() => {
    const unsubscribe = syncQueueManager.addListener((result) => {
      setLastSyncResult(result);
      updatePendingCount();

      // Invalidate queries if items were synced
      if (result.syncedCount > 0) {
        queryClient.invalidateQueries({ queryKey: annotationKeys.all });
      }
    });

    return unsubscribe;
  }, [queryClient, updatePendingCount]);

  const saveLocal = useCallback(
    async (
      datasetId: string,
      episodeId: string,
      annotationId: string,
      data: unknown
    ): Promise<void> => {
      // Save locally
      await saveAnnotationLocal(datasetId, episodeId, annotationId, data, 'pending');

      // Add to sync queue
      await addToSyncQueue('update', datasetId, episodeId, annotationId, data);

      // Update count
      await updatePendingCount();

      // Try to sync immediately if online
      if (isOnline()) {
        syncQueueManager.process();
      }
    },
    [updatePendingCount]
  );

  const getLocal = useCallback(
    async (annotationId: string): Promise<OfflineAnnotation | undefined> => {
      const annotation = await getAnnotationLocal(annotationId);
      if (!annotation) return undefined;

      return {
        id: annotation.id,
        datasetId: annotation.datasetId,
        episodeId: annotation.episodeId,
        data: annotation.data,
        syncStatus: annotation.syncStatus,
        localUpdatedAt: annotation.localUpdatedAt,
      };
    },
    []
  );

  const getPending = useCallback(async (): Promise<OfflineAnnotation[]> => {
    const annotations = await getAnnotationsBySyncStatus('pending');
    return annotations.map((a) => ({
      id: a.id,
      datasetId: a.datasetId,
      episodeId: a.episodeId,
      data: a.data,
      syncStatus: a.syncStatus,
      localUpdatedAt: a.localUpdatedAt,
    }));
  }, []);

  const deleteLocal = useCallback(
    async (annotationId: string): Promise<void> => {
      const annotation = await getAnnotationLocal(annotationId);
      if (annotation) {
        // Add delete to sync queue
        await addToSyncQueue(
          'delete',
          annotation.datasetId,
          annotation.episodeId,
          annotationId,
          null
        );
      }

      await deleteAnnotationLocal(annotationId);
      await updatePendingCount();
    },
    [updatePendingCount]
  );

  const sync = useCallback(async (): Promise<SyncResult> => {
    setIsSyncing(true);
    try {
      const result = await syncQueueManager.process();
      setLastSyncResult(result);
      return result;
    } finally {
      setIsSyncing(false);
    }
  }, []);

  const startSync = useCallback(() => {
    syncQueueManager.start();
  }, []);

  const stopSync = useCallback(() => {
    syncQueueManager.stop();
  }, []);

  return {
    isOnline: online,
    pendingCount,
    isSyncing,
    lastSyncResult,
    saveLocal,
    getLocal,
    getPending,
    deleteLocal,
    sync,
    startSync,
    stopSync,
  };
}
