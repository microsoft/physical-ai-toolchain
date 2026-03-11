/**
 * Sync queue manager for offline annotations.
 *
 * Handles background synchronization of local changes with the server.
 */

import { handleResponse, mutationHeaders } from '@/lib/api-client';

import {
  getPendingSyncItems,
  removeSyncItem,
  updateAnnotationSyncStatus,
  updateSyncItemRetry,
} from './offline-storage';

export interface SyncQueueItem {
  id: string;
  type: 'create' | 'update' | 'delete';
  datasetId: string;
  episodeId: string;
  annotationId: string;
  payload: unknown;
  createdAt: string;
  retryCount: number;
  lastError?: string;
}

export interface SyncResult {
  success: boolean;
  syncedCount: number;
  failedCount: number;
  errors: Array<{ id: string; error: string }>;
}

const MAX_RETRIES = 3;
const RETRY_DELAY_MS = 1000;

/**
 * Check if the browser is online.
 */
export function isOnline(): boolean {
  return navigator.onLine;
}

/**
 * Wait for online status.
 */
export function waitForOnline(): Promise<void> {
  return new Promise((resolve) => {
    if (isOnline()) {
      resolve();
      return;
    }

    const handleOnline = () => {
      window.removeEventListener('online', handleOnline);
      resolve();
    };

    window.addEventListener('online', handleOnline);
  });
}

/**
 * Process a single sync item.
 */
async function processSyncItem(item: SyncQueueItem): Promise<boolean> {
  try {
    const headers = {
      'Content-Type': 'application/json',
      ...(await mutationHeaders()),
    };

    let response: Response;
    switch (item.type) {
      case 'create':
        response = await fetch(
          `/api/datasets/${item.datasetId}/episodes/${item.episodeId}/annotations`,
          { method: 'POST', headers, body: JSON.stringify(item.payload) }
        );
        break;

      case 'update':
        response = await fetch(
          `/api/annotations/${item.annotationId}`,
          { method: 'PUT', headers, body: JSON.stringify(item.payload) }
        );
        break;

      case 'delete':
        response = await fetch(
          `/api/annotations/${item.annotationId}`,
          { method: 'DELETE', headers: await mutationHeaders() }
        );
        break;
    }

    await handleResponse(response);

    // Mark annotation as synced
    await updateAnnotationSyncStatus(
      item.annotationId,
      'synced',
      new Date().toISOString()
    );

    // Remove from sync queue
    await removeSyncItem(item.id);

    return true;
  } catch (error) {
    const errorMessage =
      error instanceof Error ? error.message : 'Unknown error';

    // Check for conflict (409)
    if (
      error &&
      typeof error === 'object' &&
      'status' in error &&
      (error as { status?: number }).status === 409
    ) {
      await updateAnnotationSyncStatus(item.annotationId, 'conflict');
      await removeSyncItem(item.id);
      return false;
    }

    // Update retry count
    await updateSyncItemRetry(item.id, errorMessage);

    return false;
  }
}

/**
 * Process the sync queue.
 */
export async function processSyncQueue(): Promise<SyncResult> {
  const result: SyncResult = {
    success: true,
    syncedCount: 0,
    failedCount: 0,
    errors: [],
  };

  if (!isOnline()) {
    return result;
  }

  const items = await getPendingSyncItems();

  for (const item of items) {
    // Skip items that have exceeded retry limit
    if (item.retryCount >= MAX_RETRIES) {
      result.failedCount++;
      result.errors.push({
        id: item.id,
        error: `Exceeded max retries: ${item.lastError || 'Unknown error'}`,
      });
      continue;
    }

    const success = await processSyncItem(item);

    if (success) {
      result.syncedCount++;
    } else {
      result.failedCount++;
      if (item.lastError) {
        result.errors.push({ id: item.id, error: item.lastError });
      }
    }

    // Small delay between requests
    await new Promise((resolve) => setTimeout(resolve, RETRY_DELAY_MS));
  }

  result.success = result.failedCount === 0;
  return result;
}

/**
 * Sync queue manager class.
 */
export class SyncQueueManager {
  private isProcessing = false;
  private intervalId: ReturnType<typeof setInterval> | null = null;
  private listeners: Array<(result: SyncResult) => void> = [];

  /**
   * Start automatic sync processing.
   */
  start(intervalMs = 30000): void {
    if (this.intervalId) return;

    // Process immediately
    this.process();

    // Set up interval
    this.intervalId = setInterval(() => {
      this.process();
    }, intervalMs);

    // Listen for online events
    window.addEventListener('online', this.handleOnline);
  }

  /**
   * Stop automatic sync processing.
   */
  stop(): void {
    if (this.intervalId) {
      clearInterval(this.intervalId);
      this.intervalId = null;
    }

    window.removeEventListener('online', this.handleOnline);
  }

  /**
   * Manually trigger sync processing.
   */
  async process(): Promise<SyncResult> {
    if (this.isProcessing) {
      return {
        success: true,
        syncedCount: 0,
        failedCount: 0,
        errors: [],
      };
    }

    this.isProcessing = true;

    try {
      const result = await processSyncQueue();
      this.notifyListeners(result);
      return result;
    } finally {
      this.isProcessing = false;
    }
  }

  /**
   * Add a sync result listener.
   */
  addListener(listener: (result: SyncResult) => void): () => void {
    this.listeners.push(listener);
    return () => {
      this.listeners = this.listeners.filter((l) => l !== listener);
    };
  }

  private handleOnline = (): void => {
    // Process queue when coming back online
    setTimeout(() => this.process(), 1000);
  };

  private notifyListeners(result: SyncResult): void {
    for (const listener of this.listeners) {
      try {
        listener(result);
      } catch {
        // Listener errors are non-fatal
      }
    }
  }
}

// Singleton instance
export const syncQueueManager = new SyncQueueManager();
