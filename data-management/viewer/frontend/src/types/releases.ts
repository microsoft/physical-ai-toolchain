export type ReleaseJobState =
  | 'queued'
  | 'running'
  | 'verifying'
  | 'publishing'
  | 'succeeded'
  | 'cancelled'
  | 'failed'
  | 'conflict'

export interface ReleaseFormat {
  name: 'lerobot'
  version: '3.0'
}

export interface ReleaseEpisodeSelection {
  episodeIndex: number
  decisionId: string
}

export interface ReleaseSubmitRequest {
  releaseId: string
  datasetId: string
  actorId: string
  reason: string
  destinationKind: 'local' | 'azure'
  idempotencyKey: string
  targetFormat: ReleaseFormat
  episodes: ReleaseEpisodeSelection[]
}

export interface EligibleReleaseEpisode {
  episodeIndex: number
  decisionId: string
  qualityRunId: string
}

export interface ExcludedReleaseEpisode {
  episodeIndex: number
  reasonCodes: string[]
}

export interface ReleaseEligibility {
  eligibleEpisodes: EligibleReleaseEpisode[]
  excludedEpisodes: ExcludedReleaseEpisode[]
}

export interface ReleaseVerification {
  manifestPath: string | null
  checksumsPath: string | null
  verified: boolean
}

export interface ReleaseProgress {
  percent: number | null
  message: string
  updatedAt: string | null
}

export interface ReleaseWorkflowResponse extends ReleaseEligibility {
  releaseId: string
  jobId: string
  state: ReleaseJobState
  conflict: string | null
  verification: ReleaseVerification
  progress?: ReleaseProgress
}
