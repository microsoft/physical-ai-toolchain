import { useIsFetching, useIsMutating, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState, useSyncExternalStore } from 'react'

import { getDraftRevision, subscribeDraftChanges } from '@/lib/edit-draft-storage'
import { checkEpisodeReadiness, type EpisodeReadiness } from '@/lib/episode-readiness'
import { fetchPrincipalContext } from '@/lib/principal-context'
import { useAnnotationStore } from '@/stores/annotation-store'
import { useDatasetStore } from '@/stores/dataset-store'
import { useEditStore } from '@/stores/edit-store'
import { useEpisodeStore } from '@/stores/episode-store'
import { useLabelStore } from '@/stores/label-store'

export function useEpisodeReadiness(
  datasetId: string,
  episodeIndices: readonly number[],
  enabled = true,
) {
  const queryClient = useQueryClient()
  const pendingSaves = useIsMutating({ mutationKey: ['episode-save'] })
  const pendingReads = useIsFetching()
  const principal = useQuery({
    queryKey: ['auth', 'principal-context'],
    queryFn: fetchPrincipalContext,
    staleTime: Infinity,
    enabled,
  })
  const annotation = useAnnotationStore()
  const labels = useLabelStore()
  const edits = useEditStore()
  const dataset = useDatasetStore((state) => state.currentDataset)
  const activeIndex = useEpisodeStore((state) => state.currentIndex)
  const activeEpisode = useEpisodeStore((state) => state.currentEpisode)
  const indicesKey = JSON.stringify(episodeIndices)
  const draftRevision = useSyncExternalStore(
    subscribeDraftChanges,
    getDraftRevision,
    getDraftRevision,
  )
  const [resolved, setResolved] = useState<{
    datasetId: string
    indicesKey: string
    annotation: typeof annotation
    labels: typeof labels
    edits: typeof edits
    principal: typeof principal.data
    dataset: typeof dataset
    activeIndex: number
    activeEpisode: typeof activeEpisode
    draftRevision: number
    readiness: EpisodeReadiness
  } | null>(null)

  useEffect(() => {
    if (!enabled) return
    let active = true
    void checkEpisodeReadiness(queryClient, datasetId, JSON.parse(indicesKey)).then((readiness) => {
      if (active)
        setResolved({
          datasetId,
          indicesKey,
          annotation,
          labels,
          edits,
          principal: principal.data,
          dataset,
          activeIndex,
          activeEpisode,
          draftRevision,
          readiness,
        })
    })
    return () => {
      active = false
    }
  }, [
    queryClient,
    datasetId,
    indicesKey,
    annotation,
    labels,
    edits,
    principal.data,
    principal.error,
    dataset,
    activeIndex,
    activeEpisode,
    draftRevision,
    enabled,
    pendingSaves,
    pendingReads,
  ])

  const current =
    enabled &&
    pendingSaves === 0 &&
    !principal.error &&
    resolved?.datasetId === datasetId &&
    resolved.indicesKey === indicesKey &&
    resolved.annotation === annotation &&
    resolved.labels === labels &&
    resolved.edits === edits &&
    resolved.principal === principal.data &&
    resolved.dataset === dataset &&
    resolved.activeIndex === activeIndex &&
    resolved.activeEpisode === activeEpisode &&
    resolved.draftRevision === draftRevision
  const readiness = current ? resolved.readiness : null
  const first = readiness?.blockers[0]
  return {
    ready: readiness?.ready === true,
    reason: readiness?.ready
      ? null
      : first
        ? `${first.episodeIndex === null ? '' : `Episode ${first.episodeIndex}: `}${first.reason}`
        : 'Checking saved episode readiness.',
  }
}
