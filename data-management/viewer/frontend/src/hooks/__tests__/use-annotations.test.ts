import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { _resetCsrfToken } from '@/lib/api-client'
import { useAnnotationStore, useDatasetStore, useEpisodeStore } from '@/stores'
import type {
  AnnotationSummary,
  AutoQualityAnalysis,
  DatasetInfo,
  EpisodeAnnotation,
  EpisodeAnnotationFile,
} from '@/types'

import { createQueryWrapper } from './test-utils'

const mockFetch = vi.fn()

function jsonResponse(data: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? 'OK' : 'Error',
    json: () => Promise.resolve(data),
  }
}

function mockMutationFetch(apiResponse: ReturnType<typeof jsonResponse>) {
  mockFetch
    .mockResolvedValueOnce(jsonResponse({ csrf_token: 'test-csrf-token' }))
    .mockResolvedValueOnce(apiResponse)
}

const testDataset: DatasetInfo = {
  id: 'ds-1',
  name: 'Dataset 1',
  totalEpisodes: 10,
  fps: 30,
  features: {},
  tasks: [],
}

const testAnnotation: EpisodeAnnotation = {
  annotatorId: 'user-1',
  timestamp: '2024-01-01T00:00:00.000Z',
  taskCompleteness: { rating: 'success', confidence: 5 },
  trajectoryQuality: {
    overallScore: 4,
    metrics: { smoothness: 4, efficiency: 4, safety: 5, precision: 3 },
    flags: [],
  },
  dataQuality: { overallQuality: 'good', issues: [] },
  anomalies: { anomalies: [] },
  notes: 'Test annotation',
}

const testAnnotationFile: EpisodeAnnotationFile = {
  schemaVersion: '1.0',
  episodeIndex: 0,
  datasetId: 'ds-1',
  annotations: [testAnnotation],
}

function setupStores(episodeIndex = 0) {
  useDatasetStore.getState().setDatasets([testDataset])
  useDatasetStore.getState().selectDataset(testDataset.id)
  useEpisodeStore.setState({ currentIndex: episodeIndex })
}

beforeEach(() => {
  mockFetch.mockReset()
  _resetCsrfToken()
  vi.stubGlobal('fetch', mockFetch)
  useDatasetStore.getState().reset()
  useEpisodeStore.getState().reset()
  useAnnotationStore.getState().clear()
})

afterEach(() => {
  vi.restoreAllMocks()
})

// ============================================================================
// annotationKeys
// ============================================================================

describe('annotationKeys', () => {
  it('generates correct query keys', async () => {
    const { annotationKeys } = await import('@/hooks/use-annotations')

    expect(annotationKeys.all).toEqual(['annotations'])
    expect(annotationKeys.lists()).toEqual(['annotations', 'list'])
    expect(annotationKeys.list('ds-1')).toEqual(['annotations', 'list', 'ds-1'])
    expect(annotationKeys.details()).toEqual(['annotations', 'detail'])
    expect(annotationKeys.detail('ds-1', 3)).toEqual(['annotations', 'detail', 'ds-1', 3])
    expect(annotationKeys.summary('ds-1')).toEqual(['annotations', 'summary', 'ds-1'])
    expect(annotationKeys.autoAnalysis('ds-1', 5)).toEqual(['annotations', 'auto', 'ds-1', 5])
  })
})

// ============================================================================
// useEpisodeAnnotations
// ============================================================================

describe('useEpisodeAnnotations', () => {
  it('fetches annotations for the current episode', async () => {
    const { useEpisodeAnnotations } = await import('@/hooks/use-annotations')
    setupStores(0)
    mockFetch.mockResolvedValueOnce(jsonResponse(testAnnotationFile))

    const wrapper = createQueryWrapper()
    const { result } = renderHook(() => useEpisodeAnnotations('user-1'), { wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual(testAnnotationFile)
  })

  it('loads existing user annotation into annotation store', async () => {
    const { useEpisodeAnnotations } = await import('@/hooks/use-annotations')
    setupStores(0)
    mockFetch.mockResolvedValueOnce(jsonResponse(testAnnotationFile))

    const wrapper = createQueryWrapper()
    renderHook(() => useEpisodeAnnotations('user-1'), { wrapper })

    await waitFor(() => {
      const state = useAnnotationStore.getState()
      expect(state.currentAnnotation?.annotatorId).toBe('user-1')
    })
  })

  it('initializes new annotation when user has no existing annotation', async () => {
    const { useEpisodeAnnotations } = await import('@/hooks/use-annotations')
    setupStores(0)
    const fileWithoutUser: EpisodeAnnotationFile = {
      ...testAnnotationFile,
      annotations: [],
    }
    mockFetch.mockResolvedValueOnce(jsonResponse(fileWithoutUser))

    const wrapper = createQueryWrapper()
    renderHook(() => useEpisodeAnnotations('new-user'), { wrapper })

    await waitFor(() => {
      const state = useAnnotationStore.getState()
      expect(state.currentAnnotation?.annotatorId).toBe('new-user')
    })
  })

  it('does not fetch when dataset is null', async () => {
    const { useEpisodeAnnotations } = await import('@/hooks/use-annotations')
    // Do not call setupStores — dataset stays null

    const wrapper = createQueryWrapper()
    const { result } = renderHook(() => useEpisodeAnnotations('user-1'), { wrapper })

    expect(result.current.fetchStatus).toBe('idle')
    expect(mockFetch).not.toHaveBeenCalled()
  })

  it('does not fetch when currentIndex is negative', async () => {
    const { useEpisodeAnnotations } = await import('@/hooks/use-annotations')
    useDatasetStore.getState().setDatasets([testDataset])
    useDatasetStore.getState().selectDataset(testDataset.id)
    // currentIndex defaults to -1 after reset

    const wrapper = createQueryWrapper()
    const { result } = renderHook(() => useEpisodeAnnotations('user-1'), { wrapper })

    expect(result.current.fetchStatus).toBe('idle')
  })
})

// ============================================================================
// useSaveAnnotation
// ============================================================================

describe('useSaveAnnotation', () => {
  it('calls saveAnnotation API with CSRF token', async () => {
    const { useSaveAnnotation } = await import('@/hooks/use-annotations')
    const updatedFile: EpisodeAnnotationFile = {
      ...testAnnotationFile,
      annotations: [{ ...testAnnotation, notes: 'Updated' }],
    }
    mockMutationFetch(jsonResponse(updatedFile))

    const wrapper = createQueryWrapper()
    const { result } = renderHook(() => useSaveAnnotation(), { wrapper })

    act(() => {
      result.current.mutate({
        datasetId: 'ds-1',
        episodeIndex: 0,
        annotation: testAnnotation,
      })
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    const putCall = mockFetch.mock.calls[1]
    expect(putCall[1].headers).toHaveProperty('X-CSRF-Token', 'test-csrf-token')
  })

  it('sets saving state during mutation', async () => {
    const { useSaveAnnotation } = await import('@/hooks/use-annotations')
    mockMutationFetch(jsonResponse(testAnnotationFile))

    const wrapper = createQueryWrapper()
    const { result } = renderHook(() => useSaveAnnotation(), { wrapper })

    const savingStates: boolean[] = []
    const unsubscribe = useAnnotationStore.subscribe((state) => {
      savingStates.push(state.isSaving)
    })

    act(() => {
      result.current.mutate({
        datasetId: 'ds-1',
        episodeIndex: 0,
        annotation: testAnnotation,
      })
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    unsubscribe()

    expect(savingStates).toContain(true)
    // After success, markSaved resets isSaving
    expect(useAnnotationStore.getState().isSaving).toBe(false)
  })

  it('sets error in store on mutation failure', async () => {
    const { useSaveAnnotation } = await import('@/hooks/use-annotations')
    mockFetch
      .mockResolvedValueOnce(jsonResponse({ csrf_token: 'test-csrf-token' }))
      .mockResolvedValueOnce(jsonResponse({ message: 'Server error', code: 'INTERNAL_ERROR' }, 500))

    const wrapper = createQueryWrapper()
    const { result } = renderHook(() => useSaveAnnotation(), { wrapper })

    act(() => {
      result.current.mutate({
        datasetId: 'ds-1',
        episodeIndex: 0,
        annotation: testAnnotation,
      })
    })

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(useAnnotationStore.getState().error).toBeTruthy()
  })
})

// ============================================================================
// useSaveCurrentAnnotation
// ============================================================================

describe('useSaveCurrentAnnotation', () => {
  it('saves annotation from store context', async () => {
    const { useSaveCurrentAnnotation } = await import('@/hooks/use-annotations')
    setupStores(3)
    useAnnotationStore.getState().loadAnnotation(testAnnotation)
    mockMutationFetch(jsonResponse(testAnnotationFile))

    const wrapper = createQueryWrapper()
    const { result } = renderHook(() => useSaveCurrentAnnotation(), { wrapper })

    act(() => {
      result.current.save()
    })

    await waitFor(() => expect(result.current.isPending).toBe(false))

    // Verify PUT was sent to the correct episode
    const putCall = mockFetch.mock.calls[1]
    expect(putCall[0]).toContain('/3/')
  })

  it('does nothing when dataset is null', async () => {
    const { useSaveCurrentAnnotation } = await import('@/hooks/use-annotations')
    // No setupStores — dataset is null
    useAnnotationStore.getState().loadAnnotation(testAnnotation)

    const wrapper = createQueryWrapper()
    const { result } = renderHook(() => useSaveCurrentAnnotation(), { wrapper })

    act(() => {
      result.current.save()
    })

    // Only CSRF preflight possible; no PUT should be issued
    expect(mockFetch).not.toHaveBeenCalled()
  })

  it('does nothing when currentAnnotation is null', async () => {
    const { useSaveCurrentAnnotation } = await import('@/hooks/use-annotations')
    setupStores(0)
    // Do not load any annotation

    const wrapper = createQueryWrapper()
    const { result } = renderHook(() => useSaveCurrentAnnotation(), { wrapper })

    act(() => {
      result.current.save()
    })

    expect(mockFetch).not.toHaveBeenCalled()
  })
})

// ============================================================================
// useDeleteAnnotation
// ============================================================================

describe('useDeleteAnnotation', () => {
  it('calls deleteAnnotations API with CSRF token', async () => {
    const { useDeleteAnnotation } = await import('@/hooks/use-annotations')
    mockMutationFetch(jsonResponse({ deleted: true, episodeIndex: 0 }))

    const wrapper = createQueryWrapper()
    const { result } = renderHook(() => useDeleteAnnotation(), { wrapper })

    act(() => {
      result.current.mutate({ datasetId: 'ds-1', episodeIndex: 0 })
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    const deleteCall = mockFetch.mock.calls[1]
    expect(deleteCall[1].headers).toHaveProperty('X-CSRF-Token', 'test-csrf-token')
  })

  it('clears annotation store when deleting all annotations', async () => {
    const { useDeleteAnnotation } = await import('@/hooks/use-annotations')
    useAnnotationStore.getState().loadAnnotation(testAnnotation)
    mockMutationFetch(jsonResponse({ deleted: true, episodeIndex: 0 }))

    const wrapper = createQueryWrapper()
    const { result } = renderHook(() => useDeleteAnnotation(), { wrapper })

    act(() => {
      result.current.mutate({ datasetId: 'ds-1', episodeIndex: 0 })
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(useAnnotationStore.getState().currentAnnotation).toBeNull()
  })

  it('does not clear store when deleting a specific annotator', async () => {
    const { useDeleteAnnotation } = await import('@/hooks/use-annotations')
    useAnnotationStore.getState().loadAnnotation(testAnnotation)
    mockMutationFetch(jsonResponse({ deleted: true, episodeIndex: 0 }))

    const wrapper = createQueryWrapper()
    const { result } = renderHook(() => useDeleteAnnotation(), { wrapper })

    act(() => {
      result.current.mutate({
        datasetId: 'ds-1',
        episodeIndex: 0,
        annotatorId: 'other-user',
      })
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(useAnnotationStore.getState().currentAnnotation).not.toBeNull()
  })
})

// ============================================================================
// useAutoAnalysis
// ============================================================================

describe('useAutoAnalysis', () => {
  it('calls triggerAutoAnalysis API with CSRF token', async () => {
    const { useAutoAnalysis } = await import('@/hooks/use-annotations')
    const analysis: AutoQualityAnalysis = {
      episodeIndex: 0,
      computed: {
        smoothnessScore: 0.9,
        efficiencyScore: 0.85,
        jitterMetric: 0.3,
        hesitationCount: 0,
        correctionCount: 1,
      },
      suggestedRating: 4,
      confidence: 0.85,
      flags: ['jittery'],
    }
    mockMutationFetch(jsonResponse(analysis))

    const wrapper = createQueryWrapper()
    const { result } = renderHook(() => useAutoAnalysis(), { wrapper })

    act(() => {
      result.current.mutate({ datasetId: 'ds-1', episodeIndex: 0 })
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    const postCall = mockFetch.mock.calls[1]
    expect(postCall[1].headers).toHaveProperty('X-CSRF-Token', 'test-csrf-token')
  })

  it('updates trajectory quality in annotation store on success', async () => {
    const { useAutoAnalysis } = await import('@/hooks/use-annotations')
    useAnnotationStore.getState().initializeAnnotation('user-1')

    const analysis: AutoQualityAnalysis = {
      episodeIndex: 0,
      computed: {
        smoothnessScore: 0.9,
        efficiencyScore: 0.85,
        jitterMetric: 0.3,
        hesitationCount: 0,
        correctionCount: 1,
      },
      suggestedRating: 5,
      confidence: 0.95,
      flags: ['jittery'],
    }
    mockMutationFetch(jsonResponse(analysis))

    const wrapper = createQueryWrapper()
    const { result } = renderHook(() => useAutoAnalysis(), { wrapper })

    act(() => {
      result.current.mutate({ datasetId: 'ds-1', episodeIndex: 0 })
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    const state = useAnnotationStore.getState()
    expect(state.currentAnnotation?.trajectoryQuality.overallScore).toBe(5)
    expect(state.currentAnnotation?.trajectoryQuality.flags).toEqual(['jittery'])
  })
})

// ============================================================================
// useCurrentEpisodeAutoAnalysis
// ============================================================================

describe('useCurrentEpisodeAutoAnalysis', () => {
  it('triggers auto-analysis for current episode', async () => {
    const { useCurrentEpisodeAutoAnalysis } = await import('@/hooks/use-annotations')
    setupStores(2)
    useAnnotationStore.getState().initializeAnnotation('user-1')

    const analysis: AutoQualityAnalysis = {
      episodeIndex: 2,
      computed: {
        smoothnessScore: 0.8,
        efficiencyScore: 0.75,
        jitterMetric: 0.5,
        hesitationCount: 1,
        correctionCount: 0,
      },
      suggestedRating: 3,
      confidence: 0.7,
      flags: [],
    }
    mockMutationFetch(jsonResponse(analysis))

    const wrapper = createQueryWrapper()
    const { result } = renderHook(() => useCurrentEpisodeAutoAnalysis(), { wrapper })

    act(() => {
      result.current.analyze()
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.analysis).toEqual(analysis)
  })

  it('does nothing when dataset is null', async () => {
    const { useCurrentEpisodeAutoAnalysis } = await import('@/hooks/use-annotations')
    // No setupStores

    const wrapper = createQueryWrapper()
    const { result } = renderHook(() => useCurrentEpisodeAutoAnalysis(), { wrapper })

    act(() => {
      result.current.analyze()
    })

    expect(mockFetch).not.toHaveBeenCalled()
  })
})

// ============================================================================
// useAnnotationSummary
// ============================================================================

describe('useAnnotationSummary', () => {
  it('fetches annotation summary for a dataset', async () => {
    const { useAnnotationSummary } = await import('@/hooks/use-annotations')
    const summary: AnnotationSummary = {
      datasetId: 'ds-1',
      totalEpisodes: 10,
      annotatedEpisodes: 5,
      taskCompletenessDistribution: {
        success: 3,
        partial: 1,
        failure: 0,
        unknown: 1,
      },
      qualityScoreDistribution: { 1: 0, 2: 1, 3: 2, 4: 1, 5: 1 },
      anomalyTypeCounts: {},
    }
    mockFetch.mockResolvedValueOnce(jsonResponse(summary))

    const wrapper = createQueryWrapper()
    const { result } = renderHook(() => useAnnotationSummary('ds-1'), { wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual(summary)
  })

  it('does not fetch when datasetId is undefined', async () => {
    const { useAnnotationSummary } = await import('@/hooks/use-annotations')

    const wrapper = createQueryWrapper()
    const { result } = renderHook(() => useAnnotationSummary(undefined), { wrapper })

    expect(result.current.fetchStatus).toBe('idle')
    expect(mockFetch).not.toHaveBeenCalled()
  })
})
