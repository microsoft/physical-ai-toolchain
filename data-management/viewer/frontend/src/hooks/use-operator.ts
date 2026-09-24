import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'

import {
  createOperatorPreflight,
  fetchOperatorCalibration,
  fetchOperatorCapabilities,
  fetchOperatorStatus,
  sendOperatorCommand,
  startOperatorSession,
  stopOperatorSession,
  streamOperatorEvents,
} from '@/api/operator'
import type {
  CreateOperatorPreflightRequest,
  OperatorAction,
  OperatorCalibrationReport,
  OperatorCapabilities,
  OperatorStatus,
  OperatorTelemetry,
  PreflightResult,
  StartOperatorSessionRequest,
} from '@/types'

export const MAX_OPERATOR_TELEMETRY_SAMPLES = 180

export const operatorKeys = {
  all: ['operator'] as const,
  capabilities: () => [...operatorKeys.all, 'capabilities'] as const,
  calibration: () => [...operatorKeys.all, 'calibration'] as const,
  status: () => [...operatorKeys.all, 'status'] as const,
}

export interface OperatorEventState {
  telemetry: OperatorTelemetry[]
  lastEventId: string | null
}

export interface OperatorController {
  capabilities: OperatorCapabilities | undefined
  status: OperatorStatus | undefined
  calibration: OperatorCalibrationReport | undefined
  preflight: PreflightResult | undefined
  telemetry: OperatorTelemetry[]
  connectionState: 'disabled' | 'connecting' | 'connected' | 'retrying'
  isLoading: boolean
  isPending: boolean
  error: string | null
  calibrationError: string | null
  checkCalibration: () => void
  runPreflight: (request: Omit<CreateOperatorPreflightRequest, 'commandId'>) => void
  startSession: (request: Omit<StartOperatorSessionRequest, 'commandId'>) => void
  sendCommand: (action: OperatorAction) => void
  stopSession: () => void
}

export interface UseOperatorOptions {
  reconnectDelayMs?: number
}

interface ReconciledOperatorEvent {
  status: OperatorStatus
  eventState: OperatorEventState
}

const EMPTY_EVENT_STATE: OperatorEventState = { telemetry: [], lastEventId: null }

function isMatchingIdentity(current: OperatorStatus, incoming: OperatorStatus): boolean {
  if (current.serviceInstanceId !== incoming.serviceInstanceId) return false
  if (current.sessionId !== incoming.sessionId) {
    return ['disabled', 'idle', 'stopped', 'completed', 'cancelled', 'failed'].includes(
      current.state,
    )
  }
  return !current.workerPid || !incoming.workerPid || current.workerPid === incoming.workerPid
}

export function reconcileOperatorEvent(
  current: OperatorStatus,
  incoming: OperatorStatus,
): OperatorStatus
export function reconcileOperatorEvent(
  current: OperatorStatus,
  incoming: OperatorStatus,
  eventState: OperatorEventState,
  eventId: string,
): ReconciledOperatorEvent
export function reconcileOperatorEvent(
  current: OperatorStatus,
  incoming: OperatorStatus,
  eventState?: OperatorEventState,
  eventId?: string,
): OperatorStatus | ReconciledOperatorEvent {
  const identityMatches = isMatchingIdentity(current, incoming)
  const accepted = identityMatches && incoming.revision > current.revision
  const status = accepted ? incoming : current
  if (!eventState || !eventId) return status
  if (!identityMatches || incoming.revision < current.revision) return { status, eventState }

  const telemetry = incoming.latestTelemetry
  const isDuplicate =
    telemetry && eventState.telemetry.at(-1)?.elapsedS === incoming.latestTelemetry?.elapsedS
  return {
    status,
    eventState: {
      telemetry:
        telemetry && !isDuplicate
          ? [...eventState.telemetry, telemetry].slice(-MAX_OPERATOR_TELEMETRY_SAMPLES)
          : eventState.telemetry,
      lastEventId: eventId,
    },
  }
}

function createCommandId(): string {
  return globalThis.crypto.randomUUID()
}

function errorMessage(error: unknown): string | null {
  return error instanceof Error ? error.message : null
}

export function useOperator(options: UseOperatorOptions = {}): OperatorController {
  const { reconnectDelayMs = 1_000 } = options
  const queryClient = useQueryClient()
  const [connectionState, setConnectionState] =
    useState<OperatorController['connectionState']>('connecting')
  const [streamError, setStreamError] = useState<string | null>(null)
  const [eventState, setEventState] = useState<OperatorEventState>(EMPTY_EVENT_STATE)
  const eventStateRef = useRef<OperatorEventState>(EMPTY_EVENT_STATE)

  const capabilitiesQuery = useQuery({
    queryKey: operatorKeys.capabilities(),
    queryFn: fetchOperatorCapabilities,
    staleTime: 30_000,
  })
  const statusQuery = useQuery({
    queryKey: operatorKeys.status(),
    queryFn: fetchOperatorStatus,
    refetchOnReconnect: true,
    refetchOnWindowFocus: true,
  })
  const calibrationQuery = useQuery({
    queryKey: operatorKeys.calibration(),
    queryFn: fetchOperatorCalibration,
    enabled: false,
    staleTime: 0,
  })
  const statusReady = statusQuery.data !== undefined

  const updateStatus = (incoming: OperatorStatus) => {
    queryClient.setQueryData<OperatorStatus>(operatorKeys.status(), incoming)
  }
  const preflightMutation = useMutation({
    mutationFn: createOperatorPreflight,
    retry: 1,
    retryDelay: 0,
  })
  const startMutation = useMutation({
    mutationFn: startOperatorSession,
    retry: 1,
    retryDelay: 0,
    onSuccess: updateStatus,
  })
  const commandMutation = useMutation({
    mutationFn: ({
      sessionId,
      action,
      commandId,
      expectedRevision,
    }: {
      sessionId: string
      action: OperatorAction
      commandId: string
      expectedRevision: number
    }) => sendOperatorCommand(sessionId, { action, commandId, expectedRevision }),
    retry: 1,
    retryDelay: 0,
    onSuccess: updateStatus,
  })
  const stopMutation = useMutation({
    mutationFn: ({ sessionId, commandId }: { sessionId: string; commandId: string }) =>
      stopOperatorSession(sessionId, commandId),
    retry: 1,
    retryDelay: 0,
    onSuccess: updateStatus,
  })

  useEffect(() => {
    if (!capabilitiesQuery.data?.enabled || !statusReady) return

    const controller = new AbortController()
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined

    const connect = async (): Promise<void> => {
      try {
        setConnectionState('connecting')
        await streamOperatorEvents(
          eventStateRef.current.lastEventId,
          (event) => {
            const current = queryClient.getQueryData<OperatorStatus>(operatorKeys.status())
            if (!current) return
            const reconciled = reconcileOperatorEvent(
              current,
              event.status,
              eventStateRef.current,
              event.eventId,
            )
            eventStateRef.current = reconciled.eventState
            setEventState(reconciled.eventState)
            setConnectionState('connected')
            setStreamError(null)
            if (reconciled.status !== current) {
              queryClient.setQueryData(operatorKeys.status(), reconciled.status)
            }
          },
          controller.signal,
        )
        if (!controller.signal.aborted) throw new Error('Operator event stream disconnected')
      } catch (error) {
        if (controller.signal.aborted) return
        setConnectionState('retrying')
        setStreamError(errorMessage(error) ?? 'Operator event stream disconnected')

        try {
          const authoritative = await fetchOperatorStatus()
          const previous = queryClient.getQueryData<OperatorStatus>(operatorKeys.status())
          queryClient.setQueryData(operatorKeys.status(), authoritative)
          if (!previous || !isMatchingIdentity(previous, authoritative)) {
            eventStateRef.current = EMPTY_EVENT_STATE
            setEventState(EMPTY_EVENT_STATE)
          }
        } catch (statusError) {
          setStreamError(errorMessage(statusError) ?? 'Operator status reconciliation failed')
        }

        if (!controller.signal.aborted) {
          reconnectTimer = setTimeout(() => void connect(), reconnectDelayMs)
        }
      }
    }

    void connect()
    return () => {
      controller.abort()
      if (reconnectTimer) clearTimeout(reconnectTimer)
    }
  }, [capabilitiesQuery.data?.enabled, queryClient, reconnectDelayMs, statusReady])

  const runPreflight = (request: Omit<CreateOperatorPreflightRequest, 'commandId'>) => {
    preflightMutation.mutate({ ...request, commandId: createCommandId() })
  }
  const startSession = (request: Omit<StartOperatorSessionRequest, 'commandId'>) => {
    startMutation.mutate({ ...request, commandId: createCommandId() })
  }
  const sendCommand = (action: OperatorAction) => {
    const status = queryClient.getQueryData<OperatorStatus>(operatorKeys.status())
    if (!status?.sessionId) return
    commandMutation.mutate({
      sessionId: status.sessionId,
      action,
      commandId: createCommandId(),
      expectedRevision: status.revision,
    })
  }
  const stopSession = () => {
    const status = queryClient.getQueryData<OperatorStatus>(operatorKeys.status())
    if (!status?.sessionId) return
    stopMutation.mutate({ sessionId: status.sessionId, commandId: createCommandId() })
  }

  const error =
    errorMessage(startMutation.error) ??
    errorMessage(commandMutation.error) ??
    errorMessage(stopMutation.error) ??
    errorMessage(preflightMutation.error) ??
    errorMessage(capabilitiesQuery.error) ??
    errorMessage(statusQuery.error) ??
    streamError

  return {
    capabilities: capabilitiesQuery.data,
    status: statusQuery.data,
    calibration: calibrationQuery.data,
    preflight: preflightMutation.data,
    telemetry: eventState.telemetry,
    connectionState: capabilitiesQuery.data?.enabled ? connectionState : 'disabled',
    isLoading: capabilitiesQuery.isLoading || statusQuery.isLoading,
    isPending:
      preflightMutation.isPending ||
      startMutation.isPending ||
      commandMutation.isPending ||
      stopMutation.isPending,
    error,
    calibrationError: errorMessage(calibrationQuery.error),
    checkCalibration: () => void calibrationQuery.refetch(),
    runPreflight,
    startSession,
    sendCommand,
    stopSession,
  }
}
