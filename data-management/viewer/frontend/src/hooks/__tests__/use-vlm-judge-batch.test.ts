import { act } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { applyOutcomeLabel, outcomeToLabel, useVlmJudgeBatch } from '@/hooks/use-vlm-judge-batch'
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
  it('stops writeback without acknowledging labels when the server omits its revision', async () => {
    routeFetch({ 0: true, 1: false })
    const route = mockFetch.getMockImplementation()!
    mockFetch.mockImplementation(async (url: string, init?: RequestInit) => {
      const response = await route(url, init)
      if (String(url).endsWith('/labels')) response.headers.delete('ETag')
      return response
    })
    const { result } = renderHookWithProviders(() => useVlmJudgeBatch('ds-1', 2), { queryClient })
    await act(async () => {
      await result.current.applyLabelsAll()
    })
    expect(result.current.error).toMatch(/acknowledg|revision/i)
    expect(useLabelStore.getState().savedEpisodeLabels[0]).toBeUndefined()
    expect(mockFetch.mock.calls.filter(([url]) => String(url).endsWith('/labels'))).toHaveLength(1)
  })

  it('does not apply a late result after the active dataset changes', async () => {
    routeFetch({ 0: true })
    const route = mockFetch.getMockImplementation()!
    mockFetch.mockImplementation(async (url: string, init?: RequestInit) => {
      const response = await route(url, init)
      if (String(url).endsWith('/judge')) {
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
    const { result } = renderHookWithProviders(() => useVlmJudgeBatch('ds-1', 1), { queryClient })
    await act(async () => {
      await result.current.applyLabelsAll()
    })
    expect(mockFetch.mock.calls.some(([url]) => String(url).endsWith('/labels'))).toBe(false)
    expect(result.current.error).toMatch(/context|scope/i)
  })

  it('dispatches no targets until an unmounted selected draft is resolved', async () => {
    routeFetch({ 0: true, 1: false })
    await persistLabelDraft('ds-1', 'principal-one', {
      availableLabels: ['SUCCESS', 'FAILURE'],
      episodeLabels: { 1: ['FAILURE'] },
      savedEpisodeLabels: { 1: ['SUCCESS'] },
      baseEtag: 'one',
    })
    const { result } = renderHookWithProviders(() => useVlmJudgeBatch('ds-1', 2), { queryClient })
    await act(async () => {
      await result.current.runAll()
    })
    expect(mockFetch).not.toHaveBeenCalled()
    expect(result.current.error).toMatch(/Episode 1/)
    await persistLabelDraft('ds-1', 'principal-one', null)
    await act(async () => {
      await result.current.runAll()
    })
    expect(mockFetch.mock.calls.filter(([url]) => String(url).endsWith('/judge'))).toHaveLength(2)
  })

  it('runs the judge on every episode with the selected method', async () => {
    routeFetch({ 0: true, 1: false })

    const { result } = renderHookWithProviders(() => useVlmJudgeBatch('ds-1', 2), { queryClient })
    await act(async () => {
      await result.current.runAll({ processMethod: 'chronological' })
    })

    const judgeCalls = mockFetch.mock.calls.filter(([url]) => String(url).endsWith('/judge'))
    expect(judgeCalls.map(([url]) => url)).toEqual([
      '/api/datasets/ds-1/episodes/0/judge',
      '/api/datasets/ds-1/episodes/1/judge',
    ])
    expect(JSON.parse(judgeCalls[0][1].body).process_method).toBe('chronological')
    expect(JSON.parse(judgeCalls[0][1].body).snapshot_id).toBe('a'.repeat(64))
    // No label writes during a plain run.
    expect(mockFetch.mock.calls.some(([url]) => String(url).endsWith('/labels'))).toBe(false)
    expect(result.current.isRunning).toBe(false)
    expect(result.current.progress).toBeNull()
  })

  it('applies mapped outcome labels to every episode and preserves custom labels', async () => {
    routeFetch({ 0: true, 1: false })
    useLabelStore.getState().setEpisodeLabels(0, ['REVIEW'])

    const { result } = renderHookWithProviders(() => useVlmJudgeBatch('ds-1', 2), { queryClient })
    await act(async () => {
      await result.current.applyLabelsAll({ processMethod: 'gvl' })
    })

    const labelPuts = mockFetch.mock.calls.filter(([url]) => String(url).endsWith('/labels'))
    expect(labelPuts).toHaveLength(2)
    expect(JSON.parse(labelPuts[0][1].body)).toEqual({
      labels: ['REVIEW', 'SUCCESS'],
      intent: 'legacy-unknown',
    })
    expect(JSON.parse(labelPuts[1][1].body)).toEqual({
      labels: ['FAILURE'],
      intent: 'legacy-unknown',
    })

    const store = useLabelStore.getState()
    expect(store.episodeLabels[0]).toEqual(['REVIEW', 'SUCCESS'])
    expect(store.episodeLabels[1]).toEqual(['FAILURE'])
    expect(store.savedEpisodeLabels[1]).toEqual(['FAILURE'])
    expect(store.baseEtag).toBe('"labels-1"')
  })

  it('does nothing when the dataset has no episodes', async () => {
    routeFetch({})

    const { result } = renderHookWithProviders(() => useVlmJudgeBatch('ds-1', 0))
    await act(async () => {
      await result.current.runAll()
    })

    expect(mockFetch).not.toHaveBeenCalled()
    expect(result.current.progress).toBeNull()
  })
})
