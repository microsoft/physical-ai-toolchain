import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { warmCache } from '@/lib/api-client'
import {
  disableDiagnostics,
  enableDiagnostics,
  isDiagnosticsEnabled,
} from '@/lib/playback-diagnostics'
import { useDatasetStore } from '@/stores'
import type { DatasetInfo, EpisodeMeta } from '@/types'

interface UseDataviewerShellStateOptions {
  datasets?: DatasetInfo[]
  episodes?: EpisodeMeta[]
}

export function useDataviewerShellState({ datasets, episodes }: UseDataviewerShellStateOptions) {
  const [datasetIdState, setDatasetIdState] = useState('')
  const [selectedEpisode, setSelectedEpisode] = useState<number>(0)
  const [diagnosticsVisible, setDiagnosticsVisible] = useState(() => isDiagnosticsEnabled())
  const [isWarmingCache, setIsWarmingCache] = useState(false)
  const setDatasets = useDatasetStore((state) => state.setDatasets)
  const selectDataset = useDatasetStore((state) => state.selectDataset)
  const warmedRef = useRef<string | null>(null)

  useEffect(() => {
    if (!datasets || datasets.length === 0) {
      if (datasetIdState) {
        setDatasetIdState('')
        setSelectedEpisode(0)
      }
      return
    }

    const hasSelectedDataset = datasets.some((dataset) => dataset.id === datasetIdState)

    if (!datasetIdState || !hasSelectedDataset) {
      const autoId = datasets[0].id
      setDatasetIdState(autoId)
      setSelectedEpisode(0)

      if (autoId !== warmedRef.current) {
        warmedRef.current = autoId
        setIsWarmingCache(true)
        void warmCache(autoId, 5).finally(() => setIsWarmingCache(false))
      }
    }
  }, [datasets, datasetIdState])

  useEffect(() => {
    if (!datasets || datasets.length === 0) {
      setDatasets([])
      return
    }

    setDatasets(datasets)

    if (datasetIdState) {
      selectDataset(datasetIdState)
    }
  }, [datasetIdState, datasets, selectDataset, setDatasets])

  const selectedDataset = useMemo(
    () => datasets?.find((dataset) => dataset.id === datasetIdState) ?? null,
    [datasetIdState, datasets],
  )
  const totalEpisodes = episodes?.length ?? selectedDataset?.totalEpisodes ?? 0
  const canGoPreviousEpisode = selectedEpisode > 0
  const canGoNextEpisode = totalEpisodes > 0 && selectedEpisode < totalEpisodes - 1

  const setDatasetId = useCallback((nextDatasetId: string) => {
    setDatasetIdState(nextDatasetId)
    setSelectedEpisode(0)

    if (nextDatasetId && nextDatasetId !== warmedRef.current) {
      warmedRef.current = nextDatasetId
      setIsWarmingCache(true)
      void warmCache(nextDatasetId, 5).finally(() => setIsWarmingCache(false))
    }
  }, [])

  const handlePreviousEpisode = useCallback(() => {
    setSelectedEpisode((currentEpisode) => Math.max(currentEpisode - 1, 0))
  }, [])

  const handleNextEpisode = useCallback(() => {
    if (totalEpisodes === 0) {
      return
    }

    setSelectedEpisode((currentEpisode) => Math.min(currentEpisode + 1, totalEpisodes - 1))
  }, [totalEpisodes])

  const toggleDiagnostics = useCallback(() => {
    setDiagnosticsVisible((currentValue) => {
      if (currentValue) {
        disableDiagnostics()
        return false
      }

      enableDiagnostics()
      return true
    })
  }, [])

  return {
    datasetId: datasetIdState,
    setDatasetId,
    selectedEpisode,
    setSelectedEpisode,
    diagnosticsVisible,
    selectedDataset,
    totalEpisodes,
    canGoPreviousEpisode,
    canGoNextEpisode,
    handlePreviousEpisode,
    handleNextEpisode,
    toggleDiagnostics,
    isWarmingCache,
  }
}
