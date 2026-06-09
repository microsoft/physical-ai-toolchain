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
    cached: boolean
}

export interface VlmJudgeStatus {
    enabled: boolean
    cached: boolean
    judgeModel: string | null
    promptVersion: string | null
    cacheKey: string | null
    result: VlmJudgeResult | null
}

export interface VlmJudgeRunOptions {
    /** Override the dataset-supplied instruction. */
    instruction?: string
    /** Restrict the judge to a subset of camera views. */
    views?: string[]
    /** Bypass the disk cache and force a fresh inference run. */
    force?: boolean
}
