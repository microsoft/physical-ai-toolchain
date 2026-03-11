/**
 * TanStack Query hooks for dataset data fetching.
 */

import { useQuery } from '@tanstack/react-query';
import { useEffect } from 'react';

import { fetchCacheStats, fetchCapabilities,fetchDataset, fetchDatasets, fetchEpisode, fetchEpisodes } from '@/lib/api-client';
import { useDatasetStore } from '@/stores';

/**
 * Query key factory for datasets.
 */
export const datasetKeys = {
  all: ['datasets'] as const,
  lists: () => [...datasetKeys.all, 'list'] as const,
  list: () => [...datasetKeys.lists()] as const,
  details: () => [...datasetKeys.all, 'detail'] as const,
  detail: (id: string) => [...datasetKeys.details(), id] as const,
  episodes: (datasetId: string) =>
    [...datasetKeys.detail(datasetId), 'episodes'] as const,
  episode: (datasetId: string, episodeIndex: number) =>
    [...datasetKeys.episodes(datasetId), episodeIndex] as const,
};

export const capabilityKeys = {
  all: ['capabilities'] as const,
  detail: (datasetId: string) => [...capabilityKeys.all, datasetId] as const,
};

/**
 * Hook to fetch all datasets.
 *
 * @example
 * ```tsx
 * const { data: datasets, isLoading, error } = useDatasets();
 * ```
 */
export function useDatasets() {
  const setDatasets = useDatasetStore((state) => state.setDatasets);
  const setLoading = useDatasetStore((state) => state.setLoading);
  const setError = useDatasetStore((state) => state.setError);

  const query = useQuery({
    queryKey: datasetKeys.list(),
    queryFn: fetchDatasets,
    staleTime: 30 * 1000,
    refetchInterval: 30 * 1000,
    refetchOnWindowFocus: true,
    refetchOnReconnect: true,
  });

  // Sync query state with Zustand store
  useEffect(() => {
    setLoading(query.isLoading);
  }, [query.isLoading, setLoading]);

  useEffect(() => {
    if (query.data) {
      setDatasets(query.data);
    }
  }, [query.data, setDatasets]);

  useEffect(() => {
    if (query.error) {
      setError(query.error.message);
    }
  }, [query.error, setError]);

  return query;
}

/**
 * Hook to fetch a specific dataset.
 *
 * @param datasetId - Dataset ID to fetch
 *
 * @example
 * ```tsx
 * const { data: dataset, isLoading } = useDataset('my-dataset');
 * ```
 */
export function useDataset(datasetId: string | undefined) {
  return useQuery({
    queryKey: datasetKeys.detail(datasetId ?? ''),
    queryFn: () => fetchDataset(datasetId!),
    enabled: !!datasetId,
    staleTime: 5 * 60 * 1000,
  });
}

/**
 * Options for episode list filtering.
 */
export interface UseEpisodesOptions {
  offset?: number;
  limit?: number;
  hasAnnotations?: boolean;
  taskIndex?: number;
}

/**
 * Hook to fetch episodes for a dataset.
 *
 * @param datasetId - Dataset ID
 * @param options - Filtering options
 *
 * @example
 * ```tsx
 * const { data: episodes } = useEpisodes('my-dataset', { limit: 50 });
 * ```
 */
export function useEpisodes(
  datasetId: string | undefined,
  options?: UseEpisodesOptions
) {
  return useQuery({
    queryKey: [...datasetKeys.episodes(datasetId ?? ''), options],
    queryFn: () => fetchEpisodes(datasetId!, options),
    enabled: !!datasetId,
    staleTime: 1 * 60 * 1000, // 1 minute
    gcTime: 30 * 60 * 1000, // 30 minutes
  });
}

/**
 * Hook to fetch a specific episode.
 *
 * @param datasetId - Dataset ID
 * @param episodeIndex - Episode index
 *
 * @example
 * ```tsx
 * const { data: episode } = useEpisode('my-dataset', 42);
 * ```
 */
export function useEpisode(
  datasetId: string | undefined,
  episodeIndex: number | undefined
) {
  return useQuery({
    queryKey: datasetKeys.episode(datasetId ?? '', episodeIndex ?? -1),
    queryFn: () => fetchEpisode(datasetId!, episodeIndex!),
    enabled: !!datasetId && episodeIndex !== undefined && episodeIndex >= 0,
    staleTime: 30 * 1000, // 30 seconds
    gcTime: 30 * 60 * 1000, // 30 minutes
  });
}

/**
 * Hook to fetch capabilities for a dataset.
 *
 * @param datasetId - Dataset ID
 */
export function useCapabilities(datasetId: string | undefined) {
  return useQuery({
    queryKey: capabilityKeys.detail(datasetId ?? ''),
    queryFn: () => fetchCapabilities(datasetId!),
    enabled: !!datasetId,
    staleTime: 5 * 60 * 1000, // 5 minutes
  });
}

export const cacheStatsKeys = {
  all: ['cache-stats'] as const,
};

/**
 * Hook to fetch episode cache performance stats.
 * Polls every 10 seconds when diagnostics are visible.
 */
export function useCacheStats(enabled: boolean) {
  return useQuery({
    queryKey: cacheStatsKeys.all,
    queryFn: fetchCacheStats,
    enabled,
    staleTime: 5 * 1000,
    refetchInterval: 10 * 1000,
  });
}
