export type OperatorAdapterMode = 'disabled' | 'simulated' | 'lerobot'
export type OperatorMode = 'teleoperate' | 'record' | 'policy'
export type OperatorAction = 'save' | 'rerecord' | 'pause' | 'resume' | 'finish' | 'cancel'
export type OperatorSessionState =
  | 'disabled'
  | 'idle'
  | 'starting'
  | 'running'
  | 'stopping'
  | 'stopped'
  | 'completed'
  | 'cancelled'
  | 'failed'

export interface OperatorRobot {
  role: 'leader' | 'follower'
  name: string
  embodiment: string
  actuatorCount: number
}

export interface OperatorCamera {
  name: string
  defaultFps: number
}

export interface OperatorCapabilities {
  enabled: boolean
  adapterMode: OperatorAdapterMode
  adapterVersion: number
  protocolVersion: number
  modes: OperatorMode[]
  profiles: string[]
  robots: OperatorRobot[]
  cameras: OperatorCamera[]
  preflightEnabled: boolean
  sessionStartEnabled: boolean
  reason: string | null
}

export interface CalibrationJoint {
  name: string
  id: number
  driveMode: number
  homingOffset: number
  rangeMin: number
  rangeMax: number
}

export interface CalibrationFileCheck {
  role: 'leader' | 'follower'
  fileName: string
  valid: boolean
  hardwareVerified: false
  sha256: string | null
  joints: CalibrationJoint[]
  issues: string[]
}

export interface OperatorCalibrationReport {
  profile: string
  checkedAt: string
  valid: boolean
  hardwareVerified: false
  arms: CalibrationFileCheck[]
}

export interface OperatorSessionSettings {
  controlFps: number
  cameraFps: Record<string, number>
  maxRelativeTarget: number | null
  datasetRoot: string | null
  datasetId: string | null
  repoId: string | null
  task: string
  saveDestination: 'local' | 'local_and_hub'
  hubRepoId: string | null
  numEpisodes: number
  episodeTimeS: number
  resetTimeS: number
  rolloutTimeS: number
  policyPython: string | null
  policyCheckpoint: string | null
  policyCudaVisibleDevices: string | null
}

export interface OperatorTelemetry {
  elapsedS: number
  leader: Record<string, number>
  follower: Record<string, number>
  commanded: Record<string, number>
}

export interface OperatorStatus {
  serviceInstanceId: string
  revision: number
  state: OperatorSessionState
  sessionId: string
  mode: OperatorMode | null
  workerPid: number | null
  lastCommand: OperatorAction | null
  cleanupUnconfirmed: boolean
  error: string | null
  targetHz: number | null
  actualHz: number | null
  loopP95Ms: number | null
  loopMaxMs: number | null
  overruns: number
  latestWorkerLog: string | null
  latestTelemetry: OperatorTelemetry | null
  sessionSettings: OperatorSessionSettings | null
  datasetId: string | null
  episodeIndex: number
  recordingPhase: string | null
  uploadStatus: 'not_requested' | 'succeeded' | 'failed'
  uploadError: string | null
}

export type PreflightCheckOutcome = 'passed' | 'warning' | 'blocking' | 'skipped'
export type PreflightLifecycle = 'completed' | 'cancelled' | 'expired' | 'consumed'

export interface PreflightCheck {
  name: string
  outcome: PreflightCheckOutcome
  detail: string
  remediation: string | null
}

export interface PreflightResult {
  preflightId: string
  lifecycle: PreflightLifecycle
  profile: string
  mode: OperatorMode
  profileFingerprint: string
  resourceFingerprint: string
  createdAt: string
  expiresAt: string
  checks: PreflightCheck[]
  ownershipComplete: boolean
  startEligible: boolean
}

export interface CreateOperatorPreflightRequest {
  commandId: string
  profile: string
  mode: OperatorMode
  uploadRequested?: boolean
}

export interface StartOperatorSessionRequest {
  commandId: string
  mode: OperatorMode
  profile?: string | null
  preflightId?: string | null
  preflightFingerprint?: string | null
  settings?: OperatorSessionSettings | null
}

export interface SendOperatorCommandRequest {
  commandId: string
  action: OperatorAction
  expectedRevision?: number | null
}

export interface OperatorCameraFrame {
  blob: Blob
  capturedAtS: number
}

export interface ParsedOperatorEvent {
  eventId: string
  status: OperatorStatus
}
