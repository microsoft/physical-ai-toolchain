import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { createQueryWrapper } from './test-utils'

// ============================================================================
// Mocks
// ============================================================================

const mockGetDetections = vi.fn()
const mockRunDetection = vi.fn()
const mockClearDetections = vi.fn()

vi.mock('@/api/detection', () => ({
  getDetections: (...args: unknown[]) => mockGetDetections(...args),
  runDetection: (...args: unknown[]) => mockRunDetection(...args),
  clearDetections: (...args: unknown[]) => mockClearDetections(...args),
}))

let mockCurrentDataset: { id: string } | null = null
let mockCurrentEpisode: { meta: { index: number } } | null = null
let mockIsDirty = false

vi.mock('@/stores', () => ({
  useDatasetStore: (selector: (state: Record<string, unknown>) => unknown) =>
    selector({ currentDataset: mockCurrentDataset }),
  useEpisodeStore: (selector: (state: Record<string, unknown>) => unknown) =>
    selector({ currentEpisode: mockCurrentEpisode }),
  useEditDirtyState: () => ({ isDirty: mockIsDirty }),
}))

// ============================================================================
// Fixtures
// ============================================================================

const detectionData = {
  total_frames: 10,
  processed_frames: 10,
  total_detections: 3,
  class_summary: { cup: 2, bottle: 1 },
  detections_by_frame: [
    {
      frame_index: 0,
      detections: [
        { class_name: 'cup', confidence: 0.9, bbox: [0, 0, 50, 50] },
        { class_name: 'cup', confidence: 0.3, bbox: [10, 10, 60, 60] },
      ],
    },
    {
      frame_index: 1,
      detections: [{ class_name: 'bottle', confidence: 0.7, bbox: [20, 20, 70, 70] }],
    },
  ],
}

// ============================================================================
// Tests
// ============================================================================

describe('detectionKeys', () => {
  it('generates hierarchical query keys', async () => {
    const { detectionKeys } = await import('@/hooks/use-object-detection')

    expect(detectionKeys.all).toEqual(['detection'])
    expect(detectionKeys.episode('ds-1', 0)).toEqual(['detection', 'ds-1', 0])
  })
})

// ============================================================================
// useObjectDetection
// ============================================================================

describe('useObjectDetection', () => {
  let wrapper: ReturnType<typeof createQueryWrapper>

  beforeEach(() => {
    wrapper = createQueryWrapper()
    mockGetDetections.mockReset()
    mockRunDetection.mockReset()
    mockClearDetections.mockReset()
    mockCurrentDataset = { id: 'ds-1' }
    mockCurrentEpisode = { meta: { index: 2 } }
    mockIsDirty = false
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('fetches detections when dataset and episode are available', async () => {
    mockGetDetections.mockResolvedValue(detectionData)
    const { useObjectDetection } = await import('@/hooks/use-object-detection')
    const { result } = renderHook(() => useObjectDetection(), { wrapper })

    await waitFor(() => {
      expect(result.current.data).toBeDefined()
    })

    expect(mockGetDetections).toHaveBeenCalledWith('ds-1', 2)
    expect(result.current.data).toEqual(detectionData)
    expect(result.current.isLoading).toBe(false)
  })

  it('does not fetch when dataset is missing', async () => {
    mockCurrentDataset = null
    const { useObjectDetection } = await import('@/hooks/use-object-detection')
    const { result } = renderHook(() => useObjectDetection(), { wrapper })

    expect(result.current.isLoading).toBe(false)
    expect(mockGetDetections).not.toHaveBeenCalled()
  })

  it('does not fetch when episode index is negative', async () => {
    mockCurrentEpisode = null
    const { useObjectDetection } = await import('@/hooks/use-object-detection')
    renderHook(() => useObjectDetection(), { wrapper })

    expect(mockGetDetections).not.toHaveBeenCalled()
  })

  it('runs detection mutation and updates query data', async () => {
    mockGetDetections.mockResolvedValue(null)
    const updatedData = { ...detectionData, total_detections: 5 }
    mockRunDetection.mockResolvedValue(updatedData)

    const { useObjectDetection } = await import('@/hooks/use-object-detection')
    const { result } = renderHook(() => useObjectDetection(), { wrapper })

    act(() => {
      result.current.runDetection({ model: 'yolo11n', confidence: 0.5 })
    })

    await waitFor(() => {
      expect(result.current.isRunning).toBe(false)
    })

    expect(mockRunDetection).toHaveBeenCalledWith('ds-1', 2, {
      model: 'yolo11n',
      confidence: 0.5,
    })
  })

  it('clears detection cache via mutation', async () => {
    mockGetDetections.mockResolvedValue(detectionData)
    mockClearDetections.mockResolvedValue(undefined)

    const { useObjectDetection } = await import('@/hooks/use-object-detection')
    const { result } = renderHook(() => useObjectDetection(), { wrapper })

    await waitFor(() => {
      expect(result.current.data).toBeDefined()
    })

    act(() => {
      result.current.clearCache()
    })

    await waitFor(() => {
      expect(mockClearDetections).toHaveBeenCalledWith('ds-1', 2)
    })
  })

  it('filters detections by class name', async () => {
    mockGetDetections.mockResolvedValue(detectionData)
    const { useObjectDetection } = await import('@/hooks/use-object-detection')
    const { result } = renderHook(() => useObjectDetection(), { wrapper })

    await waitFor(() => {
      expect(result.current.data).toBeDefined()
    })

    act(() => {
      result.current.setFilters({ classes: ['bottle'], minConfidence: 0 })
    })

    await waitFor(() => {
      const filtered = result.current.filteredData
      expect(filtered).not.toBeNull()
      const allDetections = filtered!.detections_by_frame.flatMap((f) => f.detections)
      expect(allDetections).toHaveLength(1)
      expect(allDetections[0].class_name).toBe('bottle')
    })
  })

  it('filters detections by minimum confidence', async () => {
    mockGetDetections.mockResolvedValue(detectionData)
    const { useObjectDetection } = await import('@/hooks/use-object-detection')
    const { result } = renderHook(() => useObjectDetection(), { wrapper })

    await waitFor(() => {
      expect(result.current.data).toBeDefined()
    })

    act(() => {
      result.current.setFilters({ classes: [], minConfidence: 0.5 })
    })

    await waitFor(() => {
      const filtered = result.current.filteredData
      expect(filtered).not.toBeNull()
      const allDetections = filtered!.detections_by_frame.flatMap((f) => f.detections)
      // Only cup 0.9 and bottle 0.7 pass; cup 0.3 is excluded
      expect(allDetections).toHaveLength(2)
    })
  })

  it('returns null filteredData when no detection data', async () => {
    mockGetDetections.mockResolvedValue(null)
    const { useObjectDetection } = await import('@/hooks/use-object-detection')
    const { result } = renderHook(() => useObjectDetection(), { wrapper })

    expect(result.current.filteredData).toBeNull()
  })

  it('extracts available classes from class_summary', async () => {
    mockGetDetections.mockResolvedValue(detectionData)
    const { useObjectDetection } = await import('@/hooks/use-object-detection')
    const { result } = renderHook(() => useObjectDetection(), { wrapper })

    await waitFor(() => {
      expect(result.current.data).toBeDefined()
    })

    expect(result.current.availableClasses).toEqual(['cup', 'bottle'])
  })

  it('returns empty availableClasses when no data', async () => {
    mockCurrentDataset = null
    const { useObjectDetection } = await import('@/hooks/use-object-detection')
    const { result } = renderHook(() => useObjectDetection(), { wrapper })

    expect(result.current.availableClasses).toEqual([])
  })

  // Documents existing bug: useMemo triggers setNeedsRerun side effect
  it('sets needsRerun when edit dirty state is true', async () => {
    mockIsDirty = true
    mockGetDetections.mockResolvedValue(detectionData)
    const { useObjectDetection } = await import('@/hooks/use-object-detection')
    const { result } = renderHook(() => useObjectDetection(), { wrapper })

    expect(result.current.needsRerun).toBe(true)
  })

  it('provides default filter values', async () => {
    mockGetDetections.mockResolvedValue(null)
    const { useObjectDetection } = await import('@/hooks/use-object-detection')
    const { result } = renderHook(() => useObjectDetection(), { wrapper })

    expect(result.current.filters).toEqual({
      classes: [],
      minConfidence: 0.25,
    })
  })
})
