import { handleResponse, mutationHeaders, requestHeaders, transformKeys } from '@/lib/api-client'

const API_BASE = '/api/operator'

export type OperatorAdapterMode = 'disabled' | 'simulated' | 'lerobot'
export type OperatorMode = 'teleoperate' | 'record' | 'policy'
export type OperatorAction = 'save' | 'rerecord' | 'pause' | 'resume' | 'finish' | 'cancel'
export type OperatorSessionState =
  'disabled' | 'idle' | 'starting' | 'running' | 'stopping' | 'completed' | 'cancelled' | 'failed'

export interface OperatorCapabilities {
  enabled: boolean
  adapterMode: OperatorAdapterMode
  adapterVersion: number
  protocolVersion: number
  modes: OperatorMode[]
  profiles: string[]
  robots?: OperatorRobot[]
  cameras?: OperatorCamera[]
  preflightEnabled?: boolean
  sessionStartEnabled?: boolean
  reason: string | null
}

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

export interface OperatorSessionSettings {
  controlFps: number
  cameraFps: Record<string, number>
  maxRelativeTarget: number | null
  datasetName: string
  task: string
  saveDestination: 'local' | 'local_and_hub'
  hubRepoId: string | null
  numEpisodes: number
  episodeTimeS: number
  resetTimeS: number
  rolloutTimeS: number
}

export interface OperatorTelemetry {
  elapsedS: number
  leader: Record<string, number>
  follower: Record<string, number>
  commanded: Record<string, number>
}

export type PreflightOutcome = 'passed' | 'warning' | 'blocking' | 'skipped'
export interface PreflightCheck {
  name: string
  outcome: PreflightOutcome
  detail: string
  remediation: string | null
}
export interface PreflightResult {
  preflightId: string
  lifecycle: 'completed' | 'cancelled' | 'expired' | 'consumed'
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
  targetHz?: number | null
  actualHz?: number | null
  loopP95Ms?: number | null
  loopMaxMs?: number | null
  overruns?: number
  latestWorkerLog?: string | null
  latestTelemetry?: OperatorTelemetry | null
  sessionSettings?: OperatorSessionSettings | null
  datasetId?: string | null
  episodeIndex?: number
  recordingPhase?: string | null
  uploadStatus?: 'not_requested' | 'succeeded' | 'failed'
  uploadError?: string | null
}

export async function fetchOperatorCapabilities(): Promise<OperatorCapabilities> {
  const response = await fetch(`${API_BASE}/capabilities`, {
    headers: await requestHeaders(),
  })
  return transformKeys<OperatorCapabilities>(await handleResponse<unknown>(response))
}

export async function fetchOperatorStatus(): Promise<OperatorStatus> {
  const response = await fetch(`${API_BASE}/status`, {
    headers: await requestHeaders(),
  })
  return transformKeys<OperatorStatus>(await handleResponse<unknown>(response))
}

export async function fetchOperatorCameraFrame(camera: string, signal: AbortSignal): Promise<Blob> {
  const response = await fetch(`${API_BASE}/cameras/${encodeURIComponent(camera)}/frame`, {
    headers: await requestHeaders(),
    signal,
    cache: 'no-store',
  })
  if (!response.ok) {
    throw new Error(
      response.status === 404
        ? 'Camera frame is not available yet'
        : `Camera preview failed (${response.status})`,
    )
  }
  return response.blob()
}

export interface ParsedOperatorEvent {
  eventId: string
  status: OperatorStatus
}

export async function startOperatorSession(
  mode: OperatorMode,
  commandId: string,
  preflight?: PreflightResult,
  settings?: OperatorSessionSettings,
): Promise<OperatorStatus> {
  const body: Record<string, unknown> = { command_id: commandId, mode }
  if (preflight) {
    body.profile = preflight.profile
    body.preflight_id = preflight.preflightId
    body.preflight_fingerprint = preflight.resourceFingerprint
  }
  if (settings) {
    body.settings = {
      control_fps: settings.controlFps,
      camera_fps: settings.cameraFps,
      max_relative_target: settings.maxRelativeTarget,
      dataset_name: settings.datasetName,
      task: settings.task,
      save_destination: settings.saveDestination,
      hub_repo_id: settings.hubRepoId,
      num_episodes: settings.numEpisodes,
      episode_time_s: settings.episodeTimeS,
      reset_time_s: settings.resetTimeS,
      rollout_time_s: settings.rolloutTimeS,
    }
  }
  const response = await fetch(`${API_BASE}/sessions`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(await mutationHeaders()),
    },
    body: JSON.stringify(body),
  })
  return transformKeys<OperatorStatus>(await handleResponse<unknown>(response))
}

export function parseOperatorEventBlock(block: string): ParsedOperatorEvent | null {
  let eventId = ''
  let eventType = ''
  let data = ''
  for (const line of block.split('\n')) {
    if (line.startsWith('id:')) eventId = line.slice(3).trim()
    if (line.startsWith('event:')) eventType = line.slice(6).trim()
    if (line.startsWith('data:')) data += line.slice(5).trim()
  }
  if (!eventId || !data || !['snapshot', 'status'].includes(eventType)) return null
  try {
    return {
      eventId,
      status: transformKeys<OperatorStatus>(JSON.parse(data)),
    }
  } catch {
    return null
  }
}

export async function streamOperatorEvents(
  lastEventId: string | null,
  onEvent: (event: ParsedOperatorEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  const headers = await requestHeaders()
  if (lastEventId) headers['Last-Event-ID'] = lastEventId
  const response = await fetch(`${API_BASE}/events`, { headers, signal })
  if (!response.ok) {
    await handleResponse<never>(response)
  }
  if (!response.body) throw new Error('Operator event stream is unavailable')

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (!signal.aborted) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true }).replaceAll('\r\n', '\n')
    let boundary = buffer.indexOf('\n\n')
    while (boundary >= 0) {
      const block = buffer.slice(0, boundary)
      buffer = buffer.slice(boundary + 2)
      const event = parseOperatorEventBlock(block)
      if (event) onEvent(event)
      boundary = buffer.indexOf('\n\n')
    }
  }
}

export async function sendOperatorCommand(
  status: OperatorStatus,
  action: OperatorAction,
  commandId: string,
): Promise<OperatorStatus> {
  const response = await fetch(`${API_BASE}/sessions/${status.sessionId}/commands`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(await mutationHeaders()),
    },
    body: JSON.stringify({
      command_id: commandId,
      action,
    }),
  })
  return transformKeys<OperatorStatus>(await handleResponse<unknown>(response))
}

export async function stopOperatorSession(
  status: OperatorStatus,
  commandId: string,
): Promise<OperatorStatus> {
  const params = new URLSearchParams({ command_id: commandId })
  const response = await fetch(`${API_BASE}/sessions/${status.sessionId}?${params}`, {
    method: 'DELETE',
    headers: await mutationHeaders(),
  })
  return transformKeys<OperatorStatus>(await handleResponse<unknown>(response))
}

export async function createOperatorPreflight(
  mode: OperatorMode,
  commandId: string,
  uploadRequested = false,
): Promise<PreflightResult> {
  const response = await fetch(`${API_BASE}/preflights`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(await mutationHeaders()) },
    body: JSON.stringify({
      command_id: commandId,
      profile: 'so101',
      mode,
      upload_requested: uploadRequested,
    }),
  })
  return transformKeys<PreflightResult>(await handleResponse<unknown>(response))
}
