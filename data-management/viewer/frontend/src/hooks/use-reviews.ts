import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  createAnnotationRevision,
  createEditRevision,
  createReviewDecision,
  runQualityReview,
} from '@/api/reviews'
import { persistReviewDecisionDraft } from '@/lib/edit-draft-storage'
import type {
  CreateReviewDecisionInput,
  EpisodeEditOperations,
  JsonValue,
  QualityProfile,
  QualityReport,
  ReviewDecision,
} from '@/types'

export const reviewKeys = {
  all: ['reviews'] as const,
  quality: (datasetId: string, episodeIndex: number) =>
    [...reviewKeys.all, 'quality', datasetId, episodeIndex] as const,
  decision: (datasetId: string, episodeIndex: number) =>
    [...reviewKeys.all, 'decision', datasetId, episodeIndex] as const,
}

interface RunQualityInput {
  datasetId: string
  episodeIndex: number
  actorId: string
  sourceFormat: 'lerobot' | 'hdf5'
  profile: QualityProfile
}

function contractId(prefix: string): string {
  return `${prefix}-${crypto.randomUUID()}`
}

function editOperations(edits: EpisodeEditOperations) {
  return Object.entries(edits)
    .filter(([key, value]) => !['datasetId', 'episodeIndex'].includes(key) && value !== undefined)
    .map(([operation, value]) => ({
      operation,
      parameters: { value: value as JsonValue },
    }))
}

export function useReviewQuality(datasetId: string, episodeIndex: number) {
  return useQuery({
    queryKey: reviewKeys.quality(datasetId, episodeIndex),
    queryFn: async () => null as QualityReport | null,
    initialData: null,
    staleTime: Infinity,
    enabled: false,
  })
}

export function useReviewDecision(datasetId: string, episodeIndex: number) {
  return useQuery({
    queryKey: reviewKeys.decision(datasetId, episodeIndex),
    queryFn: async () => null as ReviewDecision | null,
    initialData: null,
    staleTime: Infinity,
    enabled: false,
  })
}

export function useRunQualityReview() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ datasetId, episodeIndex, actorId, sourceFormat, profile }: RunQualityInput) =>
      runQualityReview(datasetId, episodeIndex, {
        runId: contractId('quality'),
        actorId,
        sourceFormat,
        profile,
      }),
    onSuccess: (report, variables) => {
      const queryKey = reviewKeys.quality(variables.datasetId, variables.episodeIndex)
      queryClient.setQueryDefaults(queryKey, { gcTime: 30 * 60 * 1000, staleTime: Infinity })
      queryClient.setQueryData(queryKey, report)
    },
  })
}

export function useCreateReviewDecision() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async ({
      datasetId,
      episodeIndex,
      actorId,
      annotation,
      edits,
      qualityReport,
      decision,
      reasonCodes,
      notes = null,
    }: CreateReviewDecisionInput) => {
      const createdAt = new Date().toISOString()
      const annotationRevision = await createAnnotationRevision(datasetId, episodeIndex, {
        revisionId: contractId('annotation'),
        source: qualityReport.source,
        actorId,
        createdAt,
        annotation: annotation as unknown as Record<string, JsonValue>,
        predecessorRevisionId: null,
      })
      const editRevision = await createEditRevision(datasetId, episodeIndex, {
        revisionId: contractId('edit'),
        source: qualityReport.source,
        actorId,
        createdAt,
        operations: editOperations(edits),
        predecessorRevisionId: null,
      })

      return createReviewDecision(datasetId, episodeIndex, {
        decisionId: contractId('decision'),
        decision,
        reasonCodes,
        notes,
        actorId,
        createdAt,
        source: qualityReport.source,
        annotationRevisionId: annotationRevision.revisionId,
        editRevisionId: editRevision.revisionId,
        qualityRunId: qualityReport.runId,
      })
    },
    onSuccess: (decision: ReviewDecision, variables) => {
      queryClient.setQueryData(
        reviewKeys.decision(variables.datasetId, variables.episodeIndex),
        decision,
      )
      void persistReviewDecisionDraft(
        variables.datasetId,
        variables.episodeIndex,
        variables.actorId,
        null,
      )
    },
  })
}
