import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { annotationKeys } from '@/hooks/use-annotations'
import { fetchDatasetLabels, labelKeys } from '@/hooks/use-labels'
import { usePrincipalContext } from '@/hooks/use-principal-context'
import { fetchAnnotations } from '@/lib/api-client'
import { type ConflictResolution, mergeLabelSets, mergeThreeWay } from '@/lib/edit-draft-storage'
import { useAnnotationStore, useDatasetStore, useEpisodeStore } from '@/stores'
import { useLabelStore } from '@/stores/label-store'

export function DraftConflictDialog() {
  const queryClient = useQueryClient()
  const principal = usePrincipalContext().data
  const currentDataset = useDatasetStore((state) => state.currentDataset)
  const currentEpisodeIndex = useEpisodeStore((state) => state.currentIndex)
  const annotationConflict = useAnnotationStore((state) => state.conflict)
  const annotationDraft = useAnnotationStore((state) => state.currentAnnotation)
  const annotationBaseline = useAnnotationStore((state) => state.originalAnnotation)
  const resolveAnnotationConflict = useAnnotationStore((state) => state.resolveConflict)
  const labelConflict = useLabelStore((state) => state.conflict)
  const availableLabels = useLabelStore((state) => state.availableLabels)
  const episodeLabels = useLabelStore((state) => state.episodeLabels)
  const savedEpisodeLabels = useLabelStore((state) => state.savedEpisodeLabels)
  const resolveLabelConflict = useLabelStore((state) => state.resolveConflict)
  const [isResolving, setIsResolving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const open = Boolean(annotationConflict || labelConflict)

  const resolve = async (resolution: ConflictResolution) => {
    if (!currentDataset || !principal) return
    setIsResolving(true)
    setError(null)
    try {
      if (annotationConflict && annotationDraft && annotationBaseline && currentEpisodeIndex >= 0) {
        const latest = await fetchAnnotations(currentDataset.id, currentEpisodeIndex)
        const server =
          latest.data.annotations.find(
            (annotation) => annotation.annotatorId === principal.scopeId,
          ) ?? annotationConflict.submitted
        const merged = mergeThreeWay(annotationBaseline, annotationDraft, server, resolution)
        resolveAnnotationConflict(merged, server)
        queryClient.setQueryData(
          annotationKeys.detail(currentDataset.id, currentEpisodeIndex, principal.scopeId),
          latest,
        )
      }

      if (labelConflict) {
        const latest = await fetchDatasetLabels(currentDataset.id)
        const serverEpisodes = Object.fromEntries(
          Object.entries(latest.data.episodes).map(([index, labels]) => [Number(index), labels]),
        )
        const mergedEpisodes = { ...serverEpisodes }
        const episodeIndices = new Set([
          ...Object.keys(serverEpisodes),
          ...Object.keys(episodeLabels),
          ...Object.keys(savedEpisodeLabels),
        ])
        for (const indexText of episodeIndices) {
          const index = Number(indexText)
          mergedEpisodes[index] = mergeLabelSets(
            savedEpisodeLabels[index] ?? [],
            episodeLabels[index] ?? [],
            serverEpisodes[index] ?? [],
          )
        }
        resolveLabelConflict(
          [...new Set([...latest.data.availableLabels, ...availableLabels])],
          mergedEpisodes,
          serverEpisodes,
        )
        queryClient.setQueryData(labelKeys.dataset(currentDataset.id), latest)
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to reload the latest server state')
    } finally {
      setIsResolving(false)
    }
  }

  return (
    <Dialog open={open}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Resolve saved-data conflict</DialogTitle>
          <DialogDescription>
            The server changed after this draft was loaded. Non-overlapping changes merge
            automatically. Choose which version wins where both changed the same field.
          </DialogDescription>
        </DialogHeader>
        {error ? <p className="text-destructive text-sm">{error}</p> : null}
        <DialogFooter>
          <Button variant="outline" disabled={isResolving} onClick={() => void resolve('server')}>
            Prefer server conflicts
          </Button>
          <Button disabled={isResolving} onClick={() => void resolve('local')}>
            Prefer local conflicts
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
