/**
 * Batch orchestration for the VLM-as-judge over an entire dataset.
 *
 * Loops episodes client-side (cache-first per episode) so the UI can surface
 * live ``done / total`` progress and support cancellation, reusing the same
 * per-episode endpoints as the single-episode panel. Two operations are
 * exposed:
 *
 * - ``runAll`` — run the judge on every episode (no label writes).
 * - ``applyLabelsAll`` — run the judge on every episode and persist its
 *   outcome as the episode's label.
 */

import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useRef, useState } from 'react'

import { fetchVlmJudgeSnapshot, runVlmJudge, setEpisodeLabels } from '@/lib/api-client'
import { assertEpisodeReadiness, assertSnapshotCurrent } from '@/lib/episode-readiness'
import type { PrincipalContext } from '@/lib/principal-context'
import { useDatasetStore } from '@/stores/dataset-store'
import { useLabelStore } from '@/stores/label-store'
import type { VlmJudgeResult, VlmJudgeRunOptions } from '@/types'

import { labelKeys } from './use-labels'
import { vlmJudgeKeys } from './use-vlm-judge'

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

export type BatchPhase = 'judging' | 'labeling'

export interface BatchProgress {
  phase: BatchPhase
  done: number
  total: number
}

export function useVlmJudgeBatch(datasetId: string | null, totalEpisodes: number) {
  const queryClient = useQueryClient()
  const commitEpisodeLabels = useLabelStore((state) => state.commitEpisodeLabels)

  const [progress, setProgress] = useState<BatchProgress | null>(null)
  const [error, setError] = useState<string | null>(null)
  const cancelRef = useRef(false)
  const runningRef = useRef(false)

  const runBatch = useCallback(
    async (phase: BatchPhase, applyLabels: boolean, options?: VlmJudgeRunOptions) => {
      if (!datasetId || totalEpisodes <= 0 || runningRef.current) return
      runningRef.current = true
      cancelRef.current = false
      setError(null)
      setProgress({ phase, done: 0, total: totalEpisodes })
      const scope = () =>
        JSON.stringify([
          useDatasetStore.getState().currentDataset?.id,
          queryClient.getQueryData<PrincipalContext>(['auth', 'principal-context'])?.scopeId,
          useLabelStore.getState().datasetId,
          useLabelStore.getState().principalScopeId,
          useLabelStore.getState().contextGeneration,
        ])
      const capturedScope = scope()
      const assertScope = () => {
        if (scope() !== capturedScope)
          throw new Error('Batch context changed; no further labels were applied.')
      }
      try {
        const targets = Array.from({ length: totalEpisodes }, (_, index) => index)
        await assertEpisodeReadiness(queryClient, datasetId, targets)
        assertScope()
        const principal = queryClient.getQueryData<PrincipalContext>(['auth', 'principal-context'])
        const labelQueryKey = labelKeys.dataset(datasetId, principal?.scopeId)
        let labelEtag = queryClient.getQueryData<{ etag: string | null }>(labelQueryKey)?.etag
        for (let index = 0; index < totalEpisodes; index += 1) {
          if (cancelRef.current) break
          await assertEpisodeReadiness(queryClient, datasetId, targets)
          if (cancelRef.current) break
          const snapshot = await fetchVlmJudgeSnapshot(
            datasetId,
            index,
            options?.annotationAuthorId,
          )
          await assertEpisodeReadiness(queryClient, datasetId, targets)
          assertSnapshotCurrent(queryClient, datasetId, index, snapshot)
          assertScope()
          if (cancelRef.current) break
          const result = await runVlmJudge(datasetId, index, {
            snapshotId: snapshot.snapshotId,
            annotationAuthorId: options?.annotationAuthorId,
            processMethod: options?.processMethod,
            views: options?.views,
          })
          if (cancelRef.current) break
          const outcome = outcomeToLabel(result)
          if (applyLabels && outcome !== null) {
            assertScope()
            await assertEpisodeReadiness(queryClient, datasetId, targets)
            assertScope()
            assertSnapshotCurrent(queryClient, datasetId, index, snapshot)
            const editGeneration = useLabelStore.getState().editGeneration
            const existing = useLabelStore.getState().episodeLabels[index] ?? []
            const next = applyOutcomeLabel(existing, outcome)
            const saved = await setEpisodeLabels(
              datasetId,
              index,
              next,
              labelEtag ? { etag: labelEtag } : { createOnly: true },
            )
            assertScope()
            if (!saved.etag || saved.data.episodeIndex !== index) {
              throw new Error(
                'Label acknowledgment is missing its revision or targets another episode. Reload before retrying.',
              )
            }
            labelEtag = saved.etag
            if (useLabelStore.getState().editGeneration === editGeneration) {
              commitEpisodeLabels(saved.data.episodeIndex, saved.data.labels)
              useLabelStore.setState({ baseEtag: labelEtag })
            }
            if (labelEtag) {
              const nextEtag = labelEtag
              queryClient.setQueryData<{ etag: string | null }>(labelQueryKey, (current) =>
                current ? { ...current, etag: nextEtag } : current,
              )
            }
          }
          setProgress({ phase, done: index + 1, total: totalEpisodes })
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err))
      } finally {
        runningRef.current = false
        queryClient.invalidateQueries({ queryKey: vlmJudgeKeys.all })
        if (applyLabels) {
          queryClient.invalidateQueries({ queryKey: labelKeys.dataset(datasetId) })
        }
        setProgress(null)
      }
    },
    [datasetId, totalEpisodes, commitEpisodeLabels, queryClient],
  )

  const runAll = useCallback(
    (options?: VlmJudgeRunOptions) => runBatch('judging', false, options),
    [runBatch],
  )
  const applyLabelsAll = useCallback(
    (options?: VlmJudgeRunOptions) => runBatch('labeling', true, options),
    [runBatch],
  )
  const cancel = useCallback(() => {
    cancelRef.current = true
  }, [])

  return {
    progress,
    error,
    isRunning: progress !== null,
    runAll,
    applyLabelsAll,
    cancel,
  }
}
