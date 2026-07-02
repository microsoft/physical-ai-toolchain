import { act } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useRunVlmJudge, vlmJudgeKeys } from '@/hooks/use-vlm-judge'
import { runVlmJudge } from '@/lib/api-client'
import { createTestQueryClient, renderHookWithProviders } from '@/test-utils/render'
import type { VlmJudgeResult, VlmJudgeStatus } from '@/types'

vi.mock('@/lib/api-client', () => ({
  fetchVlmJudgeStatus: vi.fn(),
  runVlmJudge: vi.fn(),
}))

const mockRunVlmJudge = vi.mocked(runVlmJudge)

function judgeResult(partial: Partial<VlmJudgeResult> = {}): VlmJudgeResult {
  return {
    episodeId: 'demo/episode_000000',
    instruction: 'Pick up the orange',
    judgeModel: 'Qwen/Qwen3-VL-4B-Instruct',
    promptVersion: 'test-v1',
    nFrames: 12,
    outcomeSuccess: true,
    outcomeConfidence: 1,
    outcomeNValidVotes: 3,
    progressPerFrame: [0, 10, 20, 35, 50, 65, 75, 85, 90, 95, 100, 100],
    voc: 1,
    milestones: [],
    failureMode: null,
    cached: false,
    ...partial,
  }
}

function status(partial: Partial<VlmJudgeStatus> = {}): VlmJudgeStatus {
  return {
    enabled: true,
    cached: false,
    judgeModel: 'Qwen/Qwen3-VL-4B-Instruct',
    promptVersion: 'test-v1',
    cacheKey: 'cache-before-run',
    backend: 'openai-compat',
    processMethod: 'gvl',
    processMethods: ['gvl', 'chronological'],
    nFrames: 12,
    result: null,
    ...partial,
  }
}

beforeEach(() => {
  mockRunVlmJudge.mockReset()
})

describe('useRunVlmJudge', () => {
  it('preserves status metadata when storing a run result in the query cache', async () => {
    const queryClient = createTestQueryClient()
    queryClient.setQueryData(vlmJudgeKeys.episode('ds-1', 0), status())
    mockRunVlmJudge.mockResolvedValueOnce(judgeResult({ processMethod: 'gvl' }))

    const { result } = renderHookWithProviders(() => useRunVlmJudge(), { queryClient })
    await act(async () => {
      await result.current.mutateAsync({ datasetId: 'ds-1', episodeIndex: 0 })
    })

    expect(queryClient.getQueryData<VlmJudgeStatus>(vlmJudgeKeys.episode('ds-1', 0))).toEqual(
      expect.objectContaining({
        backend: 'openai-compat',
        processMethod: 'gvl',
        processMethods: ['gvl', 'chronological'],
        nFrames: 12,
        result: expect.objectContaining({ processMethod: 'gvl' }),
      }),
    )
  })
})
