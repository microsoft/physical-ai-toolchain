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
import {
  type ConflictResolution,
  episodeDraftSource,
  mergeLabelSets,
  mergeThreeWay,
} from '@/lib/edit-draft-storage'
import { recordDiagnosticEvent } from '@/lib/playback-diagnostics'
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
    const source = episodeDraftSource(useEpisodeStore.getState().currentEpisode)
    const annotationGeneration = useAnnotationStore.getState().editGeneration
    const labelGeneration = useLabelStore.getState().editGeneration
    const ensureContext = () => {
      if (
        useDatasetStore.getState().currentDataset?.id !== currentDataset.id ||
        useEpisodeStore.getState().currentIndex !== currentEpisodeIndex ||
        queryClient.getQueryData<{ scopeId: string }>(['auth', 'principal-context'])?.scopeId !==
          principal.scopeId ||
        JSON.stringify(episodeDraftSource(useEpisodeStore.getState().currentEpisode)) !==
          JSON.stringify(source)
      ) {
        throw new Error(
          'The active context changed. Resolve the conflict again in its original episode.',
        )
      }
    }
    setIsResolving(true)
    setError(null)
    try {
      if ((annotationConflict?.sourceChanged || labelConflict?.sourceChanged) && !source) {
        throw new Error('The current source is unavailable. Reload before resolving this conflict.')
      }
      if (annotationConflict && annotationDraft && annotationBaseline && currentEpisodeIndex >= 0) {
        const latest = await fetchAnnotations(currentDataset.id, currentEpisodeIndex)
        ensureContext()
        if (useAnnotationStore.getState().editGeneration !== annotationGeneration)
          throw new Error('The annotation changed while reloading. Resolve again.')
        let server = latest.data.annotations.find(
          (annotation) => annotation.annotatorId === principal.scopeId,
        )
        if (!server) {
          useAnnotationStore.getState().initializeAnnotation(principal.scopeId)
          server = useAnnotationStore.getState().currentAnnotation!
        }
        const merged = annotationConflict.sourceChanged
          ? structuredClone(resolution === 'local' ? annotationDraft : server)
          : mergeThreeWay(annotationBaseline, annotationDraft, server, resolution)
        useAnnotationStore.setState({ sourceBinding: source })
        resolveAnnotationConflict(merged, server, latest.etag)
        queryClient.setQueryData(
          annotationKeys.detail(currentDataset.id, currentEpisodeIndex, principal.scopeId),
          latest,
        )
      }

      if (labelConflict) {
        const latest = await fetchDatasetLabels(currentDataset.id)
        ensureContext()
        if (useLabelStore.getState().editGeneration !== labelGeneration)
          throw new Error('Labels changed while reloading. Resolve again.')
        if (labelConflict.sourceChanged && labelConflict.episodeIndex !== currentEpisodeIndex)
          throw new Error('Open the conflicting episode before resolving its source.')
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
          mergedEpisodes[index] =
            labelConflict.sourceChanged && index === labelConflict.episodeIndex
              ? [
                  ...(resolution === 'server'
                    ? (serverEpisodes[index] ?? [])
                    : (episodeLabels[index] ?? [])),
                ]
              : mergeLabelSets(
                  savedEpisodeLabels[index] ?? [],
                  episodeLabels[index] ?? [],
                  serverEpisodes[index] ?? [],
                )
        }
        if (source)
          useLabelStore.setState({
            sourceScopes: {
              ...useLabelStore.getState().sourceScopes,
              [currentEpisodeIndex]: source,
            },
          })
        resolveLabelConflict(
          [...new Set([...latest.data.availableLabels, ...availableLabels])],
          mergedEpisodes,
          serverEpisodes,
        )
        useLabelStore.setState({ baseEtag: latest.etag })
        queryClient.setQueryData(labelKeys.dataset(currentDataset.id, principal.scopeId), latest)
      }
      recordDiagnosticEvent('persistence', 'draft-conflict-resolved', {
        datasetId: currentDataset.id,
        episodeIndex: currentEpisodeIndex,
        resolution,
      })
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
            {annotationConflict?.sourceChanged || labelConflict?.sourceChanged
              ? 'The episode source changed. Prefer server discards the conflicting draft; prefer local explicitly reapplies it to the current source.'
              : 'The server changed after this draft was loaded. Non-overlapping changes merge automatically. Choose which version wins where both changed the same field.'}
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
