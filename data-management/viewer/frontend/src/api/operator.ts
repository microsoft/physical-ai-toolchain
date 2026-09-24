import { apiFetch, apiRequest, handleResponse, transformKeys } from '@/lib/api-client'
import type {
  CreateOperatorPreflightRequest,
  OperatorCalibrationReport,
  OperatorCameraFrame,
  OperatorCapabilities,
  OperatorSessionSettings,
  OperatorStatus,
  ParsedOperatorEvent,
  PreflightResult,
  SendOperatorCommandRequest,
  StartOperatorSessionRequest,
} from '@/types'

const OPERATOR_PATH = '/operator'
const MAX_EVENT_BLOCK_BYTES = 256 * 1024

type JsonMap = Record<string, unknown>

function jsonBody(value: unknown): RequestInit {
  return {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(value),
  }
}

function serializeSettings(settings: OperatorSessionSettings): JsonMap {
  return {
    control_fps: settings.controlFps,
    camera_fps: settings.cameraFps,
    max_relative_target: settings.maxRelativeTarget,
    dataset_root: settings.datasetRoot,
    dataset_id: settings.datasetId,
    repo_id: settings.repoId,
    task: settings.task,
    save_destination: settings.saveDestination,
    hub_repo_id: settings.hubRepoId,
    num_episodes: settings.numEpisodes,
    episode_time_s: settings.episodeTimeS,
    reset_time_s: settings.resetTimeS,
    rollout_time_s: settings.rolloutTimeS,
    policy_python: settings.policyPython,
    policy_checkpoint: settings.policyCheckpoint,
    policy_cuda_visible_devices: settings.policyCudaVisibleDevices,
  }
}

function transformOperatorStatus(data: unknown): OperatorStatus {
  const raw = data as JsonMap
  const result = transformKeys<OperatorStatus>(raw)
  const rawSettings = raw.session_settings as JsonMap | null | undefined
  const rawTelemetry = raw.latest_telemetry as JsonMap | null | undefined

  if (result.sessionSettings && rawSettings?.camera_fps) {
    result.sessionSettings.cameraFps = { ...(rawSettings.camera_fps as Record<string, number>) }
  }
  if (result.latestTelemetry && rawTelemetry) {
    result.latestTelemetry = {
      ...result.latestTelemetry,
      leader: { ...(rawTelemetry.leader as Record<string, number>) },
      follower: { ...(rawTelemetry.follower as Record<string, number>) },
      commanded: { ...(rawTelemetry.commanded as Record<string, number>) },
    }
  }
  return result
}

export function fetchOperatorCapabilities(): Promise<OperatorCapabilities> {
  return apiRequest<OperatorCapabilities>(`${OPERATOR_PATH}/capabilities`)
}

export function fetchOperatorStatus(): Promise<OperatorStatus> {
  return apiRequest(`${OPERATOR_PATH}/status`, {}, transformOperatorStatus)
}

export function fetchOperatorCalibration(): Promise<OperatorCalibrationReport> {
  return apiRequest<OperatorCalibrationReport>(`${OPERATOR_PATH}/calibration`, {
    cache: 'no-store',
  })
}

export async function fetchOperatorCameraFrame(
  camera: string,
  signal: AbortSignal,
): Promise<OperatorCameraFrame> {
  const response = await apiFetch(`${OPERATOR_PATH}/cameras/${encodeURIComponent(camera)}/frame`, {
    cache: 'no-store',
    signal,
  })
  if (!response.ok) await handleResponse<never>(response)

  const capturedAtS = Number(response.headers.get('X-Operator-Captured-At'))
  if (!Number.isFinite(capturedAtS)) throw new Error('Operator camera timestamp is unavailable')
  return { blob: await response.blob(), capturedAtS }
}

export function createOperatorPreflight(
  request: CreateOperatorPreflightRequest,
): Promise<PreflightResult> {
  return apiRequest<PreflightResult>(
    `${OPERATOR_PATH}/preflights`,
    jsonBody({
      command_id: request.commandId,
      profile: request.profile,
      mode: request.mode,
      upload_requested: request.uploadRequested ?? false,
    }),
  )
}

export function fetchOperatorPreflight(preflightId: string): Promise<PreflightResult> {
  return apiRequest<PreflightResult>(
    `${OPERATOR_PATH}/preflights/${encodeURIComponent(preflightId)}`,
  )
}

export function cancelOperatorPreflight(
  preflightId: string,
  commandId: string,
): Promise<PreflightResult> {
  const params = new URLSearchParams({ command_id: commandId })
  return apiRequest<PreflightResult>(
    `${OPERATOR_PATH}/preflights/${encodeURIComponent(preflightId)}?${params}`,
    { method: 'DELETE' },
  )
}

export function startOperatorSession(
  request: StartOperatorSessionRequest,
): Promise<OperatorStatus> {
  return apiRequest(
    `${OPERATOR_PATH}/sessions`,
    jsonBody({
      command_id: request.commandId,
      mode: request.mode,
      profile: request.profile ?? null,
      preflight_id: request.preflightId ?? null,
      preflight_fingerprint: request.preflightFingerprint ?? null,
      settings: request.settings ? serializeSettings(request.settings) : null,
    }),
    transformOperatorStatus,
  )
}

export function sendOperatorCommand(
  sessionId: string,
  request: SendOperatorCommandRequest,
): Promise<OperatorStatus> {
  return apiRequest(
    `${OPERATOR_PATH}/sessions/${encodeURIComponent(sessionId)}/commands`,
    jsonBody({
      command_id: request.commandId,
      action: request.action,
      expected_revision: request.expectedRevision ?? null,
    }),
    transformOperatorStatus,
  )
}

export function stopOperatorSession(sessionId: string, commandId: string): Promise<OperatorStatus> {
  const params = new URLSearchParams({ command_id: commandId })
  return apiRequest(
    `${OPERATOR_PATH}/sessions/${encodeURIComponent(sessionId)}?${params}`,
    { method: 'DELETE' },
    transformOperatorStatus,
  )
}

export function parseOperatorEventBlock(block: string): ParsedOperatorEvent | null {
  if (new TextEncoder().encode(block).byteLength > MAX_EVENT_BLOCK_BYTES) return null

  let eventId = ''
  let eventType = ''
  const data: string[] = []
  for (const line of block.split('\n')) {
    if (line.startsWith('id:')) eventId = line.slice(3).trim()
    if (line.startsWith('event:')) eventType = line.slice(6).trim()
    if (line.startsWith('data:')) data.push(line.slice(5).trimStart())
  }
  if (!eventId || data.length === 0 || !['snapshot', 'status'].includes(eventType)) return null

  try {
    const status = transformOperatorStatus(JSON.parse(data.join('\n')))
    return eventId === `${status.serviceInstanceId}:${status.revision}` ? { eventId, status } : null
  } catch {
    return null
  }
}

export async function streamOperatorEvents(
  lastEventId: string | null,
  onEvent: (event: ParsedOperatorEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  const headers: Record<string, string> = {}
  if (lastEventId) headers['Last-Event-ID'] = lastEventId
  const response = await apiFetch(`${OPERATOR_PATH}/events`, { headers, signal })
  if (!response.ok) await handleResponse<never>(response)
  if (!response.body) throw new Error('Operator event stream is unavailable')

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (!signal.aborted) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true }).replaceAll('\r\n', '\n')
    if (new TextEncoder().encode(buffer).byteLength > MAX_EVENT_BLOCK_BYTES) {
      await reader.cancel()
      throw new Error('Operator event exceeds the allowed size')
    }

    let boundary = buffer.indexOf('\n\n')
    while (boundary >= 0) {
      const event = parseOperatorEventBlock(buffer.slice(0, boundary))
      buffer = buffer.slice(boundary + 2)
      if (event) onEvent(event)
      boundary = buffer.indexOf('\n\n')
    }
  }
}
