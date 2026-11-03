import { beforeEach, describe, expect, it, vi } from 'vitest'

import { jsonResponse } from '@/test-utils/fetch-mocks'

import {
  cancelReleaseJob,
  fetchSourceEpisodeMedia,
  getReleaseArtifact,
  getReleaseCapabilities,
  getReleaseJob,
  getReleaseResult,
  getReleaseSourceInventory,
  getSourceRelease,
  listReleaseJobs,
  listSourceEpisodes,
  listSourceReleases,
  lookupAcceptedRelease,
  previewRelease,
  recoverReleaseJob,
  type ReleaseRequest,
  ReleaseRequestError,
  submitRelease,
} from '../releases'

vi.mock('@/lib/api-client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api-client')>()),
  apiFetch: vi.fn(),
}))

const { apiFetch } = await import('@/lib/api-client')
const mockApiFetch = vi.mocked(apiFetch)

const request: ReleaseRequest = {
  destination: 'local-archive',
  packageId: 'release-a',
  attemptId: 'attempt-1',
  datasetId: 'group-a--source-a',
  datasetName: 'Release A',
  episodeIndices: [7, 2],
  resources: [],
  finalized: true,
}

const confirmation = {
  idempotencyKey: 'key-1',
  previewDigest: 'preview-digest',
  processingDigest: 'processing-digest',
  localDiskAcknowledged: true,
}

function sentBody(callIndex = 0): Record<string, unknown> {
  const init = mockApiFetch.mock.calls[callIndex][1]
  return JSON.parse(String(init?.body)) as Record<string, unknown>
}

beforeEach(() => {
  mockApiFetch.mockReset()
})

describe('getReleaseCapabilities', () => {
  it('GETs the capabilities route and camel-cases the advisory response', async () => {
    mockApiFetch.mockResolvedValueOnce(
      jsonResponse({
        job_store: true,
        planning: true,
        destinations: [{ alias: 'local-archive' }],
        routes: ['local-to-local'],
        capacity: 'unknown',
      }),
    )

    const capabilities = await getReleaseCapabilities()

    expect(mockApiFetch).toHaveBeenCalledWith('/releases/v1/capabilities')
    expect(capabilities).toMatchObject({
      jobStore: true,
      planning: true,
      destinations: [{ alias: 'local-archive' }],
      capacity: 'unknown',
    })
  })
})

describe('getReleaseSourceInventory', () => {
  it('pages the full source population with an encoded dataset id', async () => {
    mockApiFetch.mockResolvedValueOnce(
      jsonResponse({
        items: [{ episode_index: 2 }, { episode_index: 7 }],
        total: 9,
        next_cursor: 2,
      }),
    )

    const page = await getReleaseSourceInventory('group a/source-a', { cursor: 0, limit: 2 })

    expect(mockApiFetch.mock.calls[0][0]).toBe(
      '/releases/v1/inventory/group%20a%2Fsource-a?cursor=0&limit=2',
    )
    expect(page).toMatchObject({
      items: [{ episodeIndex: 2 }, { episodeIndex: 7 }],
      total: 9,
      nextCursor: 2,
    })
  })
})

describe('previewRelease', () => {
  it('POSTs the selection body in the backend snake_case contract and returns the digest', async () => {
    mockApiFetch.mockResolvedValueOnce(
      jsonResponse({
        digest: 'preview-digest',
        reserved_bytes: 1024,
        summary: { processing_digest: 'processing-digest', route: 'copy' },
      }),
    )

    const preview = await previewRelease(request)

    const [path, init] = mockApiFetch.mock.calls[0]
    expect(path).toBe('/releases/v1/preview')
    expect(init).toMatchObject({ method: 'POST' })
    expect(sentBody()).toMatchObject({
      destination: 'local-archive',
      package_id: 'release-a',
      attempt_id: 'attempt-1',
      dataset_id: 'group-a--source-a',
      dataset_name: 'Release A',
      episode_indices: [7, 2],
      finalized: true,
    })
    expect(preview).toMatchObject({
      digest: 'preview-digest',
      reservedBytes: 1024,
      summary: { processingDigest: 'processing-digest', route: 'copy' },
    })
  })
})

describe('getSourceRelease', () => {
  it('converts envelope fields without rewriting immutable saved evidence', async () => {
    const annotations = [{ episode_index: 0, machine_analysis: { source_revision: 'r1' } }]
    const labels = { dataset_id: 'release-a', episodes: { '0': ['accepted'] } }
    const frozenEvidence = {
      source_revision: 'r1',
      resources: [{ snapshot_value: { 'camera_wrist/image': { frame_index: 7 } } }],
    }
    mockApiFetch.mockResolvedValueOnce(
      jsonResponse({
        handle: 'a'.repeat(64),
        schema: 'curated-release-api/v1',
        total_episodes: 1,
        annotations,
        labels,
        frozen_evidence: frozenEvidence,
      }),
    )

    const release = await getSourceRelease('source-a', 'a'.repeat(64))

    expect(release.totalEpisodes).toBe(1)
    expect(release.annotations).toEqual(annotations)
    expect(release.labels).toEqual(labels)
    expect(release.frozenEvidence).toEqual(frozenEvidence)
  })
})

describe('submitRelease', () => {
  it('sends the caller idempotency key and the exact confirmed digests', async () => {
    mockApiFetch.mockResolvedValueOnce(
      jsonResponse({ job_id: 'a'.repeat(32), replayed: false }, 202),
    )

    const accepted = await submitRelease(request, confirmation)

    const [path, init] = mockApiFetch.mock.calls[0]
    expect(path).toBe('/releases/v1/jobs')
    expect(init).toMatchObject({ method: 'POST' })
    expect((init?.headers as Record<string, string>)['Idempotency-Key']).toBe('key-1')
    expect(sentBody()).toMatchObject({
      preview_digest: 'preview-digest',
      processing_digest: 'processing-digest',
      local_disk_acknowledged: true,
    })
    expect(accepted).toEqual({ jobId: 'a'.repeat(32), replayed: false })
  })

  it('refuses to submit without an idempotency key', async () => {
    await expect(submitRelease(request, { ...confirmation, idempotencyKey: '' })).rejects.toThrow(
      /idempotency/i,
    )
    expect(mockApiFetch).not.toHaveBeenCalled()
  })

  it('surfaces the backend release error code from the nested detail object', async () => {
    mockApiFetch.mockResolvedValueOnce(
      jsonResponse({ detail: { schema: 'curated-release-api/v1', code: 'stale_preview' } }, 409),
    )

    await expect(submitRelease(request, confirmation)).rejects.toMatchObject({
      name: 'ReleaseRequestError',
      code: 'stale_preview',
      status: 409,
    })
  })

  it('falls back to a status-derived code when the error body is not JSON', async () => {
    mockApiFetch.mockResolvedValueOnce(new Response('upstream failure', { status: 500 }))

    const failure = await submitRelease(request, confirmation).catch((error: unknown) => error)

    expect(failure).toBeInstanceOf(ReleaseRequestError)
    expect(failure).toMatchObject({ code: 'HTTP_500', status: 500 })
  })
})

describe('retained release routes', () => {
  it.each([
    [() => getReleaseJob('job/a'), '/releases/v1/jobs/job%2Fa'],
    [() => getReleaseResult('job/a'), '/releases/v1/jobs/job%2Fa/result'],
    [() => listSourceReleases('source/a'), '/releases/v1/sources/source%2Fa/releases'],
    [
      () => listSourceEpisodes('source/a', 'handle/a', { cursor: 50, limit: 50 }),
      '/releases/v1/sources/source%2Fa/releases/handle%2Fa/episodes?cursor=50&limit=50',
    ],
    [() => listReleaseJobs({ cursor: 20, limit: 20 }), '/releases/v1/jobs?cursor=20&limit=20'],
    [
      () => listReleaseJobs({ cursor: 0, limit: 20, datasetId: 'source/a' }),
      '/releases/v1/jobs?cursor=0&limit=20&dataset_id=source%2Fa',
    ],
  ] as const)('reads an encoded route without submitting: %s', async (read, path) => {
    mockApiFetch.mockResolvedValue(jsonResponse({ job_id: 'accepted', next_cursor: null }))
    expect(await read()).toEqual({ jobId: 'accepted', nextCursor: null })
    expect(mockApiFetch).toHaveBeenCalledWith(path)
  })

  it.each([
    [cancelReleaseJob, 'cancel'],
    [recoverReleaseJob, 'recover'],
  ] as const)('posts an explicit lifecycle action: %s', async (action, suffix) => {
    mockApiFetch.mockResolvedValue(jsonResponse({ job_id: 'accepted' }))
    expect(await action('job/a')).toEqual({ jobId: 'accepted' })
    expect(mockApiFetch).toHaveBeenCalledWith(`/releases/v1/jobs/job%2Fa/${suffix}`, {
      method: 'POST',
    })
  })

  it('looks up acceptance using a header without resubmitting', async () => {
    mockApiFetch.mockResolvedValue(jsonResponse({ job_id: 'accepted', replayed: true }))
    expect(await lookupAcceptedRelease('key-1234')).toEqual({ jobId: 'accepted', replayed: true })
    expect(mockApiFetch).toHaveBeenCalledWith('/releases/v1/requests', {
      method: 'GET',
      headers: { 'Idempotency-Key': 'key-1234' },
    })
  })

  it('preserves artifact keys and rejects non-object payloads', async () => {
    const artifact = { name: 'manifest', content: { source_revision: 'r1' } }
    mockApiFetch
      .mockResolvedValueOnce(jsonResponse(artifact))
      .mockResolvedValueOnce(jsonResponse([]))
    expect(await getReleaseArtifact('job/a', 'manifest')).toEqual(artifact)
    expect(mockApiFetch).toHaveBeenCalledWith('/releases/v1/jobs/job%2Fa/artifacts/manifest')
    await expect(getReleaseArtifact('job/a', 'manifest')).rejects.toMatchObject({
      code: 'invalid_artifact',
    })
  })

  it('fetches authenticated camera bytes with encoded identifiers', async () => {
    mockApiFetch.mockResolvedValue(
      new Response('video-bytes', { headers: { 'Content-Type': 'video/mp4' } }),
    )
    const blob = await fetchSourceEpisodeMedia('source/a', 'handle/a', 7, 'camera/wrist')
    expect(blob.type).toBe('video/mp4')
    expect(await blob.text()).toBe('video-bytes')
    expect(mockApiFetch).toHaveBeenCalledWith(
      '/releases/v1/sources/source%2Fa/releases/handle%2Fa/episodes/7/media/camera%2Fwrist',
    )
  })

  it.each([
    () => getReleaseArtifact('job', 'manifest'),
    () => getSourceRelease('source', 'handle'),
    () => fetchSourceEpisodeMedia('source', 'handle', 0, 'wrist'),
  ])('propagates a safe error from a retained read', async (read) => {
    mockApiFetch.mockResolvedValue(jsonResponse({ detail: { code: 'forbidden' } }, 403))
    await expect(read()).rejects.toMatchObject({ code: 'forbidden', status: 403 })
  })
})
