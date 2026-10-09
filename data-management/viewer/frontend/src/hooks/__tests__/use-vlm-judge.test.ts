import { act, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useEpisodeReadiness } from '@/hooks/use-episode-readiness'
import { useJudgeEvidence, useRunVlmJudge, vlmJudgeKeys } from '@/hooks/use-vlm-judge'
import { fetchJudgeEvidence, fetchVlmJudgeSnapshot, runVlmJudge } from '@/lib/api-client'
import * as draftStorage from '@/lib/edit-draft-storage'
import {
  clearPersistedEditDraftsForTests,
  persistAnnotationDraft,
  persistEditDraft,
  persistLabelDraft,
} from '@/lib/edit-draft-storage'
import { checkEpisodeReadiness } from '@/lib/episode-readiness'
import {
  useAnnotationStore,
  useDatasetStore,
  useEditStore,
  useEpisodeStore,
  useLabelStore,
} from '@/stores'
import { createTestQueryClient, renderHookWithProviders } from '@/test-utils/render'
import type { VlmJudgeResult, VlmJudgeStatus } from '@/types'

vi.mock('@/lib/api-client', () => ({
  fetchVlmJudgeStatus: vi.fn(),
  fetchVlmJudgeSnapshot: vi.fn(),
  runVlmJudge: vi.fn(),
  fetchJudgeEvidence: vi.fn(),
  mutateJudgeDataset: vi.fn(),
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

beforeEach(async () => {
  useAnnotationStore.getState().clear()
  useEditStore.getState().clear()
  useLabelStore.getState().reset()
  useDatasetStore.getState().reset()
  useEpisodeStore.getState().reset()
  await clearPersistedEditDraftsForTests()
  mockRunVlmJudge.mockReset()
  vi.mocked(fetchVlmJudgeSnapshot)
    .mockReset()
    .mockResolvedValue({
      snapshotId: 'a'.repeat(64),
      datasetId: 'ds-1',
      episodeIndex: 0,
      principalScopeId: 'principal-one',
      sourceId: 'source',
      sourceRevision: 'one',
      annotationAuthorId: null,
      annotationRevision: null,
      editRevision: null,
      instruction: 'Saved',
      instructionOrigin: 'dataset',
    })
})

afterEach(() => vi.restoreAllMocks())

describe('selected episode readiness', () => {
  it('retains evidence on refresh failure, excludes denied or replaced sources, and never runs inference', async () => {
    const queryClient = createTestQueryClient()
    queryClient.setDefaultOptions({ queries: { gcTime: Infinity, retry: false } })
    queryClient.setQueryData(['auth', 'principal-context'], {
      scopeId: 'principal-one',
      authMode: 'local',
    })
    useDatasetStore.setState({
      currentDataset: {
        id: 'ds-1',
        name: 'Dataset',
        totalEpisodes: 2,
        fps: 30,
        features: {},
        tasks: [],
      },
    })
    const episode = {
      sourceId: 'source',
      sourceRevision: 'one',
      meta: { index: 0, length: 3, taskIndex: 0, hasAnnotations: false },
      cameras: [],
      videoUrls: {},
      trajectoryData: [],
    }
    useEpisodeStore.setState({ currentEpisode: episode })
    const input = await vi.mocked(fetchVlmJudgeSnapshot)('ds-1', 0)
    vi.mocked(fetchJudgeEvidence)
      .mockReset()
      .mockResolvedValue({
        items: [
          {
            runId: 'run',
            resultId: 'result',
            resultKind: 'judge',
            configRevision: 'config',
            input,
            applicability: 'current',
            applied: false,
            result: judgeResult(),
          },
        ],
        total: 1,
      })
    let enabled = false
    const { result, rerender } = renderHookWithProviders(
      () => useJudgeEvidence('ds-1', 0, enabled),
      {
        queryClient,
      },
    )
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ['judge-evidence', 'ds-1'] })
    })
    expect(fetchJudgeEvidence).not.toHaveBeenCalled()
    expect(result.current.data).toBeUndefined()
    enabled = true
    rerender()
    await waitFor(() => expect(result.current.data?.items).toHaveLength(1))
    vi.mocked(fetchJudgeEvidence).mockRejectedValueOnce(new Error('temporary'))
    await act(async () => {
      await result.current.refetch()
    })
    expect(result.current.data?.items).toHaveLength(1)
    vi.mocked(fetchJudgeEvidence).mockRejectedValueOnce(
      Object.assign(new Error('Denied'), { status: 403 }),
    )
    await act(async () => {
      await result.current.refetch()
    })
    await waitFor(() => expect(result.current.data).toBeUndefined())
    act(() => useEpisodeStore.setState({ currentEpisode: { ...episode, sourceRevision: 'two' } }))
    await waitFor(() => expect(result.current.data?.items).toHaveLength(0))
    expect(mockRunVlmJudge).not.toHaveBeenCalled()
  })
  it.each(['identity', 'dataset'])(
    'blocks %s access failure during the persisted draft scan',
    async (scope) => {
      const queryClient = createTestQueryClient()
      queryClient.setDefaultOptions({ queries: { gcTime: Infinity } })
      queryClient.setQueryData(['auth', 'principal-context'], {
        scopeId: 'principal-one',
        authMode: 'local',
      })
      queryClient.setQueryData(['datasets', 'ds-1'], {})
      const queryKey = scope === 'identity' ? ['auth', 'principal-context'] : ['datasets', 'ds-1']
      vi.spyOn(draftStorage, 'loadPersistedLabelDraft').mockImplementationOnce(async () => {
        queryClient
          .getQueryCache()
          .find({ queryKey })!
          .setState({ status: 'error', error: new Error('Access lost') })
        return undefined
      })
      expect((await checkEpisodeReadiness(queryClient, 'ds-1', [0])).ready).toBe(false)
    },
  )

  it('reuses persisted readiness reads until a draft changes', async () => {
    const queryClient = createTestQueryClient()
    queryClient.setDefaultOptions({ queries: { gcTime: Infinity } })
    queryClient.setQueryData(['auth', 'principal-context'], {
      scopeId: 'principal-one',
      authMode: 'local',
    })
    const lookup = vi.spyOn(draftStorage, 'loadPersistedAnnotationDraft')
    const targets = [0, 1, 2, 3]
    expect((await checkEpisodeReadiness(queryClient, 'ds-1', targets)).ready).toBe(true)
    expect((await checkEpisodeReadiness(queryClient, 'ds-1', targets)).ready).toBe(true)
    expect(lookup).toHaveBeenCalledTimes(targets.length)
    await persistLabelDraft('ds-1', 'principal-one', {
      availableLabels: ['SUCCESS'],
      episodeLabels: { 3: ['SUCCESS'] },
      savedEpisodeLabels: {},
      baseEtag: null,
    })
    expect((await checkEpisodeReadiness(queryClient, 'ds-1', targets)).ready).toBe(false)
    expect(lookup).toHaveBeenCalledTimes(targets.length * 2)
  })

  it('blocks the active workspace until all resource contexts are initialized', async () => {
    const queryClient = createTestQueryClient()
    queryClient.setDefaultOptions({ queries: { gcTime: Infinity } })
    queryClient.setQueryData(['auth', 'principal-context'], {
      scopeId: 'principal-one',
      authMode: 'local',
    })
    useDatasetStore.setState({
      currentDataset: {
        id: 'ds-1',
        name: 'Dataset',
        totalEpisodes: 2,
        fps: 30,
        features: {},
        tasks: [],
      },
    })
    useEpisodeStore.setState({ currentIndex: 0 })
    expect(await checkEpisodeReadiness(queryClient, 'ds-1', [1])).toMatchObject({ ready: false })
  })

  it('blocks selected episodes while a save is pending after its control unmounts', async () => {
    const queryClient = createTestQueryClient()
    queryClient.setDefaultOptions({ queries: { gcTime: Infinity } })
    queryClient.setQueryData(['auth', 'principal-context'], {
      scopeId: 'principal-one',
      authMode: 'local',
    })
    let resolveSave!: () => void
    const mutation = queryClient.getMutationCache().build(queryClient, {
      mutationKey: ['episode-save', 'edits'],
      mutationFn: () =>
        new Promise<void>((resolve) => {
          resolveSave = resolve
        }),
    })
    const pending = mutation.execute(undefined)
    await waitFor(() => expect(resolveSave).toBeTypeOf('function'))
    expect(await checkEpisodeReadiness(queryClient, 'ds-1', [0])).toMatchObject({ ready: false })
    resolveSave()
    await pending
    expect(await checkEpisodeReadiness(queryClient, 'ds-1', [0])).toMatchObject({ ready: true })
  })

  it('blocks judging after source replacement even when edit stores are clean', async () => {
    const queryClient = createTestQueryClient()
    queryClient.setDefaultOptions({ queries: { gcTime: Infinity } })
    queryClient.setQueryData(['auth', 'principal-context'], {
      scopeId: 'principal-one',
      authMode: 'local',
    })
    useEditStore.setState({
      datasetId: 'ds-1',
      episodeIndex: 0,
      principalScopeId: 'principal-one',
      draftHydrated: true,
      serverBaseline: {
        sourceId: 'source',
        sourceRevision: 'old',
        principalScopeId: 'principal-one',
        etag: null,
        operations: { datasetId: 'ds-1', episodeIndex: 0 },
      },
    })
    useEpisodeStore.setState({
      currentEpisode: {
        sourceId: 'source',
        sourceRevision: 'new',
        meta: { index: 0, length: 3, taskIndex: 0, hasAnnotations: false },
        cameras: [],
        videoUrls: {},
        trajectoryData: [],
      },
    })
    expect(await checkEpisodeReadiness(queryClient, 'ds-1', [0])).toMatchObject({ ready: false })
  })

  it('refreshes visible readiness when an unmounted draft is resolved', async () => {
    const queryClient = createTestQueryClient()
    queryClient.setDefaultOptions({ queries: { gcTime: Infinity } })
    queryClient.setQueryData(['auth', 'principal-context'], {
      scopeId: 'principal-one',
      authMode: 'local',
    })
    await persistLabelDraft('ds-1', 'principal-one', {
      availableLabels: ['SUCCESS'],
      episodeLabels: { 0: [] },
      savedEpisodeLabels: { 0: ['SUCCESS'] },
      baseEtag: 'one',
    })
    const { result } = renderHookWithProviders(() => useEpisodeReadiness('ds-1', [0]), {
      queryClient,
    })
    await waitFor(() => expect(result.current.reason).toMatch(/Episode 0/))
    await act(async () => {
      await persistLabelDraft('ds-1', 'principal-one', null)
    })
    await waitFor(() => expect(result.current.ready).toBe(true))
  })

  it('fails closed on a draft lookup error', async () => {
    const queryClient = createTestQueryClient()
    queryClient.setDefaultOptions({ queries: { gcTime: Infinity } })
    queryClient.setQueryData(['auth', 'principal-context'], {
      scopeId: 'principal-one',
      authMode: 'local',
    })
    vi.spyOn(draftStorage, 'loadPersistedLabelDraft').mockRejectedValueOnce(
      new Error('storage unavailable'),
    )
    expect(await checkEpisodeReadiness(queryClient, 'ds-1', [0])).toMatchObject({
      ready: false,
      blockers: [expect.objectContaining({ resource: 'lookup' })],
    })
  })

  it.each(['pending', 'conflict', 'recovery'] as const)(
    'blocks selected label %s state',
    async (reason) => {
      const queryClient = createTestQueryClient()
      queryClient.setDefaultOptions({ queries: { gcTime: Infinity } })
      queryClient.setQueryData(['auth', 'principal-context'], {
        scopeId: 'principal-one',
        authMode: 'local',
      })
      useLabelStore.getState().prepareDatasetLabels('ds-1', 'principal-one')
      useLabelStore.setState({
        isLoaded: true,
        draftHydrated: true,
        isSaving: reason === 'pending',
        draftError: reason === 'recovery' ? 'failed' : null,
        conflict:
          reason === 'conflict'
            ? { episodeIndex: 0, currentEtag: 'two', submittedLabels: [] }
            : null,
      })
      expect(await checkEpisodeReadiness(queryClient, 'ds-1', [0])).toMatchObject({ ready: false })
    },
  )

  it.each(['annotation', 'episode-edit'] as const)(
    'detects an unmounted %s draft',
    async (resource) => {
      const queryClient = createTestQueryClient()
      queryClient.setDefaultOptions({ queries: { gcTime: Infinity } })
      queryClient.setQueryData(['auth', 'principal-context'], {
        scopeId: 'principal-one',
        authMode: 'local',
      })
      if (resource === 'annotation') {
        useAnnotationStore.getState().initializeAnnotation('principal-one')
        const baseline = structuredClone(useAnnotationStore.getState().currentAnnotation!)
        await persistAnnotationDraft('ds-1', 0, 'principal-one', {
          baseline,
          draft: { ...baseline, notes: 'Unsaved' },
          baseEtag: 'one',
        })
        useAnnotationStore.getState().clear()
      } else {
        await persistEditDraft('ds-1', 0, 'principal-one', {
          datasetId: 'ds-1',
          episodeIndex: 0,
          removedFrames: [2],
        })
      }
      expect(await checkEpisodeReadiness(queryClient, 'ds-1', [0])).toMatchObject({
        ready: false,
        blockers: [expect.objectContaining({ episodeIndex: 0, resource })],
      })
    },
  )

  it('blocks an unmounted selected label draft while the active episode is clean', async () => {
    const queryClient = createTestQueryClient()
    queryClient.setDefaultOptions({ queries: { retry: false, gcTime: Infinity } })
    queryClient.setQueryData(['auth', 'principal-context'], {
      scopeId: 'principal-one',
      authMode: 'local',
    })
    await persistLabelDraft('ds-1', 'principal-one', {
      availableLabels: ['SUCCESS', 'FAILURE'],
      episodeLabels: { 0: ['FAILURE'], 1: ['SUCCESS'] },
      savedEpisodeLabels: { 0: ['SUCCESS'], 1: ['SUCCESS'] },
      baseEtag: 'one',
    })
    expect(await checkEpisodeReadiness(queryClient, 'ds-1', [0])).toMatchObject({
      ready: false,
      blockers: [expect.objectContaining({ episodeIndex: 0, resource: 'labels' })],
    })
    expect(await checkEpisodeReadiness(queryClient, 'ds-1', [1])).toMatchObject({ ready: true })
    await persistLabelDraft('ds-1', 'principal-one', null)
    expect(await checkEpisodeReadiness(queryClient, 'ds-1', [0])).toMatchObject({ ready: true })
  })

  it('fails closed when identity is unavailable', async () => {
    expect(await checkEpisodeReadiness(createTestQueryClient(), 'ds-1', [0])).toMatchObject({
      ready: false,
    })
  })
})

describe('useRunVlmJudge', () => {
  it('rejects a result returned after the principal changes', async () => {
    const queryClient = createTestQueryClient()
    queryClient.setDefaultOptions({ queries: { gcTime: Infinity } })
    queryClient.setQueryData(['auth', 'principal-context'], {
      scopeId: 'principal-one',
      authMode: 'local',
    })
    mockRunVlmJudge.mockImplementationOnce(async () => {
      queryClient.setQueryData(['auth', 'principal-context'], {
        scopeId: 'principal-two',
        authMode: 'local',
      })
      return judgeResult()
    })
    const { result } = renderHookWithProviders(() => useRunVlmJudge(), { queryClient })
    await act(async () => {
      await expect(
        result.current.mutateAsync({ datasetId: 'ds-1', episodeIndex: 0 }),
      ).rejects.toThrow(/context changed/i)
    })
    expect(
      queryClient.getQueryData(vlmJudgeKeys.episode('ds-1', 0, 'principal-two')),
    ).toBeUndefined()
  })

  it('does not dispatch when a draft appears while the saved snapshot is loading', async () => {
    const queryClient = createTestQueryClient()
    queryClient.setDefaultOptions({ queries: { gcTime: Infinity } })
    queryClient.setQueryData(['auth', 'principal-context'], {
      scopeId: 'principal-one',
      authMode: 'local',
    })
    vi.mocked(fetchVlmJudgeSnapshot).mockImplementationOnce(async () => {
      await persistLabelDraft('ds-1', 'principal-one', {
        availableLabels: ['SUCCESS'],
        episodeLabels: { 0: ['SUCCESS'] },
        savedEpisodeLabels: {},
        baseEtag: null,
      })
      return {
        snapshotId: 'a'.repeat(64),
        datasetId: 'ds-1',
        episodeIndex: 0,
        principalScopeId: 'principal-one',
        sourceId: 'source',
        sourceRevision: 'one',
        annotationAuthorId: null,
        annotationRevision: null,
        editRevision: null,
        instruction: 'Saved',
        instructionOrigin: 'dataset',
      }
    })
    const { result } = renderHookWithProviders(() => useRunVlmJudge(), { queryClient })
    await act(async () => {
      await expect(
        result.current.mutateAsync({
          datasetId: 'ds-1',
          episodeIndex: 0,
          options: { force: true },
        }),
      ).rejects.toThrow(/Episode 0/)
    })
    expect(mockRunVlmJudge).not.toHaveBeenCalled()
  })

  it('does not dispatch a forced run against a recovered dirty label draft', async () => {
    const queryClient = createTestQueryClient()
    queryClient.setDefaultOptions({ queries: { gcTime: Infinity } })
    queryClient.setQueryData(['auth', 'principal-context'], {
      scopeId: 'principal-one',
      authMode: 'local',
    })
    await persistLabelDraft('ds-1', 'principal-one', {
      availableLabels: ['SUCCESS', 'FAILURE'],
      episodeLabels: { 0: ['FAILURE'] },
      savedEpisodeLabels: { 0: ['SUCCESS'] },
      baseEtag: 'one',
    })
    const { result } = renderHookWithProviders(() => useRunVlmJudge(), { queryClient })
    await act(async () => {
      await expect(
        result.current.mutateAsync({
          datasetId: 'ds-1',
          episodeIndex: 0,
          options: { force: true },
        }),
      ).rejects.toThrow(/Episode 0/)
    })
    expect(mockRunVlmJudge).not.toHaveBeenCalled()
  })

  it('preserves status metadata when storing a run result in the query cache', async () => {
    const queryClient = createTestQueryClient()
    queryClient.setDefaultOptions({ queries: { retry: false, gcTime: Infinity } })
    queryClient.setQueryData(['auth', 'principal-context'], {
      scopeId: 'principal-one',
      authMode: 'local',
    })
    queryClient.setQueryData(vlmJudgeKeys.episode('ds-1', 0, 'principal-one'), status())
    mockRunVlmJudge.mockResolvedValueOnce(judgeResult({ processMethod: 'gvl' }))

    const { result } = renderHookWithProviders(() => useRunVlmJudge(), { queryClient })
    await act(async () => {
      await result.current.mutateAsync({ datasetId: 'ds-1', episodeIndex: 0 })
    })

    expect(
      queryClient.getQueryData<VlmJudgeStatus>(vlmJudgeKeys.episode('ds-1', 0, 'principal-one')),
    ).toEqual(
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
