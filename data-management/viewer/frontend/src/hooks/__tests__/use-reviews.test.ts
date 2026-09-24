import { act, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  reviewKeys,
  useCreateReviewDecision,
  useReviewDecision,
  useReviewQuality,
  useRunQualityReview,
} from '@/hooks/use-reviews'
import { renderHookWithProviders } from '@/test-utils/render'
import type {
  EpisodeAnnotation,
  EpisodeEditOperations,
  QualityReport,
  ReviewDecision,
} from '@/types'

const api = vi.hoisted(() => ({
  createAnnotationRevision: vi.fn(),
  createEditRevision: vi.fn(),
  createReviewDecision: vi.fn(),
  fetchLatestReviewDecision: vi.fn(),
  fetchLatestReviewQuality: vi.fn(),
  runQualityReview: vi.fn(),
}))

vi.mock('@/api/reviews', () => api)

const source = {
  schemaVersion: '1.0.0',
  datasetId: 'dataset-1',
  episodeIndex: 2,
  sourceFormat: 'lerobot',
  formatVersion: '3.0',
  sourceDigest: 'a'.repeat(64),
  files: [{ relativePath: 'meta/info.json', sizeBytes: 2, sha256: 'b'.repeat(64) }],
}

const qualityReport: QualityReport = {
  schemaVersion: '1.0.0',
  runId: 'quality-1',
  checkSetVersion: '1.0.0',
  source,
  actorId: 'reviewer',
  createdAt: '2026-09-22T12:00:00Z',
  episodeChecks: [],
  packageChecks: [],
}

beforeEach(() => {
  vi.clearAllMocks()
  api.fetchLatestReviewDecision.mockResolvedValue({
    decisionId: 'decision-latest',
    decision: 'accept',
  })
  api.fetchLatestReviewQuality.mockResolvedValue(qualityReport)
  api.runQualityReview.mockResolvedValue(qualityReport)
  api.createAnnotationRevision.mockImplementation(async (_datasetId, _episodeIndex, revision) =>
    Promise.resolve(revision),
  )
  api.createEditRevision.mockImplementation(async (_datasetId, _episodeIndex, revision) =>
    Promise.resolve(revision),
  )
  api.createReviewDecision.mockImplementation(async (_datasetId, _episodeIndex, decision) =>
    Promise.resolve(decision),
  )
})

describe('review hooks', () => {
  it('restores the latest persisted decision for the selected episode', async () => {
    const { result } = renderHookWithProviders(() => useReviewDecision('dataset-1', 2))

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(api.fetchLatestReviewDecision).toHaveBeenCalledWith('dataset-1', 2)
    expect(result.current.data).toMatchObject({ decisionId: 'decision-latest' })
  })

  it('restores the latest persisted quality report for the selected episode', async () => {
    const { result } = renderHookWithProviders(() => useReviewQuality('dataset-1', 2))

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(api.fetchLatestReviewQuality).toHaveBeenCalledWith('dataset-1', 2)
    expect(result.current.data).toEqual(qualityReport)
  })

  it('stores quality reports in TanStack Query state', async () => {
    const { result, queryClient } = renderHookWithProviders(() => useRunQualityReview())

    act(() => {
      result.current.mutate({
        datasetId: 'dataset-1',
        episodeIndex: 2,
        actorId: 'reviewer',
        sourceFormat: 'lerobot',
        profile: {
          profileId: 'workspace-review',
          version: '1.0.0',
          fps: 30,
          timestampToleranceSeconds: 0.02,
          requiredFeatures: [],
          optionalFeatures: [],
          requiredMetadataFiles: [],
          calibration: null,
          requireTaskLabel: true,
        },
      })
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(queryClient.getQueryData(reviewKeys.quality('dataset-1', 2))).toEqual(qualityReport)
  })

  it('creates annotation and edit revisions before the decision references them', async () => {
    const calls: string[] = []
    api.createAnnotationRevision.mockImplementation(async (_datasetId, _episodeIndex, revision) => {
      calls.push('annotation')
      return revision
    })
    api.createEditRevision.mockImplementation(async (_datasetId, _episodeIndex, revision) => {
      calls.push('edit')
      return revision
    })
    api.createReviewDecision.mockImplementation(async (_datasetId, _episodeIndex, decision) => {
      calls.push('decision')
      return decision
    })
    const annotation = { annotatorId: 'reviewer' } as EpisodeAnnotation
    const edits: EpisodeEditOperations = { datasetId: 'dataset-1', episodeIndex: 2 }
    const { result } = renderHookWithProviders(() => useCreateReviewDecision())

    act(() => {
      result.current.mutate({
        datasetId: 'dataset-1',
        episodeIndex: 2,
        actorId: 'reviewer',
        annotation,
        edits,
        qualityReport,
        decision: 'accept',
        reasonCodes: ['evidence-reviewed'],
      })
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(calls).toEqual(['annotation', 'edit', 'decision'])
    expect(api.createReviewDecision).toHaveBeenCalledWith(
      'dataset-1',
      2,
      expect.objectContaining({
        annotationRevisionId: expect.stringMatching(/^annotation-/),
        editRevisionId: expect.stringMatching(/^edit-/),
        qualityRunId: 'quality-1',
      }),
    )
  })

  it('links new evidence to the latest review decision revisions', async () => {
    const annotation = { annotatorId: 'reviewer' } as EpisodeAnnotation
    const edits: EpisodeEditOperations = { datasetId: 'dataset-1', episodeIndex: 2 }
    const latestDecision = {
      decisionId: 'decision-previous',
      source,
      annotationRevisionId: 'annotation-previous',
      editRevisionId: 'edit-previous',
    } as ReviewDecision
    const { result, queryClient } = renderHookWithProviders(() => useCreateReviewDecision())
    queryClient.setQueryData(reviewKeys.decision('dataset-1', 2), latestDecision)

    act(() => {
      result.current.mutate({
        datasetId: 'dataset-1',
        episodeIndex: 2,
        actorId: 'reviewer',
        annotation,
        edits,
        qualityReport,
        decision: 'accept',
        reasonCodes: ['evidence-reviewed'],
      })
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(api.createAnnotationRevision).toHaveBeenCalledWith(
      'dataset-1',
      2,
      expect.objectContaining({ predecessorRevisionId: 'annotation-previous' }),
    )
    expect(api.createEditRevision).toHaveBeenCalledWith(
      'dataset-1',
      2,
      expect.objectContaining({ predecessorRevisionId: 'edit-previous' }),
    )
  })
})
