/**
 * TanStack Query hooks for object detection.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useMemo, useState } from 'react';

import { clearDetections,getDetections, runDetection } from '@/api/detection';
import { useDatasetStore, useEditDirtyState,useEpisodeStore } from '@/stores';
import type {
  DetectionFilters,
  DetectionRequest,
  EpisodeDetectionSummary,
} from '@/types/detection';

/**
 * Query key factory for detection.
 */
export const detectionKeys = {
  all: ['detection'] as const,
  episode: (datasetId: string, episodeIdx: number) =>
    [...detectionKeys.all, datasetId, episodeIdx] as const,
};

/**
 * Hook for managing object detection state and operations.
 */
export function useObjectDetection() {
  const queryClient = useQueryClient();
  const currentDataset = useDatasetStore((state) => state.currentDataset);
  const currentEpisode = useEpisodeStore((state) => state.currentEpisode);
  const { isDirty: hasEdits } = useEditDirtyState();

  const datasetId = currentDataset?.id ?? '';
  const episodeIdx = currentEpisode?.meta.index ?? -1;

  const [filters, setFilters] = useState<DetectionFilters>({
    classes: [],
    minConfidence: 0.25,
  });

  const [needsRerun, setNeedsRerun] = useState(false);

  // Track edit dirty state to suggest re-run
  useMemo(() => {
    if (hasEdits) {
      setNeedsRerun(true);
    }
  }, [hasEdits]);

  // Fetch cached detections
  const query = useQuery({
    queryKey: detectionKeys.episode(datasetId, episodeIdx),
    queryFn: () => getDetections(datasetId, episodeIdx),
    enabled: !!datasetId && episodeIdx >= 0,
    staleTime: 5 * 60 * 1000, // 5 minutes
  });

  // Run detection mutation
  const runMutation = useMutation({
    mutationFn: (request: DetectionRequest) => {
      return runDetection(datasetId, episodeIdx, request);
    },
    onSuccess: (data) => {
      queryClient.setQueryData(detectionKeys.episode(datasetId, episodeIdx), data);
      setNeedsRerun(false);
    },
    onError: (error) => {
      // eslint-disable-next-line no-console
      console.error('[useObjectDetection] Detection failed', error);
    },
  });

  // Clear cache mutation
  const clearMutation = useMutation({
    mutationFn: () => clearDetections(datasetId, episodeIdx),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: detectionKeys.episode(datasetId, episodeIdx),
      });
    },
  });

  // Filter detections based on current filters
  const filteredData = useCallback((): EpisodeDetectionSummary | null => {
    if (!query.data) return null;

    const filtered: EpisodeDetectionSummary = {
      ...query.data,
      detections_by_frame: query.data.detections_by_frame.map((frame) => ({
        ...frame,
        detections: frame.detections.filter((det) => {
          const classMatch =
            filters.classes.length === 0 || filters.classes.includes(det.class_name);
          const confMatch = det.confidence >= filters.minConfidence;
          return classMatch && confMatch;
        }),
      })),
      // Preserve existing properties
      total_frames: query.data.total_frames,
      processed_frames: query.data.processed_frames,
      total_detections: 0, // Recalculate below
      class_summary: query.data.class_summary,
    };

    // Recalculate totals
    filtered.total_detections = filtered.detections_by_frame.reduce(
      (sum, frame) => sum + frame.detections.length,
      0
    );

    return filtered;
  }, [query.data, filters]);

  // Get available classes from data
  const availableClasses = useMemo(() => {
    if (!query.data) return [];
    return Object.keys(query.data.class_summary);
  }, [query.data]);

  return {
    data: query.data,
    filteredData: filteredData(),
    isLoading: query.isLoading,
    isRunning: runMutation.isPending,
    error: query.error || runMutation.error,
    needsRerun,
    filters,
    setFilters,
    runDetection: runMutation.mutate,
    clearCache: clearMutation.mutate,
    availableClasses,
  };
}
