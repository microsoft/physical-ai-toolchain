/**
 * Hook for fetching and managing AI analysis suggestions.
 */

import { useMutation,useQuery } from '@tanstack/react-query';

import {
  analyzeTrajectory,
  type AnnotationSuggestion,
  type AnomalyDetectionRequest,
  type AnomalyDetectionResponse,
  detectAnomalies,
  getAnnotationSuggestion,
  type SuggestAnnotationRequest,
  type TrajectoryData,
  type TrajectoryMetrics,
} from '@/api/ai-analysis';

/** Query key factory for AI analysis */
export const aiAnalysisKeys = {
  all: ['ai-analysis'] as const,
  suggestion: (datasetId: string, episodeId: string, data?: unknown) =>
    [...aiAnalysisKeys.all, 'suggestion', datasetId, episodeId, data] as const,
  trajectory: (datasetId: string, episodeId: string, data?: unknown) =>
    [...aiAnalysisKeys.all, 'trajectory', datasetId, episodeId, data] as const,
  anomalies: (datasetId: string, episodeId: string, data?: unknown) =>
    [...aiAnalysisKeys.all, 'anomalies', datasetId, episodeId, data] as const,
};

/** Options for AI suggestion hook */
export interface UseAISuggestionOptions {
  datasetId: string;
  episodeId: string;
  trajectoryData?: SuggestAnnotationRequest;
  enabled?: boolean;
}

/**
 * Hook for fetching AI annotation suggestions.
 */
export function useAISuggestion({
  datasetId,
  episodeId,
  trajectoryData,
  enabled = true,
}: UseAISuggestionOptions) {
  return useQuery({
    queryKey: aiAnalysisKeys.suggestion(datasetId, episodeId, trajectoryData),
    queryFn: () => {
      if (!trajectoryData) {
        throw new Error('Trajectory data required');
      }
      return getAnnotationSuggestion(trajectoryData);
    },
    enabled: enabled && !!trajectoryData && trajectoryData.positions.length >= 3,
    staleTime: 5 * 60 * 1000, // 5 minutes
    gcTime: 10 * 60 * 1000, // 10 minutes
  });
}

/** Options for trajectory analysis hook */
export interface UseTrajectoryAnalysisOptions {
  datasetId: string;
  episodeId: string;
  trajectoryData?: TrajectoryData;
  enabled?: boolean;
}

/**
 * Hook for fetching trajectory quality metrics.
 */
export function useTrajectoryAnalysis({
  datasetId,
  episodeId,
  trajectoryData,
  enabled = true,
}: UseTrajectoryAnalysisOptions) {
  return useQuery({
    queryKey: aiAnalysisKeys.trajectory(datasetId, episodeId, trajectoryData),
    queryFn: () => {
      if (!trajectoryData) {
        throw new Error('Trajectory data required');
      }
      return analyzeTrajectory(trajectoryData);
    },
    enabled: enabled && !!trajectoryData && trajectoryData.positions.length >= 3,
    staleTime: 5 * 60 * 1000,
    gcTime: 10 * 60 * 1000,
  });
}

/** Options for anomaly detection hook */
export interface UseAnomalyDetectionOptions {
  datasetId: string;
  episodeId: string;
  trajectoryData?: AnomalyDetectionRequest;
  enabled?: boolean;
}

/**
 * Hook for detecting anomalies in trajectory.
 */
export function useAnomalyDetection({
  datasetId,
  episodeId,
  trajectoryData,
  enabled = true,
}: UseAnomalyDetectionOptions) {
  return useQuery({
    queryKey: aiAnalysisKeys.anomalies(datasetId, episodeId, trajectoryData),
    queryFn: () => {
      if (!trajectoryData) {
        throw new Error('Trajectory data required');
      }
      return detectAnomalies(trajectoryData);
    },
    enabled: enabled && !!trajectoryData && trajectoryData.positions.length >= 3,
    staleTime: 5 * 60 * 1000,
    gcTime: 10 * 60 * 1000,
  });
}

/**
 * Hook for manually triggering AI analysis.
 */
export function useRequestAISuggestion() {
  return useMutation({
    mutationFn: getAnnotationSuggestion,
    onSuccess: (_data) => {
      // Result available via mutation return value
    },
  });
}

export type {
  AnnotationSuggestion,
  AnomalyDetectionRequest,
  AnomalyDetectionResponse,
  SuggestAnnotationRequest,
  TrajectoryData,
  TrajectoryMetrics,
};
