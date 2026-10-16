import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef } from 'react'

import { usePrincipalContext } from '@/hooks/use-principal-context'
import {
  fetchGroundingOverview,
  fetchJudgeApprovals,
  fetchJudgeInventory,
  fetchJudgeJob,
  fetchJudgeJobs,
  fetchJudgeReset,
  fetchVlmJudgeSnapshot,
  mutateJudgeDataset,
  submitJudgeJob,
} from '@/lib/api-client'
import { assertEpisodeReadiness, assertSnapshotCurrent } from '@/lib/episode-readiness'
import { recordDiagnosticEvent } from '@/lib/playback-diagnostics'
import type { PrincipalContext } from '@/lib/principal-context'
import { useDatasetStore } from '@/stores/dataset-store'
import { useEpisodeStore } from '@/stores/episode-store'
import type { VlmJudgeResult } from '@/types'
import type { JudgeDatasetAction, JudgeSubmission } from '@/types/vlm-judge'

/** Bind each baseline to the backend-selected annotation or saved labels and task snapshot. */
export function useJudgeSamples(
  datasetId: string,
  indices: number[],
  authors: Record<number, string | null>,
  enabled: boolean,
  labelRevisions: Record<number, string> = {},
) {
  const principal = usePrincipalContext()
  const sourceId = useEpisodeStore((state) => state.currentEpisode?.sourceId)
  const sourceRevision = useEpisodeStore((state) => state.currentEpisode?.sourceRevision)
  const selections = indices.map((index) => ({
    index,
    author: authors[index],
    labelsRevision: labelRevisions[index],
  }))
  const query = useQuery({
    queryKey: [
      'judge-dataset',
      datasetId,
      principal.data?.scopeId,
      sourceId,
      sourceRevision,
      'samples',
      selections,
    ],
    enabled: enabled && !!principal.data?.scopeId && !principal.error && indices.length > 0,
    retry: false,
    queryFn: () =>
      Promise.all(
        selections.map(async ({ index, author, labelsRevision }) => {
          const snapshot =
            author || labelsRevision
              ? await fetchVlmJudgeSnapshot(datasetId, index, author ?? undefined)
              : null
          const annotationReference =
            snapshot?.annotationAuthorId && snapshot.annotationRevision
              ? {
                  annotationAuthorId: snapshot.annotationAuthorId,
                  annotationRevision: snapshot.annotationRevision,
                }
              : {}
          return {
            index,
            reference:
              labelsRevision && snapshot?.instruction.trim()
                ? {
                    ...annotationReference,
                    labelsRevision,
                    snapshotId: snapshot.snapshotId,
                  }
                : author && snapshot?.annotationRevision && snapshot.annotationAuthorId === author
                  ? { ...annotationReference, snapshotId: snapshot.snapshotId }
                  : null,
          }
        }),
      ),
  })
  const denied =
    principal.error ||
    (query.error && 'status' in query.error && [401, 403].includes(Number(query.error.status)))
  return { ...query, data: denied ? undefined : query.data }
}

/** Loads principal-scoped judge state and rejects incomplete episode inventories. */
export function useJudgeDataset(
  datasetId: string,
  enabled: boolean,
  reviewId?: string,
  snapshotId?: string,
  jobOffset = 0,
) {
  const principal = usePrincipalContext()
  const client = useQueryClient()
  const scopeId = principal.data?.scopeId
  const sourceId = useEpisodeStore((state) => state.currentEpisode?.sourceId)
  const sourceRevision = useEpisodeStore((state) => state.currentEpisode?.sourceRevision)
  const key = ['judge-dataset', datasetId, scopeId, sourceId, sourceRevision]
  const ready = enabled && !!scopeId && !principal.error
  const inventory = useQuery({
    queryKey: [
      'judge-dataset',
      datasetId,
      scopeId,
      sourceId,
      sourceRevision,
      'inventory',
      snapshotId,
    ],
    queryFn: async () => {
      const first = await fetchJudgeInventory(datasetId, 0, snapshotId, 250)
      const items = [...first.items]
      while (items.length < first.total) {
        const page = await fetchJudgeInventory(datasetId, items.length, first.snapshotId, 250)
        if (!page.items.length) {
          throw new Error('Episode inventory is incomplete. Refresh before retrying.')
        }
        items.push(...page.items)
      }
      return { ...first, items }
    },
    enabled: ready,
    retry: false,
  })
  const grounding = useQuery({
    queryKey: ['judge-dataset', datasetId, scopeId, sourceId, sourceRevision, 'grounding'],
    queryFn: () => fetchGroundingOverview(datasetId),
    enabled: ready,
    retry: false,
  })
  const annotated = { ...grounding, data: grounding.data?.annotated }
  const usable = {
    ...grounding,
    data: grounding.data && (grounding.data.usable ?? []).map((episode) => episode.episodeIndex),
  }
  const groundingAuthors = Object.fromEntries(
    (grounding.data?.usable ?? []).map((episode) => [episode.episodeIndex, episode.annotatorId]),
  ) as Record<number, string | null>
  const groundingLabelRevisions = Object.fromEntries(
    (grounding.data?.usable ?? [])
      .filter((episode) => episode.labelsRevision)
      .map((episode) => [episode.episodeIndex, episode.labelsRevision]),
  ) as Record<number, string>
  const jobs = useQuery({
    queryKey: ['judge-dataset', datasetId, scopeId, sourceId, sourceRevision, 'jobs', jobOffset],
    queryFn: () => fetchJudgeJobs(datasetId, jobOffset),
    enabled: ready,
    retry: false,
    refetchInterval: ready ? 3000 : false,
  })
  const approvals = useQuery({
    queryKey: ['judge-dataset', datasetId, scopeId, sourceId, sourceRevision, 'approvals'],
    queryFn: () => fetchJudgeApprovals(datasetId),
    enabled: ready,
    retry: false,
  })
  const reset = useQuery({
    queryKey: ['judge-dataset', datasetId, scopeId, sourceId, sourceRevision, 'reset'],
    queryFn: () => fetchJudgeReset(datasetId),
    enabled: ready,
    retry: false,
    refetchInterval: (query) => (ready && query.state.data?.status === 'running' ? 2000 : false),
  })
  const review = useQuery({
    queryKey: [...key, 'review', reviewId],
    queryFn: () => fetchJudgeJob(reviewId!),
    enabled: ready && !!reviewId,
    retry: false,
    refetchInterval: (query) =>
      ready && ['queued', 'running'].includes(query.state.data?.status ?? '') ? 3000 : false,
  })
  const queries = [inventory, grounding, jobs, approvals, reset, review]
  const denied =
    !!principal.error ||
    queries.some(
      (query) =>
        query.error && 'status' in query.error && [401, 403].includes(Number(query.error.status)),
    )
  const refresh = () => {
    void client.invalidateQueries({ queryKey: key })
  }
  const revision = JSON.stringify([
    datasetId,
    scopeId,
    jobs.data?.items.map((job) => [job.id, job.status, job.judged, job.applied]),
    reset.data?.status,
  ])
  const previousRevision = useRef(revision)
  useEffect(() => {
    if (denied || revision === previousRevision.current) return
    previousRevision.current = revision
    void client.invalidateQueries({ queryKey: ['labels', datasetId] })
    void client.invalidateQueries({ queryKey: ['vlm-judge', datasetId] })
    void client.invalidateQueries({ queryKey: ['judge-evidence', datasetId, scopeId] })
  }, [revision, denied, client, datasetId, scopeId])
  const mutation = useMutation({
    mutationFn: async (action: JudgeDatasetAction) => {
      const assertContext = () => {
        if (
          !ready ||
          denied ||
          client.getQueryData<PrincipalContext>(['auth', 'principal-context'])?.scopeId !==
            scopeId ||
          client.getQueryState(['auth', 'principal-context'])?.status !== 'success' ||
          useDatasetStore.getState().currentDataset?.id !== datasetId ||
          useEpisodeStore.getState().currentEpisode?.sourceId !== sourceId ||
          useEpisodeStore.getState().currentEpisode?.sourceRevision !== sourceRevision
        ) {
          throw new Error('Dataset operation context changed. Refresh before retrying.')
        }
      }
      assertContext()
      recordDiagnosticEvent('workspace', 'judge-dataset-action', { action: action.kind })
      const result = await mutateJudgeDataset(datasetId, action)
      assertContext()
      return result
    },
    onSuccess: () => {
      refresh()
      void client.invalidateQueries({ queryKey: ['labels', datasetId] })
      void client.invalidateQueries({ queryKey: ['vlm-judge', datasetId] })
      void client.invalidateQueries({ queryKey: ['judge-evidence', datasetId, scopeId] })
    },
    onError: () => recordDiagnosticEvent('workspace', 'judge-dataset-action-error'),
  })
  return {
    scopeId,
    denied,
    inventory: { ...inventory, data: denied ? undefined : inventory.data },
    annotated: { ...annotated, data: denied ? undefined : annotated.data },
    usable: { ...usable, data: denied ? undefined : usable.data },
    groundingAuthors: denied ? {} : groundingAuthors,
    groundingLabelRevisions: denied ? {} : groundingLabelRevisions,
    jobs: { ...jobs, data: denied ? undefined : jobs.data },
    approvals: { ...approvals, data: denied ? undefined : approvals.data },
    reset: { ...reset, data: denied ? undefined : reset.data },
    review: { ...review, data: denied ? undefined : review.data },
    act: mutation.mutateAsync,
    isPending: mutation.isPending,
    error: principal.error ?? mutation.error,
    refresh,
  }
}

/** Mutually-exclusive outcome labels managed by the judge. */
export const OUTCOME_LABELS = ['SUCCESS', 'FAILURE', 'PARTIAL'] as const

/** Map a judge outcome to its canonical episode label. */
export function outcomeToLabel(result: Pick<VlmJudgeResult, 'outcomeSuccess'>): string | null {
  if (result.outcomeSuccess === true) return 'SUCCESS'
  if (result.outcomeSuccess === false) return 'FAILURE'
  return null
}

/**
 * Replace any existing outcome label with ``outcome`` while preserving every
 * other (custom) label already assigned to the episode.
 */
export function applyOutcomeLabel(existing: string[], outcome: string): string[] {
  const kept = existing.filter(
    (label) => !OUTCOME_LABELS.includes(label as (typeof OUTCOME_LABELS)[number]),
  )
  return [...kept, outcome]
}

export function useVlmJudgeBatch(datasetId: string | null) {
  const queryClient = useQueryClient()
  const mutation = useMutation({
    mutationFn: async (submission: JudgeSubmission) => {
      if (!datasetId || !submission.indices.length)
        throw new Error('Select actual episode IDs before judging.')
      const indices = [...new Set(submission.indices)].sort((first, second) => first - second)
      const scope = () =>
        JSON.stringify([
          useDatasetStore.getState().currentDataset?.id,
          useEpisodeStore.getState().currentEpisode?.sourceId,
          useEpisodeStore.getState().currentEpisode?.sourceRevision,
          queryClient.getQueryData<PrincipalContext>(['auth', 'principal-context'])?.scopeId,
          queryClient.getQueryState(['auth', 'principal-context'])?.status,
        ])
      const capturedScope = scope()
      const assertScope = () => {
        if (scope() !== capturedScope)
          throw new Error('Dataset job context changed. Refresh jobs before resubmitting.')
      }
      await assertEpisodeReadiness(queryClient, datasetId, indices)
      assertScope()
      const snapshotIds: Record<number, string> = {}
      for (const index of indices) {
        const reference = submission.samples?.[index]
        const snapshot = await fetchVlmJudgeSnapshot(
          datasetId,
          index,
          reference?.annotationAuthorId ?? submission.options?.annotationAuthorId,
        )
        assertScope()
        assertSnapshotCurrent(queryClient, datasetId, index, snapshot)
        if (
          reference &&
          (reference.snapshotId !== snapshot.snapshotId ||
            (reference.annotationRevision ?? null) !== (snapshot.annotationRevision ?? null))
        ) {
          throw new Error('Validation sample changed. Refresh its saved reference.')
        }
        snapshotIds[index] = snapshot.snapshotId
      }
      await assertEpisodeReadiness(queryClient, datasetId, indices)
      assertScope()
      recordDiagnosticEvent('workspace', 'judge-job-submit', { targets: indices.length })
      const accepted = await submitJudgeJob(
        datasetId,
        { ...submission, indices, snapshotIds },
        crypto.randomUUID(),
      )
      assertScope()
      return accepted
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['judge-dataset', datasetId] }),
    onError: () => recordDiagnosticEvent('workspace', 'judge-job-submit-error'),
  })
  return {
    submit: mutation.mutateAsync,
    error: mutation.error?.message ?? null,
    isPending: mutation.isPending,
  }
}
