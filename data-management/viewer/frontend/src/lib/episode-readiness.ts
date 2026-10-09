import type { QueryClient } from '@tanstack/react-query'

import {
  getDraftRevision,
  hasPendingDraftWrites,
  loadPersistedAnnotationDraft,
  loadPersistedEditDraft,
  loadPersistedLabelDraft,
} from '@/lib/edit-draft-storage'
import { recordDiagnosticEvent } from '@/lib/playback-diagnostics'
import type { PrincipalContext } from '@/lib/principal-context'
import { useAnnotationStore } from '@/stores/annotation-store'
import { useDatasetStore } from '@/stores/dataset-store'
import { useEditStore } from '@/stores/edit-store'
import { useEpisodeStore } from '@/stores/episode-store'
import { useLabelStore } from '@/stores/label-store'
import type { SavedInputSnapshot } from '@/types/vlm-judge'

export interface EpisodeReadinessBlocker {
  episodeIndex: number | null
  resource: 'identity' | 'annotation' | 'labels' | 'episode-edit' | 'lookup'
  reason: string
}

export interface EpisodeReadiness {
  ready: boolean
  blockers: EpisodeReadinessBlocker[]
}

const persistedReadiness = new WeakMap<
  QueryClient,
  { key: string; revision: number; blockers: EpisodeReadinessBlocker[] }
>()

function different(first: unknown, second: unknown): boolean {
  return JSON.stringify(first) !== JSON.stringify(second)
}

export function pendingWorkspaceChanges(
  queryClient?: QueryClient,
  options: { episodeIndex?: number } = {},
): string[] {
  const annotation = useAnnotationStore.getState()
  const labels = useLabelStore.getState()
  const edits = useEditStore.getState()
  // Label drafts are dataset-wide and survive episode navigation, so only the current episode's matter there.
  const dirtyLabels = Object.entries(labels.episodeLabels)
    .filter(([index, values]) => different(values, labels.savedEpisodeLabels[Number(index)] ?? []))
    .map(([index]) => Number(index))
    .filter((index) => options.episodeIndex === undefined || index === options.episodeIndex)
  return [
    queryClient?.isMutating({ mutationKey: ['episode-save'] }) ? 'a save in progress' : null,
    annotation.isDirty || annotation.isSaving ? 'annotation changes' : null,
    annotation.conflict || labels.conflict ? 'an unresolved conflict' : null,
    annotation.draftError || labels.draftError || edits.draftError ? 'draft recovery errors' : null,
    labels.isSaving ? 'a label save in progress' : null,
    dirtyLabels.length ? `label changes for episode ${dirtyLabels.join(', ')}` : null,
    edits.isDirty ? 'frame or subtask edits' : null,
  ].filter((reason): reason is string => reason !== null)
}

export function hasPendingWorkspaceChanges(queryClient?: QueryClient): boolean {
  return pendingWorkspaceChanges(queryClient).length > 0
}

export async function checkEpisodeReadiness(
  queryClient: QueryClient,
  datasetId: string,
  episodeIndices: readonly number[],
): Promise<EpisodeReadiness> {
  const principal = queryClient.getQueryData<PrincipalContext>(['auth', 'principal-context'])
  const blockers: EpisodeReadinessBlocker[] = []
  const block = (
    episodeIndex: number | null,
    resource: EpisodeReadinessBlocker['resource'],
    reason: string,
  ) => {
    blockers.push({ episodeIndex, resource, reason })
  }
  if (
    !principal?.scopeId ||
    queryClient.getQueryState(['auth', 'principal-context'])?.status === 'error'
  ) {
    block(null, 'identity', 'Reload your identity before judging.')
    return { ready: false, blockers }
  }
  if (queryClient.isMutating({ mutationKey: ['episode-save'] }) > 0) {
    block(null, 'lookup', 'Wait for pending episode saves before judging.')
    return { ready: false, blockers }
  }
  const selected = new Set(episodeIndices)
  const dataset = useDatasetStore.getState()
  const activeIndex = useEpisodeStore.getState().currentIndex
  const activeEpisode = useEpisodeStore.getState().currentEpisode
  if (dataset.currentDataset?.id === datasetId && activeIndex >= 0) selected.add(activeIndex)
  if (!selected.size || [...selected].some((index) => !Number.isInteger(index) || index < 0)) {
    block(null, 'lookup', 'Select valid episodes before judging.')
    return { ready: false, blockers }
  }
  const annotation = useAnnotationStore.getState()
  const labels = useLabelStore.getState()
  const edits = useEditStore.getState()
  for (const index of selected) {
    const episode = useEpisodeStore.getState().currentEpisode
    if (
      dataset.currentDataset?.id === datasetId &&
      index === activeIndex &&
      (annotation.resourceKey !== JSON.stringify([datasetId, index, principal.scopeId]) ||
        labels.datasetId !== datasetId ||
        labels.principalScopeId !== principal.scopeId ||
        edits.datasetId !== datasetId ||
        edits.episodeIndex !== index ||
        edits.principalScopeId !== principal.scopeId ||
        !episode?.sourceId ||
        !episode.sourceRevision)
    ) {
      block(index, 'lookup', 'Wait for the active episode and its saved resource contexts to load.')
    }
    if (
      edits.datasetId === datasetId &&
      edits.episodeIndex === index &&
      episode?.meta.index === index &&
      episode.sourceId &&
      episode.sourceRevision &&
      edits.serverBaseline &&
      (episode.sourceId !== edits.serverBaseline.sourceId ||
        episode.sourceRevision !== edits.serverBaseline.sourceRevision)
    ) {
      block(
        index,
        'episode-edit',
        'The episode source changed. Reload and resolve the draft before judging.',
      )
    }
    if (
      annotation.resourceKey === JSON.stringify([datasetId, index, principal.scopeId]) &&
      (annotation.isDirty ||
        annotation.isSaving ||
        annotation.conflict ||
        !annotation.draftHydrated ||
        annotation.draftError)
    ) {
      block(index, 'annotation', 'Save or resolve annotation changes before judging.')
    }
    if (
      labels.datasetId === datasetId &&
      labels.principalScopeId === principal.scopeId &&
      (!labels.isLoaded ||
        !labels.draftHydrated ||
        labels.draftError ||
        labels.isSaving ||
        labels.conflict ||
        different(labels.episodeLabels[index] ?? [], labels.savedEpisodeLabels[index] ?? []))
    ) {
      block(index, 'labels', 'Save or resolve label changes before judging.')
    }
    if (
      edits.datasetId === datasetId &&
      edits.episodeIndex === index &&
      edits.principalScopeId === principal.scopeId &&
      (edits.isDirty ||
        !edits.draftHydrated ||
        edits.draftError ||
        edits.validationErrors.length ||
        !edits.serverBaseline)
    ) {
      block(index, 'episode-edit', 'Save or resolve episode edits before judging.')
    }
  }
  const draftRevision = getDraftRevision()
  const scanKey = JSON.stringify([
    datasetId,
    principal.scopeId,
    [...selected].sort((first, second) => first - second),
  ])
  const cachedScan = persistedReadiness.get(queryClient)
  try {
    if (
      cachedScan?.key === scanKey &&
      cachedScan.revision === draftRevision &&
      !hasPendingDraftWrites()
    ) {
      blockers.push(...cachedScan.blockers)
    } else {
      const scanStart = blockers.length
      const labelDraft = await loadPersistedLabelDraft(datasetId, principal.scopeId)
      for (const index of selected) {
        if (
          labelDraft &&
          different(
            labelDraft.draft.episodeLabels[index] ?? [],
            labelDraft.baseline.episodeLabels[index] ?? [],
          )
        ) {
          block(index, 'labels', 'Resolve the recovered label draft before judging.')
        }
        const annotationDraft = await loadPersistedAnnotationDraft(
          datasetId,
          index,
          principal.scopeId,
        )
        if (annotationDraft && different(annotationDraft.draft, annotationDraft.baseline)) {
          block(index, 'annotation', 'Resolve the recovered annotation draft before judging.')
        }
        const editDraft = await loadPersistedEditDraft(datasetId, index, principal.scopeId)
        if (
          editDraft &&
          (!editDraft.baseline || different(editDraft.draft, editDraft.baseline.operations))
        ) {
          block(index, 'episode-edit', 'Resolve the recovered edit draft before judging.')
        }
      }
      if (draftRevision === getDraftRevision() && !hasPendingDraftWrites()) {
        persistedReadiness.set(queryClient, {
          key: scanKey,
          revision: draftRevision,
          blockers: blockers.slice(scanStart),
        })
      }
    }
  } catch {
    block(null, 'lookup', 'Episode draft readiness could not be checked.')
    recordDiagnosticEvent('persistence', 'episode-readiness-failed', {
      datasetId: datasetId.replace(/[\r\n]/g, ''),
    })
  }
  if (
    queryClient.isMutating({ mutationKey: ['episode-save'] }) > 0 ||
    queryClient.getQueryState(['auth', 'principal-context'])?.status === 'error' ||
    queryClient
      .getQueryCache()
      .findAll()
      .some(
        (query) =>
          ['datasets', 'labels', 'annotations', 'episode-edits'].includes(
            String(query.queryKey[0]),
          ) &&
          query.queryKey.includes(datasetId) &&
          query.state.status === 'error',
      ) ||
    hasPendingDraftWrites() ||
    getDraftRevision() !== draftRevision ||
    queryClient.getQueryData(['auth', 'principal-context']) !== principal ||
    useDatasetStore.getState() !== dataset ||
    useEpisodeStore.getState().currentIndex !== activeIndex ||
    useEpisodeStore.getState().currentEpisode !== activeEpisode ||
    useAnnotationStore.getState() !== annotation ||
    useLabelStore.getState() !== labels ||
    useEditStore.getState() !== edits
  ) {
    block(
      null,
      'lookup',
      'Episode state changed while checking readiness. Check again before judging.',
    )
  }
  return { ready: blockers.length === 0, blockers }
}

export async function assertEpisodeReadiness(
  queryClient: QueryClient,
  datasetId: string,
  episodeIndices: readonly number[],
): Promise<void> {
  const readiness = await checkEpisodeReadiness(queryClient, datasetId, episodeIndices)
  if (!readiness.ready) {
    const first = readiness.blockers[0]
    throw new Error(
      `${first.episodeIndex === null ? '' : `Episode ${first.episodeIndex}: `}${first.reason}`,
    )
  }
}

export function assertSnapshotCurrent(
  queryClient: QueryClient,
  datasetId: string,
  episodeIndex: number,
  snapshot: SavedInputSnapshot,
): void {
  const principal = queryClient.getQueryData<PrincipalContext>(['auth', 'principal-context'])
  if (
    !snapshot.snapshotId ||
    snapshot.datasetId !== datasetId ||
    snapshot.episodeIndex !== episodeIndex ||
    snapshot.principalScopeId !== principal?.scopeId
  ) {
    throw new Error(
      'The saved snapshot belongs to another episode or identity. Reload before judging.',
    )
  }
  const episode = useEpisodeStore.getState()
  if (
    useDatasetStore.getState().currentDataset?.id === datasetId &&
    episode.currentIndex === episodeIndex &&
    (snapshot.sourceId !== episode.currentEpisode?.sourceId ||
      snapshot.sourceRevision !== episode.currentEpisode?.sourceRevision ||
      snapshot.annotationRevision !== useAnnotationStore.getState().baseEtag ||
      snapshot.editRevision !== useEditStore.getState().serverBaseline?.etag)
  ) {
    throw new Error('The saved snapshot changed. Reload the episode before judging.')
  }
}
