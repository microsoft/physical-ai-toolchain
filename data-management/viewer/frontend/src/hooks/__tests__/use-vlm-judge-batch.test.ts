import { act, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  applyOutcomeLabel,
  outcomeToLabel,
  useJudgeDataset,
  useVlmJudgeBatch,
} from '@/hooks/use-vlm-judge-batch'
import { _resetCsrfToken } from '@/lib/api-client'
import { clearPersistedEditDraftsForTests, persistLabelDraft } from '@/lib/edit-draft-storage'
import {
  useAnnotationStore,
  useDatasetStore,
  useEditStore,
  useEpisodeStore,
  useLabelStore,
} from '@/stores'
import { TEST_CSRF_TOKEN } from '@/test-utils/constants'
import { installFetchMock, jsonResponse, mockFetch } from '@/test-utils/fetch-mocks'
import { createTestQueryClient, renderHookWithProviders } from '@/test-utils/render'

let queryClient = createTestQueryClient()

function routeFetch(outcomes: Record<number, boolean | null>) {
  mockFetch.mockImplementation((url: string, init?: RequestInit) => {
    const target = String(url)
    if (target.includes('/csrf-token')) {
      return Promise.resolve(jsonResponse({ csrf_token: TEST_CSRF_TOKEN }))
    }
    if (target.endsWith('/judge/jobs'))
      return Promise.resolve(jsonResponse({ id: 'durable', status: 'queued', total: 2 }, 202))
    const snapshotMatch = target.match(/episodes\/(\d+)\/judge\/snapshot$/)
    if (snapshotMatch)
      return Promise.resolve(
        jsonResponse({
          snapshot_id: 'a'.repeat(64),
          dataset_id: 'ds-1',
          episode_index: Number(snapshotMatch[1]),
          principal_scope_id: 'principal-one',
          source_id: 'source',
          source_revision: 'one',
          annotation_revision: null,
          edit_revision: null,
          annotation_author_id: null,
          instruction: 'Saved',
          instruction_origin: 'dataset',
        }),
      )
    const judgeMatch = target.match(/episodes\/(\d+)\/judge$/)
    if (judgeMatch) {
      return Promise.resolve(
        jsonResponse({ id: `job-${judgeMatch[1]}`, dataset_id: 'ds-1', status: 'queued' }, 202),
      )
    }
    const statusMatch = target.match(/judge\/jobs\/job-(\d+)$/)
    if (statusMatch) {
      const idx = Number(statusMatch[1])
      return Promise.resolve(
        jsonResponse({
          id: `job-${idx}`,
          dataset_id: 'ds-1',
          status: 'succeeded',
          config: { process_method: 'gvl' },
          targets: [
            {
              episode_index: idx,
              status: 'succeeded',
              cached: false,
              result: {
                episode_id: `ds-1/episode_${String(idx).padStart(6, '0')}`,
                instruction: 'Pick',
                judge_model: 'Qwen/Qwen3-VL-4B-Instruct',
                prompt_version: 'outcome-mcq-v1',
                n_frames: 6,
                outcome_success: outcomes[idx] ?? null,
                outcome_confidence: 1,
                outcome_n_valid_votes: 3,
                progress_per_frame: [100],
                voc: 1,
                milestones: [],
                failure_mode: null,
                cached: false,
              },
            },
          ],
        }),
      )
    }
    const labelMatch = target.match(/episodes\/(\d+)\/labels$/)
    if (labelMatch) {
      const idx = Number(labelMatch[1])
      const body = JSON.parse(String(init?.body ?? '{}'))
      const response = jsonResponse({ episode_index: idx, labels: body.labels })
      response.headers.set('ETag', `"labels-${idx}"`)
      return Promise.resolve(response)
    }
    return Promise.resolve(jsonResponse({}))
  })
}

beforeEach(async () => {
  installFetchMock({ csrf: false })
  _resetCsrfToken()
  useLabelStore.getState().reset()
  useAnnotationStore.getState().clear()
  useEditStore.getState().clear()
  useDatasetStore.getState().reset()
  useEpisodeStore.getState().reset()
  await clearPersistedEditDraftsForTests()
  queryClient = createTestQueryClient()
  queryClient.setDefaultOptions({ queries: { gcTime: Infinity } })
  queryClient.setQueryData(['auth', 'principal-context'], {
    scopeId: 'principal-one',
    authMode: 'local',
  })
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('outcomeToLabel', () => {
  it('maps the judge outcome to a canonical label', () => {
    expect(outcomeToLabel({ outcomeSuccess: true })).toBe('SUCCESS')
    expect(outcomeToLabel({ outcomeSuccess: false })).toBe('FAILURE')
    expect(outcomeToLabel({ outcomeSuccess: null })).toBeNull()
  })
})

describe('applyOutcomeLabel', () => {
  it('replaces an existing outcome label while keeping custom labels', () => {
    expect(applyOutcomeLabel(['FAILURE', 'REVIEW'], 'SUCCESS')).toEqual(['REVIEW', 'SUCCESS'])
  })

  it('adds the outcome when none is present', () => {
    expect(applyOutcomeLabel(['REVIEW'], 'PARTIAL')).toEqual(['REVIEW', 'PARTIAL'])
  })
})

describe('useVlmJudgeBatch', () => {
  it('reconnects to durable jobs and excludes retained evidence after access denial', async () => {
    useDatasetStore.setState({
      currentDataset: {
        id: 'ds-1',
        name: 'Synthetic',
        totalEpisodes: 2,
        fps: 30,
        features: {},
        tasks: [],
      },
    })
    let denied = false
    mockFetch.mockImplementation(async (url: string) => {
      if (String(url).includes('/judge/jobs?'))
        return denied
          ? jsonResponse({ detail: 'Denied' }, 403)
          : jsonResponse({
              items: [{ id: 'durable', status: 'running', judged: 1, applied: 0 }],
              total: 1,
            })
      if (String(url).includes('/resets?')) return jsonResponse({ detail: 'Missing' }, 404)
      return jsonResponse({ items: [], total: 0 })
    })
    const { result } = renderHookWithProviders(() => useJudgeDataset('ds-1', 0, true), {
      queryClient,
    })
    await waitFor(() => expect(result.current.jobs.data?.items[0].id).toBe('durable'))
    denied = true
    await act(async () => {
      await result.current.jobs.refetch()
    })
    await waitFor(() => expect(result.current.jobs.data).toBeUndefined())
    await act(async () => {
      await expect(result.current.act({ kind: 'cancel', jobId: 'durable' })).rejects.toThrow(
        /context/,
      )
    })
    expect(mockFetch.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
  })
  it('submits sparse targets once with saved references and never writes browser labels', async () => {
    routeFetch({ 3: true, 1005: false })
    const route = mockFetch.getMockImplementation()!
    mockFetch.mockImplementation(async (url: string, init?: RequestInit) => {
      if (String(url).endsWith('/judge/jobs'))
        return jsonResponse({ id: 'durable', status: 'queued', total: 2 }, 202)
      return route(url, init)
    })
    const { result } = renderHookWithProviders(() => useVlmJudgeBatch('ds-1'), { queryClient })
    await act(async () => {
      await result.current.submit({
        indices: [3, 1005],
        mode: 'judge-and-label',
        approvalId: 'approval',
        options: { processMethod: 'chronological' },
      })
    })
    const calls = mockFetch.mock.calls.filter(([url]) => String(url).endsWith('/judge/jobs'))
    expect(calls).toHaveLength(1)
    const payload = JSON.parse(String(calls[0][1]?.body))
    expect(payload.episode_indices).toEqual([3, 1005])
    expect(payload.approval_id).toBe('approval')
    expect(payload.options.process_method).toBe('chronological')
    expect(Object.keys(payload.snapshot_ids)).toEqual(['3', '1005'])
    expect(
      mockFetch.mock.calls.some(
        ([url]) => String(url).endsWith('/labels') || String(url).endsWith('/judge'),
      ),
    ).toBe(false)
  })
  it('leaves label acknowledgment and conditional application to the durable backend', async () => {
    routeFetch({ 0: true, 1: false })
    const route = mockFetch.getMockImplementation()!
    mockFetch.mockImplementation(async (url: string, init?: RequestInit) => {
      const response = await route(url, init)
      if (String(url).endsWith('/labels')) response.headers.delete('ETag')
      return response
    })
    const { result } = renderHookWithProviders(() => useVlmJudgeBatch('ds-1'), { queryClient })
    await act(async () => {
      await result.current.submit({
        indices: [0, 1],
        mode: 'judge-and-label',
        approvalId: 'approval',
      })
    })
    expect(useLabelStore.getState().savedEpisodeLabels[0]).toBeUndefined()
    expect(mockFetch.mock.calls.filter(([url]) => String(url).endsWith('/labels'))).toHaveLength(0)
  })

  it('does not apply a late result after the active dataset changes', async () => {
    routeFetch({ 0: true })
    const route = mockFetch.getMockImplementation()!
    mockFetch.mockImplementation(async (url: string, init?: RequestInit) => {
      const response = await route(url, init)
      if (String(url).endsWith('/snapshot')) {
        useDatasetStore.setState({
          currentDataset: {
            id: 'another',
            name: 'Another',
            totalEpisodes: 1,
            fps: 30,
            features: {},
            tasks: [],
          },
        })
      }
      return response
    })
    const { result } = renderHookWithProviders(() => useVlmJudgeBatch('ds-1'), { queryClient })
    await act(async () => {
      await expect(
        result.current.submit({ indices: [0], mode: 'judge', approvalId: 'approval' }),
      ).rejects.toThrow(/context|scope/i)
    })
    expect(mockFetch.mock.calls.some(([url]) => String(url).endsWith('/labels'))).toBe(false)
    expect(mockFetch.mock.calls.some(([url]) => String(url).endsWith('/judge/jobs'))).toBe(false)
  })

  it('dispatches no targets until an unmounted selected draft is resolved', async () => {
    routeFetch({ 0: true, 1: false })
    await persistLabelDraft('ds-1', 'principal-one', {
      availableLabels: ['SUCCESS', 'FAILURE'],
      episodeLabels: { 1: ['FAILURE'] },
      savedEpisodeLabels: { 1: ['SUCCESS'] },
      baseEtag: 'one',
    })
    const { result } = renderHookWithProviders(() => useVlmJudgeBatch('ds-1'), { queryClient })
    await act(async () => {
      await expect(
        result.current.submit({ indices: [0, 1], mode: 'judge', approvalId: 'approval' }),
      ).rejects.toThrow(/Episode 1/)
    })
    expect(mockFetch).not.toHaveBeenCalled()
    await persistLabelDraft('ds-1', 'principal-one', null)
    await act(async () => {
      await result.current.submit({ indices: [0, 1], mode: 'judge', approvalId: 'approval' })
    })
    expect(
      mockFetch.mock.calls.filter(([url]) => String(url).endsWith('/judge/jobs')),
    ).toHaveLength(1)
  })

  it('submits the exact selected method and saved target references', async () => {
    routeFetch({ 0: true, 1: false })

    const { result } = renderHookWithProviders(() => useVlmJudgeBatch('ds-1'), { queryClient })
    await act(async () => {
      await result.current.submit({
        indices: [0, 1],
        mode: 'judge',
        approvalId: 'approval',
        options: { processMethod: 'chronological' },
      })
    })

    const judgeCalls = mockFetch.mock.calls.filter(([url]) => String(url).endsWith('/judge/jobs'))
    expect(judgeCalls).toHaveLength(1)
    expect(JSON.parse(judgeCalls[0][1].body).options.process_method).toBe('chronological')
    expect(JSON.parse(judgeCalls[0][1].body).snapshot_ids['0']).toBe('a'.repeat(64))
    // No label writes during a plain run.
    expect(mockFetch.mock.calls.some(([url]) => String(url).endsWith('/labels'))).toBe(false)
  })

  it('submits labeling intent without mutating local label drafts', async () => {
    routeFetch({ 0: true, 1: false })
    useLabelStore.getState().setEpisodeLabels(0, ['REVIEW'])

    const { result } = renderHookWithProviders(() => useVlmJudgeBatch('ds-1'), { queryClient })
    await act(async () => {
      await result.current.submit({
        indices: [0, 1],
        mode: 'judge-and-label',
        approvalId: 'approval',
        options: { processMethod: 'gvl' },
      })
    })

    const labelPuts = mockFetch.mock.calls.filter(([url]) => String(url).endsWith('/labels'))
    expect(labelPuts).toHaveLength(0)

    const store = useLabelStore.getState()
    expect(store.episodeLabels[0]).toEqual(['REVIEW'])
    expect(store.savedEpisodeLabels[1]).toBeUndefined()
  })

  it('rejects an empty target selection without network calls', async () => {
    routeFetch({})

    const { result } = renderHookWithProviders(() => useVlmJudgeBatch('ds-1'))
    await act(async () => {
      await expect(result.current.submit({ indices: [], mode: 'judge' })).rejects.toThrow(
        /Select actual episode IDs/,
      )
    })

    expect(mockFetch).not.toHaveBeenCalled()
  })
})
