import { beforeEach, describe, expect, it } from 'vitest'

import {
  createAnnotationRevision,
  createEditRevision,
  createReviewDecision,
  fetchLatestReviewDecision,
  fetchLatestReviewQuality,
  runQualityReview,
} from '@/api/reviews'
import { _resetCsrfToken } from '@/lib/api-client'
import { installFetchMock, jsonResponse, mockFetch } from '@/test-utils/fetch-mocks'

const source = {
  schemaVersion: '1.0.0',
  datasetId: 'dataset-1',
  episodeIndex: 2,
  sourceFormat: 'lerobot',
  formatVersion: '3.0',
  sourceDigest: 'a'.repeat(64),
  files: [{ relativePath: 'meta/info.json', sizeBytes: 2, sha256: 'b'.repeat(64) }],
}

beforeEach(() => {
  _resetCsrfToken()
  installFetchMock()
})

describe('review API', () => {
  it('fetches the latest persisted review decision for an episode', async () => {
    installFetchMock({ csrf: false })
    mockFetch.mockResolvedValueOnce(
      jsonResponse({
        decision_id: 'decision-latest',
        decision: 'accept',
        reason_codes: ['evidence-reviewed'],
        quality_run_id: 'quality-latest',
      }),
    )

    const decision = await fetchLatestReviewDecision('dataset-1', 2)

    expect(mockFetch.mock.calls[0][0]).toBe(
      '/api/datasets/dataset-1/episodes/2/review/decisions/latest',
    )
    expect(decision?.decisionId).toBe('decision-latest')
  })

  it('fetches the latest persisted quality report for an episode', async () => {
    installFetchMock({ csrf: false })
    mockFetch.mockResolvedValueOnce(
      jsonResponse({
        run_id: 'quality-latest',
        check_set_version: '1.0.0',
        source,
        actor_id: 'reviewer',
        created_at: '2026-09-24T08:00:00Z',
        episode_checks: [],
        package_checks: [],
      }),
    )

    const report = await fetchLatestReviewQuality('dataset-1', 2)

    expect(mockFetch.mock.calls[0][0]).toBe(
      '/api/datasets/dataset-1/episodes/2/review/quality-reports/latest',
    )
    expect(report?.runId).toBe('quality-latest')
  })

  it('uses the P06 review routes and transforms immutable contracts', async () => {
    mockFetch
      .mockResolvedValueOnce(
        jsonResponse({
          run_id: 'quality-1',
          check_set_version: '1.0.0',
          source: {
            schema_version: '1.0.0',
            dataset_id: 'dataset-1',
            episode_index: 2,
            source_format: 'lerobot',
            format_version: '3.0',
            source_digest: 'a'.repeat(64),
            files: [
              {
                relative_path: 'meta/info.json',
                size_bytes: 2,
                sha256: 'b'.repeat(64),
              },
            ],
          },
          actor_id: 'reviewer',
          created_at: '2026-09-22T12:00:00Z',
          episode_checks: [
            {
              check_id: 'timestamps.contiguous',
              required: true,
              outcome: 'fail',
              reason_codes: ['timestamp-gap'],
            },
          ],
          package_checks: [],
        }),
      )
      .mockResolvedValueOnce(jsonResponse({ revision_id: 'annotation-1', source }))
      .mockResolvedValueOnce(jsonResponse({ revision_id: 'edit-1', source }))
      .mockResolvedValueOnce(
        jsonResponse({
          decision_id: 'decision-1',
          decision: 'reject',
          reason_codes: ['needs-recapture'],
          quality_run_id: 'quality-1',
        }),
      )

    const quality = await runQualityReview('dataset-1', 2, {
      runId: 'quality-1',
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
    await createAnnotationRevision('dataset-1', 2, {
      revisionId: 'annotation-1',
      source,
      actorId: 'reviewer',
      createdAt: '2026-09-22T12:01:00Z',
      annotation: { notes: 'checked' },
      predecessorRevisionId: null,
    })
    await createEditRevision('dataset-1', 2, {
      revisionId: 'edit-1',
      source,
      actorId: 'reviewer',
      createdAt: '2026-09-22T12:01:00Z',
      operations: [],
      predecessorRevisionId: null,
    })
    await createReviewDecision('dataset-1', 2, {
      decisionId: 'decision-1',
      decision: 'reject',
      reasonCodes: ['needs-recapture'],
      notes: null,
      actorId: 'reviewer',
      createdAt: '2026-09-22T12:01:00Z',
      source,
      annotationRevisionId: 'annotation-1',
      editRevisionId: 'edit-1',
      qualityRunId: 'quality-1',
    })

    expect(quality.episodeChecks[0]).toMatchObject({
      checkId: 'timestamps.contiguous',
      outcome: 'fail',
      reasonCodes: ['timestamp-gap'],
    })
    expect(mockFetch.mock.calls.slice(1).map(([url]) => url)).toEqual([
      '/api/datasets/dataset-1/episodes/2/review/quality-runs',
      '/api/datasets/dataset-1/episodes/2/review/annotation-revisions',
      '/api/datasets/dataset-1/episodes/2/review/edit-revisions',
      '/api/datasets/dataset-1/episodes/2/review/decisions',
    ])
    expect(JSON.parse(String(mockFetch.mock.calls[1][1].body))).toMatchObject({
      run_id: 'quality-1',
      actor_id: 'reviewer',
      source_format: 'lerobot',
      profile: {
        profile_id: 'workspace-review',
        timestamp_tolerance_seconds: 0.02,
        required_features: [],
        require_task_label: true,
      },
    })
    expect(JSON.parse(String(mockFetch.mock.calls[4][1].body))).toMatchObject({
      decision_id: 'decision-1',
      reason_codes: ['needs-recapture'],
      annotation_revision_id: 'annotation-1',
      edit_revision_id: 'edit-1',
      quality_run_id: 'quality-1',
    })
  })
})
