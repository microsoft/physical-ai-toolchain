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

import { runVlmJudge, setEpisodeLabels } from '@/lib/api-client'
import { useLabelStore } from '@/stores/label-store'
import type { VlmJudgeResult, VlmJudgeRunOptions } from '@/types'

import { labelKeys } from './use-labels'
import { vlmJudgeKeys } from './use-vlm-judge'

/** Mutually-exclusive outcome labels managed by the judge. */
export const OUTCOME_LABELS = ['SUCCESS', 'FAILURE', 'PARTIAL'] as const

/** Map a judge outcome to its canonical episode label. */
export function outcomeToLabel(result: Pick<VlmJudgeResult, 'outcomeSuccess'>): string {
  if (result.outcomeSuccess === true) return 'SUCCESS'
  if (result.outcomeSuccess === false) return 'FAILURE'
  return 'PARTIAL'
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

  const runBatch = useCallback(
    async (phase: BatchPhase, applyLabels: boolean, options?: VlmJudgeRunOptions) => {
      if (!datasetId || totalEpisodes <= 0) return
      cancelRef.current = false
      setError(null)
      setProgress({ phase, done: 0, total: totalEpisodes })
      try {
        for (let index = 0; index < totalEpisodes; index += 1) {
          if (cancelRef.current) break
          const result = await runVlmJudge(datasetId, index, {
            processMethod: options?.processMethod,
          })
          if (applyLabels) {
            const existing = useLabelStore.getState().episodeLabels[index] ?? []
            const next = applyOutcomeLabel(existing, outcomeToLabel(result))
            const saved = await setEpisodeLabels(datasetId, index, next)
            commitEpisodeLabels(saved.episodeIndex, saved.labels)
          }
          setProgress({ phase, done: index + 1, total: totalEpisodes })
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err))
      } finally {
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
