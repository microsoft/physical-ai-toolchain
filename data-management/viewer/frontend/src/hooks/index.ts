/**
 * Custom hooks for data fetching and state management.
 */

export {
  aiAnalysisKeys,
  type AnnotationSuggestion,
  type AnomalyDetectionResponse,
  type SuggestAnnotationRequest,
  type TrajectoryMetrics,
  useAISuggestion,
  useAnomalyDetection,
  useRequestAISuggestion,
  useTrajectoryAnalysis,
} from './use-ai-analysis'
export { useAnnotationWorkflow } from './use-annotation-workflow'
export {
  annotationKeys,
  useAnnotationSummary,
  useAutoAnalysis,
  useCurrentEpisodeAutoAnalysis,
  useDeleteAnnotation,
  useEpisodeAnnotations,
  useSaveAnnotation,
  useSaveCurrentAnnotation,
} from './use-annotations'
export { useBatchSelection, useBatchSelectionStore } from './use-batch-selection'
export {
  type ActivityItem,
  type AnnotatorStats,
  dashboardKeys,
  type DashboardStats,
  useDashboardMetrics,
  useDashboardStats,
} from './use-dashboard'
export {
  capabilityKeys,
  datasetKeys,
  useCapabilities,
  useDataset,
  useDatasets,
  useEpisode,
  useEpisodes,
} from './use-datasets'
export {
  episodeKeys,
  useCurrentEpisode,
  useEpisodeList,
  useEpisodeNavigationWithPrefetch,
} from './use-episodes'
export { useExport } from './use-export'
export {
  jointConfigKeys,
  useJointConfig,
  useJointConfigDefaults,
  useSaveJointConfig,
  useSaveJointConfigDefaults,
} from './use-joint-config'
export {
  formatShortcut,
  type KeyboardShortcut,
  useAnnotationShortcuts,
  useKeyboardShortcuts,
} from './use-keyboard-shortcuts'
export {
  labelKeys,
  useAddLabelOption,
  useCurrentEpisodeLabels,
  useDatasetLabels,
  useRemoveLabelOption,
  useSaveEpisodeLabels,
} from './use-labels'
export { detectionKeys, useObjectDetection } from './use-object-detection'
export {
  type OfflineAnnotation,
  useOfflineAnnotations,
  type UseOfflineAnnotationsResult,
} from './use-offline-annotations'
