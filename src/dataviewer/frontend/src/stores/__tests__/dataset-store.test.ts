import { beforeEach, describe, expect, it } from 'vitest'

import type { DatasetInfo } from '@/types'

import { useDatasetStore } from '../dataset-store'

const mockDatasets: DatasetInfo[] = [
  {
    id: 'ds-1',
    name: 'Pick and Place',
    totalEpisodes: 100,
    fps: 30,
    features: { observation: { dtype: 'video', shape: [480, 640, 3] } },
    tasks: [{ taskIndex: 0, description: 'Pick up a cube' }],
  },
  {
    id: 'ds-2',
    name: 'Assembly',
    totalEpisodes: 50,
    fps: 15,
    features: {},
    tasks: [],
  },
]

describe('useDatasetStore', () => {
  beforeEach(() => {
    useDatasetStore.getState().reset()
  })

  it('starts with initial state', () => {
    const state = useDatasetStore.getState()
    expect(state.datasets).toEqual([])
    expect(state.currentDataset).toBeNull()
    expect(state.isLoading).toBe(false)
    expect(state.error).toBeNull()
  })

  describe('setDatasets', () => {
    it('sets datasets and clears any existing error', () => {
      useDatasetStore.getState().setError('previous error')
      useDatasetStore.getState().setDatasets(mockDatasets)

      const state = useDatasetStore.getState()
      expect(state.datasets).toEqual(mockDatasets)
      expect(state.error).toBeNull()
    })
  })

  describe('selectDataset', () => {
    it('selects a dataset by ID', () => {
      useDatasetStore.getState().setDatasets(mockDatasets)
      useDatasetStore.getState().selectDataset('ds-2')

      expect(useDatasetStore.getState().currentDataset).toEqual(mockDatasets[1])
    })

    it('sets currentDataset to null for unknown ID', () => {
      useDatasetStore.getState().setDatasets(mockDatasets)
      useDatasetStore.getState().selectDataset('nonexistent')

      expect(useDatasetStore.getState().currentDataset).toBeNull()
    })
  })

  describe('clearSelection', () => {
    it('clears the selected dataset', () => {
      useDatasetStore.getState().setDatasets(mockDatasets)
      useDatasetStore.getState().selectDataset('ds-1')
      useDatasetStore.getState().clearSelection()

      expect(useDatasetStore.getState().currentDataset).toBeNull()
    })
  })

  describe('setLoading', () => {
    it('sets loading state', () => {
      useDatasetStore.getState().setLoading(true)
      expect(useDatasetStore.getState().isLoading).toBe(true)

      useDatasetStore.getState().setLoading(false)
      expect(useDatasetStore.getState().isLoading).toBe(false)
    })
  })

  describe('setError', () => {
    it('sets error and clears loading', () => {
      useDatasetStore.getState().setLoading(true)
      useDatasetStore.getState().setError('Network failure')

      const state = useDatasetStore.getState()
      expect(state.error).toBe('Network failure')
      expect(state.isLoading).toBe(false)
    })

    it('clears error when set to null', () => {
      useDatasetStore.getState().setError('some error')
      useDatasetStore.getState().setError(null)

      expect(useDatasetStore.getState().error).toBeNull()
    })
  })

  describe('reset', () => {
    it('restores initial state', () => {
      useDatasetStore.getState().setDatasets(mockDatasets)
      useDatasetStore.getState().selectDataset('ds-1')
      useDatasetStore.getState().setLoading(true)
      useDatasetStore.getState().setError('err')
      useDatasetStore.getState().reset()

      const state = useDatasetStore.getState()
      expect(state.datasets).toEqual([])
      expect(state.currentDataset).toBeNull()
      expect(state.isLoading).toBe(false)
      expect(state.error).toBeNull()
    })
  })
})
