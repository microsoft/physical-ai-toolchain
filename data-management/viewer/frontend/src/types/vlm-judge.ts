/**
 * Type definitions for the VLM-as-judge HTTP surface.
 *
 * All fields mirror the backend ``JudgeStatus`` / ``JudgeResponse`` Pydantic
 * models in ``data-management/viewer/backend/src/api/routers/vlm_judge.py``.
 * Snake_case keys from the backend are converted to camelCase by
 * ``transformKeys`` in ``api-client.ts``.
 */

export interface VlmJudgeMilestone {
  name: string
  completed: boolean
  frameRange: string
  evidence: string
}

export interface VlmJudgeResult {
  episodeId: string
  instruction: string
  judgeModel: string
  promptVersion: string
  nFrames: number
  outcomeSuccess: boolean | null
  outcomeConfidence: number
  outcomeNValidVotes: number
  progressPerFrame: number[]
  voc: number
  milestones: VlmJudgeMilestone[]
  failureMode: string | null
  processMethod?: string | null
  cached: boolean
}

export interface VlmJudgeStatus {
  enabled: boolean
  cached: boolean
  judgeModel: string | null
  promptVersion: string | null
  cacheKey: string | null
  backend?: string | null
  processMethod?: string | null
  processMethods?: string[]
  nFrames?: number | null
  result: VlmJudgeResult | null
}

export interface VlmJudgeRunOptions {
  snapshotId?: string
  annotationAuthorId?: string
  /** Restrict the judge to a subset of camera views. */
  views?: string[]
  /** Process-reward scoring technique: 'gvl' or 'chronological'. */
  processMethod?: string
  /** Bypass the disk cache and force a fresh inference run. */
  force?: boolean
}

export interface JudgeJob {
  id: string
  datasetId: string
  status: 'queued' | 'running' | 'succeeded' | 'partial' | 'failed' | 'cancelled'
  total: number
  judged: number
  applied: number
  errors: number
  mode?: 'sample' | 'judge' | 'judge-and-label'
  configRevision?: string
  config: { processMethod?: string; views?: string[]; model?: string }
  targets?: Array<{
    episodeIndex: number
    status: string
    input: SavedInputSnapshot
    result: VlmJudgeResult | null
    resultId: string | null
    cached?: boolean
    error: string | null
    applied: boolean
    comparison?: 'agreement' | 'disagreement' | 'inconclusive'
    sample?: { humanOutcome: 'success' | 'failure' | 'partial' }
  }>
}

export interface JudgeApproval {
  id: string
  sampleJobId: string
  configRevision: string
  current: boolean
}

export interface JudgeResetPreview {
  id: string
  summary: {
    removableFields: number
    acceptedUnchanged: number
    preservedHuman: number
    legacyUnknown: number
    conflicts: number
    episodes: number
    fields: Array<{ episodeIndex: number; field: string; disposition: string }>
  }
}

export interface JudgeReset extends JudgeResetPreview {
  status: 'idle' | 'running' | 'partial' | 'conflicted' | 'succeeded'
}

export type JudgeDatasetAction =
  | { kind: 'cancel' | 'retry'; jobId: string }
  | { kind: 'apply'; jobId: string; indices: number[] }
  | { kind: 'approve'; jobId: string; acknowledgeExceptions: boolean }
  | { kind: 'preview-reset' | 'retry-reset' }
  | { kind: 'confirm-reset'; previewId: string }

interface JudgeEvidenceIdentity {
  runId: string
  resultId: string
  configRevision: string
  input: SavedInputSnapshot
  applicability: 'current' | 'stale' | 'withdrawn'
  applied: boolean
}

export type JudgeEvidence = JudgeEvidenceIdentity &
  (
    | { resultKind: 'judge'; result: VlmJudgeResult }
    | {
        resultKind: 'task-findings'
        result: {
          judgeModel: string
          instruction: string
          findings: {
            pickFrom: string
            object: string
            graspSuccess: boolean
            placeSuccess: boolean
            movementQuality: string
            notes: string
          }
        }
      }
  )

export interface JudgeSampleReference {
  annotationAuthorId: string
  annotationRevision: string
  snapshotId: string
}

export interface JudgeSubmission {
  indices: number[]
  mode: 'sample' | 'judge' | 'judge-and-label'
  options?: VlmJudgeRunOptions
  approvalId?: string
  samples?: Record<number, JudgeSampleReference>
  snapshotIds?: Record<number, string>
}

export interface SavedInputSnapshot {
  snapshotId: string
  datasetId: string
  episodeIndex: number
  principalScopeId: string
  sourceId: string
  sourceRevision: string
  annotationAuthorId: string | null
  annotationRevision: string | null
  editRevision: string | null
  instruction: string
  instructionOrigin: 'annotation' | 'dataset'
}
