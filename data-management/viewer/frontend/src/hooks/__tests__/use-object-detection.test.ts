/**
 * Tests for useObjectDetection hook.
 *
 * NOTE — Known bug (follow-up WI-01):
 *   The hook calls `setNeedsRerun(true)` inside a `useMemo` body when
 *   `hasEdits` becomes true. `useMemo` is for memoized values, not side
 *   effects; React may skip, batch, or re-run the body unpredictably.
 *   The intent is clearly an effect — `useEffect` is the correct primitive.
 *   In the current test environment the side effect happens to flip
 *   `needsRerun` to true on re-render, so the behavioral assertion passes
 *   today. The reliability concern remains — the fix (move to `useEffect`)
 *   is tracked in WI-01. Do NOT fix the hook here.
 */

import { act, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useObjectDetection } from '@/hooks/use-object-detection'
import { renderHookWithProviders } from '@/test-utils/render-hook'

const { mockRunDetection, mockGetDetections, mockClearDetections, storeState } = vi.hoisted(() => ({
  mockRunDetection: vi.fn(),
  mockGetDetections: vi.fn(),
  mockClearDetections: vi.fn(),
  storeState: {
    currentDataset: { id: 'ds-1' } as { id: string } | null,
    currentEpisode: { meta: { index: 0 } } as { meta: { index: number } } | null,
    isDirty: false,
  },
}))

vi.mock('@/api/detection', () => ({
  runDetection: mockRunDetection,
  getDetections: mockGetDetections,
  clearDetections: mockClearDetections,
}))

vi.mock('@/stores', () => ({
  useDatasetStore: <T>(selector: (s: { currentDataset: typeof storeState.currentDataset }) => T) =>
    selector({ currentDataset: storeState.currentDataset }),
  useEpisodeStore: <T>(selector: (s: { currentEpisode: typeof storeState.currentEpisode }) => T) =>
    selector({ currentEpisode: storeState.currentEpisode }),
  useEditDirtyState: () => ({ isDirty: storeState.isDirty }),
}))

const sampleSummary = {
  total_frames: 10,
  processed_frames: 10,
  total_detections: 3,
  class_summary: { person: 2, ball: 1 },
  detections_by_frame: [
    {
      frame_index: 0,
      detections: [
        { class_name: 'person', confidence: 0.9, bbox: [0, 0, 1, 1] },
        { class_name: 'person', confidence: 0.1, bbox: [0, 0, 1, 1] },
        { class_name: 'ball', confidence: 0.5, bbox: [0, 0, 1, 1] },
      ],
    },
  ],
}

describe('useObjectDetection', () => {
  beforeEach(() => {
    storeState.currentDataset = { id: 'ds-1' }
    storeState.currentEpisode = { meta: { index: 0 } }
    storeState.isDirty = false

    mockGetDetections.mockReset().mockResolvedValue(sampleSummary)
    mockRunDetection.mockReset().mockResolvedValue(sampleSummary)
    mockClearDetections.mockReset().mockResolvedValue(undefined)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('initializes with default filters and no rerun pending', () => {
    const { result } = renderHookWithProviders(() => useObjectDetection())

    expect(result.current.filters).toEqual({ classes: [], minConfidence: 0.25 })
    expect(result.current.needsRerun).toBe(false)
  })

  it('enables query and loads detections when dataset and episode are present', async () => {
    const { result } = renderHookWithProviders(() => useObjectDetection())

    await waitFor(() => expect(result.current.data).toBeDefined())
    expect(mockGetDetections).toHaveBeenCalledWith('ds-1', 0)
    expect(result.current.availableClasses.sort()).toEqual(['ball', 'person'])
  })

  it('disables query when there is no current dataset', async () => {
    storeState.currentDataset = null
    const { result } = renderHookWithProviders(() => useObjectDetection())

    // Wait a tick to ensure no fetch fires.
    await new Promise((r) => setTimeout(r, 0))
    expect(mockGetDetections).not.toHaveBeenCalled()
    expect(result.current.data).toBeUndefined()
  })

  it('disables query when episode index is negative', async () => {
    storeState.currentEpisode = null
    const { result } = renderHookWithProviders(() => useObjectDetection())

    await new Promise((r) => setTimeout(r, 0))
    expect(mockGetDetections).not.toHaveBeenCalled()
    expect(result.current.data).toBeUndefined()
  })

  it('filters detections by minConfidence', async () => {
    const { result } = renderHookWithProviders(() => useObjectDetection())

    await waitFor(() => expect(result.current.data).toBeDefined())

    act(() => {
      result.current.setFilters({ classes: [], minConfidence: 0.4 })
    })

    await waitFor(() => {
      expect(result.current.filteredData?.detections_by_frame[0].detections).toHaveLength(2)
      expect(result.current.filteredData?.total_detections).toBe(2)
    })
  })

  it('filters detections by class allow-list', async () => {
    const { result } = renderHookWithProviders(() => useObjectDetection())

    await waitFor(() => expect(result.current.data).toBeDefined())

    act(() => {
      result.current.setFilters({ classes: ['ball'], minConfidence: 0 })
    })

    await waitFor(() => {
      const dets = result.current.filteredData?.detections_by_frame[0].detections ?? []
      expect(dets.every((d: { class_name: string }) => d.class_name === 'ball')).toBe(true)
      expect(dets).toHaveLength(1)
    })
  })

  it('runDetection mutation clears needsRerun on success', async () => {
    const { result } = renderHookWithProviders(() => useObjectDetection())
    await waitFor(() => expect(result.current.data).toBeDefined())

    await act(async () => {
      result.current.runDetection({ confidence_threshold: 0.5 } as never)
    })

    await waitFor(() => {
      expect(mockRunDetection).toHaveBeenCalledWith('ds-1', 0, { confidence_threshold: 0.5 })
      expect(result.current.needsRerun).toBe(false)
    })
  })

  it('clearCache invokes clearDetections API', async () => {
    const { result } = renderHookWithProviders(() => useObjectDetection())
    await waitFor(() => expect(result.current.data).toBeDefined())

    await act(async () => {
      result.current.clearCache()
    })

    await waitFor(() => {
      expect(mockClearDetections).toHaveBeenCalledWith('ds-1', 0)
    })
  })

  it('availableClasses is empty when no data is loaded', async () => {
    storeState.currentDataset = null
    const { result } = renderHookWithProviders(() => useObjectDetection())

    expect(result.current.availableClasses).toEqual([])
  })

  // --- WI-01: anti-pattern (setState inside useMemo) ---------------------
  // The hook places `setNeedsRerun(true)` inside a `useMemo` body. React
  // does not guarantee the memo body runs (or runs only once) for a given
  // dependency change, so the state update is unreliable in principle. In
  // this test environment the flip currently lands; the WI-01 fix should
  // move the logic into `useEffect` for correctness.
  it('flips needsRerun to true when edits become dirty (WI-01)', async () => {
    storeState.isDirty = false
    const { result, rerender } = renderHookWithProviders(() => useObjectDetection())
    await waitFor(() => expect(result.current.data).toBeDefined())

    storeState.isDirty = true
    rerender()

    await waitFor(() => {
      expect(result.current.needsRerun).toBe(true)
    })
  })

  it.skip('keeps needsRerun stable across re-renders without edits (WI-01 scaffold)', () => {
    // Scaffold for the post-fix coverage: once WI-01 moves the side effect
    // into useEffect, this test should assert that re-rendering with
    // hasEdits=false leaves needsRerun untouched.
  })
})
