import type { EpisodeAnnotation } from './annotations'
import type { EpisodeEditOperations } from './episode-edit'

export type JsonValue =
  boolean | number | string | null | JsonValue[] | { [key: string]: JsonValue }

export interface SourceFileIdentity {
  relativePath: string
  sizeBytes: number
  sha256: string
}

export interface SourceIdentity {
  schemaVersion: string
  datasetId: string
  episodeIndex: number
  sourceFormat: string
  formatVersion: string
  sourceDigest: string
  files: SourceFileIdentity[]
}

export interface AnnotationRevision {
  schemaVersion?: string
  revisionId: string
  source: SourceIdentity
  actorId: string
  createdAt: string
  annotation: Record<string, JsonValue>
  predecessorRevisionId: string | null
}

export interface EditOperation {
  operation: string
  parameters: Record<string, JsonValue>
}

export interface EditRevision {
  schemaVersion?: string
  revisionId: string
  source: SourceIdentity
  actorId: string
  createdAt: string
  operations: EditOperation[]
  predecessorRevisionId: string | null
}

export type ReviewDecisionValue = 'accept' | 'reject'

export interface ReviewDecision {
  schemaVersion?: string
  decisionId: string
  decision: ReviewDecisionValue
  reasonCodes: string[]
  notes: string | null
  actorId: string
  createdAt: string
  source: SourceIdentity
  annotationRevisionId: string
  editRevisionId: string
  qualityRunId: string
}

export type QualityOutcome = 'pass' | 'fail' | 'not_applicable'

export interface QualityCheckResult {
  checkId: string
  required: boolean
  outcome: QualityOutcome
  measurements: Record<string, JsonValue>
  thresholds: Record<string, JsonValue>
  reasonCodes: string[]
}

export interface QualityReport {
  schemaVersion: string
  runId: string
  checkSetVersion: string
  source: SourceIdentity
  actorId: string
  createdAt: string
  episodeChecks: QualityCheckResult[]
  packageChecks: QualityCheckResult[]
}

export interface FeatureRequirement {
  name: string
  dtype: string
  shape: number[]
}

export interface QualityProfile {
  profileId: string
  version: string
  fps: number
  timestampToleranceSeconds: number
  requiredFeatures: FeatureRequirement[]
  optionalFeatures: FeatureRequirement[]
  requiredMetadataFiles: string[]
  calibration: null
  requireTaskLabel: boolean
}

export interface QualityRunRequest {
  runId: string
  actorId: string
  sourceFormat: 'lerobot' | 'hdf5'
  profile: QualityProfile
  reason?: string | null
}

export interface CreateReviewDecisionInput {
  datasetId: string
  episodeIndex: number
  actorId: string
  annotation: EpisodeAnnotation
  edits: EpisodeEditOperations
  qualityReport: QualityReport
  decision: ReviewDecisionValue
  reasonCodes: string[]
  notes?: string | null
}
