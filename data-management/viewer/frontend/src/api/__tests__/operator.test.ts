import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  createOperatorPreflight,
  fetchOperatorCameraFrame,
  fetchOperatorCapabilities,
  parseOperatorEventBlock,
  sendOperatorCommand,
  startOperatorSession,
} from '@/api/operator'
import { _resetCsrfToken, ApiClientError } from '@/lib/api-client'
import {
  installFetchMock,
  jsonResponse,
  mockFetch,
  mockMutationFetch,
} from '@/test-utils/fetch-mocks'
import type { OperatorStatus } from '@/types'

const statusPayload = {
  service_instance_id: 'service-1',
  revision: 4,
  state: 'running',
  session_id: 'session-1',
  mode: 'record',
  worker_pid: 42,
  last_command: null,
  cleanup_unconfirmed: false,
  error: null,
  session_settings: {
    control_fps: 30,
    camera_fps: { wrist_cam: 15 },
    max_relative_target: null,
    dataset_root: null,
    dataset_id: 'plate_grasping',
    repo_id: null,
    task: 'Pick up the plate',
    save_destination: 'local',
    hub_repo_id: null,
    num_episodes: 2,
    episode_time_s: 60,
    reset_time_s: 30,
    rollout_time_s: 30,
    policy_python: null,
    policy_checkpoint: null,
    policy_cuda_visible_devices: null,
  },
  latest_telemetry: {
    elapsed_s: 1.5,
    leader: { shoulder_pan: 1 },
    follower: { shoulder_pan: 2 },
    commanded: { shoulder_pan: 3 },
  },
  dataset_id: 'plate_grasping',
  episode_index: 1,
  recording_phase: 'recording',
  upload_status: 'not_requested',
  upload_error: null,
}

beforeEach(() => {
  installFetchMock({ csrf: false })
  _resetCsrfToken()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('operator API', () => {
  it('uses the shared transport and preserves domain map keys in transformed responses', async () => {
    mockFetch.mockResolvedValueOnce(
      jsonResponse({
        enabled: true,
        adapter_mode: 'simulated',
        adapter_version: 1,
        protocol_version: 1,
        modes: ['record'],
        profiles: ['so101'],
        robots: [],
        cameras: [{ name: 'wrist_cam', default_fps: 15 }],
        preflight_enabled: true,
        session_start_enabled: true,
        reason: null,
      }),
    )

    await expect(fetchOperatorCapabilities()).resolves.toMatchObject({
      adapterMode: 'simulated',
      cameras: [{ name: 'wrist_cam', defaultFps: 15 }],
    })
    expect(mockFetch).toHaveBeenCalledWith('/api/operator/capabilities', { headers: {} })

    mockFetch.mockResolvedValueOnce(jsonResponse(statusPayload))
    const event = parseOperatorEventBlock(
      `id: service-1:4\nevent: status\ndata: ${JSON.stringify(statusPayload)}\n`,
    )
    expect(event?.status.sessionSettings?.cameraFps).toEqual({ wrist_cam: 15 })
    expect(event?.status.latestTelemetry?.leader).toEqual({ shoulder_pan: 1 })
  })

  it('serializes current backend snake_case contracts and reuses caller command identities', async () => {
    mockMutationFetch(jsonResponse(statusPayload, 201))

    await startOperatorSession({
      commandId: 'start-1',
      mode: 'record',
      profile: 'so101',
      preflightId: 'preflight-1',
      preflightFingerprint: 'fingerprint-1',
      settings: {
        controlFps: 30,
        cameraFps: { wrist_cam: 15 },
        maxRelativeTarget: null,
        datasetRoot: null,
        datasetId: 'plate_grasping',
        repoId: null,
        task: 'Pick up the plate',
        saveDestination: 'local',
        hubRepoId: null,
        numEpisodes: 2,
        episodeTimeS: 60,
        resetTimeS: 30,
        rolloutTimeS: 30,
        policyPython: null,
        policyCheckpoint: null,
        policyCudaVisibleDevices: null,
      },
    })

    expect(JSON.parse(String(mockFetch.mock.calls[1][1]?.body))).toEqual({
      command_id: 'start-1',
      mode: 'record',
      profile: 'so101',
      preflight_id: 'preflight-1',
      preflight_fingerprint: 'fingerprint-1',
      settings: {
        control_fps: 30,
        camera_fps: { wrist_cam: 15 },
        max_relative_target: null,
        dataset_root: null,
        dataset_id: 'plate_grasping',
        repo_id: null,
        task: 'Pick up the plate',
        save_destination: 'local',
        hub_repo_id: null,
        num_episodes: 2,
        episode_time_s: 60,
        reset_time_s: 30,
        rollout_time_s: 30,
        policy_python: null,
        policy_checkpoint: null,
        policy_cuda_visible_devices: null,
      },
    })

    mockMutationFetch(jsonResponse(statusPayload))
    await sendOperatorCommand('session-1', {
      commandId: 'command-1',
      action: 'pause',
      expectedRevision: 4,
    })
    expect(JSON.parse(String(mockFetch.mock.calls.at(-1)?.[1]?.body))).toEqual({
      command_id: 'command-1',
      action: 'pause',
      expected_revision: 4,
    })
  })

  it('supports typed preflight and frame contracts while retaining public API errors', async () => {
    mockMutationFetch(
      jsonResponse({
        preflight_id: 'preflight-1',
        lifecycle: 'completed',
        profile: 'so101',
        mode: 'record',
        profile_fingerprint: 'profile-fingerprint',
        resource_fingerprint: 'resource-fingerprint',
        created_at: '2026-09-24T10:00:00Z',
        expires_at: '2026-09-24T10:05:00Z',
        checks: [],
        ownership_complete: true,
        start_eligible: true,
      }),
    )
    await expect(
      createOperatorPreflight({
        commandId: 'preflight-command-1',
        profile: 'so101',
        mode: 'record',
        uploadRequested: false,
      }),
    ).resolves.toMatchObject({ preflightId: 'preflight-1', startEligible: true })

    const jpeg = new Blob(['jpeg'], { type: 'image/jpeg' })
    mockFetch.mockResolvedValueOnce(
      new Response(jpeg, {
        headers: { 'X-Operator-Captured-At': '12.5', 'Content-Type': 'image/jpeg' },
      }),
    )
    await expect(
      fetchOperatorCameraFrame('wrist/cam', new AbortController().signal),
    ).resolves.toEqual({ blob: jpeg, capturedAtS: 12.5 })
    expect(mockFetch.mock.calls[2][0]).toBe('/api/operator/cameras/wrist%2Fcam/frame')

    mockFetch.mockResolvedValueOnce(jsonResponse({ detail: '/dev/video0' }, 500))
    await expect(fetchOperatorCapabilities()).rejects.toBeInstanceOf(ApiClientError)
  })
})

const _statusContract: OperatorStatus = {
  serviceInstanceId: 'service-1',
  revision: 0,
  state: 'stopped',
  sessionId: '',
  mode: null,
  workerPid: null,
  lastCommand: null,
  cleanupUnconfirmed: false,
  error: null,
  targetHz: null,
  actualHz: null,
  loopP95Ms: null,
  loopMaxMs: null,
  overruns: 0,
  latestWorkerLog: null,
  latestTelemetry: null,
  sessionSettings: null,
  datasetId: null,
  episodeIndex: 0,
  recordingPhase: null,
  uploadStatus: 'not_requested',
  uploadError: null,
}

void _statusContract
