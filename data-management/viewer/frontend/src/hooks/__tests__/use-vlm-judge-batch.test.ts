import { act } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { applyOutcomeLabel, outcomeToLabel, useVlmJudgeBatch } from '@/hooks/use-vlm-judge-batch'
import { _resetCsrfToken } from '@/lib/api-client'
import { useLabelStore } from '@/stores'
import { TEST_CSRF_TOKEN } from '@/test-utils/constants'
import { installFetchMock, jsonResponse, mockFetch } from '@/test-utils/fetch-mocks'
import { renderHookWithProviders } from '@/test-utils/render'

function routeFetch(outcomes: Record<number, boolean | null>) {
  mockFetch.mockImplementation((url: string, init?: RequestInit) => {
    const target = String(url)
    if (target.includes('/csrf-token')) {
      return Promise.resolve(jsonResponse({ csrf_token: TEST_CSRF_TOKEN }))
    }
    const judgeMatch = target.match(/episodes\/(\d+)\/judge$/)
    if (judgeMatch) {
      const idx = Number(judgeMatch[1])
      return Promise.resolve(
        jsonResponse({
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
        }),
      )
    }
    const labelMatch = target.match(/episodes\/(\d+)\/labels$/)
    if (labelMatch) {
      const idx = Number(labelMatch[1])
      const body = JSON.parse(String(init?.body ?? '{}'))
      return Promise.resolve(jsonResponse({ episode_index: idx, labels: body.labels }))
    }
    return Promise.resolve(jsonResponse({}))
  })
}

beforeEach(() => {
  installFetchMock({ csrf: false })
  _resetCsrfToken()
  useLabelStore.getState().reset()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('outcomeToLabel', () => {
  it('maps the judge outcome to a canonical label', () => {
    expect(outcomeToLabel({ outcomeSuccess: true })).toBe('SUCCESS')
    expect(outcomeToLabel({ outcomeSuccess: false })).toBe('FAILURE')
    expect(outcomeToLabel({ outcomeSuccess: null })).toBe('PARTIAL')
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
  it('runs the judge on every episode with the selected method', async () => {
    routeFetch({ 0: true, 1: false })

    const { result } = renderHookWithProviders(() => useVlmJudgeBatch('ds-1', 2))
    await act(async () => {
      await result.current.runAll({ processMethod: 'chronological' })
    })

    const judgeCalls = mockFetch.mock.calls.filter(([url]) => String(url).endsWith('/judge'))
    expect(judgeCalls.map(([url]) => url)).toEqual([
      '/api/datasets/ds-1/episodes/0/judge',
      '/api/datasets/ds-1/episodes/1/judge',
    ])
    expect(JSON.parse(judgeCalls[0][1].body).process_method).toBe('chronological')
    // No label writes during a plain run.
    expect(mockFetch.mock.calls.some(([url]) => String(url).endsWith('/labels'))).toBe(false)
    expect(result.current.isRunning).toBe(false)
    expect(result.current.progress).toBeNull()
  })

  it('applies mapped outcome labels to every episode and preserves custom labels', async () => {
    routeFetch({ 0: true, 1: false })
    useLabelStore.getState().setEpisodeLabels(0, ['REVIEW'])

    const { result } = renderHookWithProviders(() => useVlmJudgeBatch('ds-1', 2))
    await act(async () => {
      await result.current.applyLabelsAll({ processMethod: 'gvl' })
    })

    const labelPuts = mockFetch.mock.calls.filter(([url]) => String(url).endsWith('/labels'))
    expect(labelPuts).toHaveLength(2)
    expect(JSON.parse(labelPuts[0][1].body)).toEqual({ labels: ['REVIEW', 'SUCCESS'] })
    expect(JSON.parse(labelPuts[1][1].body)).toEqual({ labels: ['FAILURE'] })

    const store = useLabelStore.getState()
    expect(store.episodeLabels[0]).toEqual(['REVIEW', 'SUCCESS'])
    expect(store.episodeLabels[1]).toEqual(['FAILURE'])
    expect(store.savedEpisodeLabels[1]).toEqual(['FAILURE'])
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
