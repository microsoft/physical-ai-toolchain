import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'

import {
  createOperatorPreflight,
  fetchOperatorCapabilities,
  fetchOperatorStatus,
  type OperatorAction,
  type OperatorCapabilities,
  type OperatorMode,
  type OperatorSessionSettings,
  type OperatorStatus,
  type OperatorTelemetry,
  type PreflightResult,
  sendOperatorCommand,
  startOperatorSession,
  stopOperatorSession,
  streamOperatorEvents,
} from '@/api/operator'
import { recordDiagnosticEvent } from '@/lib/playback-diagnostics'

const operatorKeys = {
  all: ['operator'] as const,
  capabilities: () => [...operatorKeys.all, 'capabilities'] as const,
  status: () => [...operatorKeys.all, 'status'] as const,
}

export interface OperatorController {
  capabilities: OperatorCapabilities | undefined
  status: OperatorStatus | undefined
  isLoading: boolean
  isPending: boolean
  error: string | null
  connectionState: 'disabled' | 'connecting' | 'connected' | 'retrying'
  preflight: PreflightResult | undefined
  telemetry: OperatorTelemetry[]
  runPreflight: (mode: OperatorMode, uploadRequested?: boolean) => void
  startSession: (mode: OperatorMode, settings?: OperatorSessionSettings) => void
  sendCommand: (action: OperatorAction) => void
  stopSession: () => void
}

function createCommandId(): string {
  return globalThis.crypto.randomUUID()
}

export function selectNewerOperatorStatus(
  current: OperatorStatus | undefined,
  incoming: OperatorStatus,
): OperatorStatus {
  if (!current || current.serviceInstanceId !== incoming.serviceInstanceId) return incoming
  return incoming.revision > current.revision ? incoming : current
}

function errorMessage(error: unknown): string | null {
  return error instanceof Error ? error.message : null
}

export function useOperator(): OperatorController {
  const queryClient = useQueryClient()
  const lastEventIdRef = useRef<string | null>(null)
  const startCommandRef = useRef<{
    mode: OperatorMode
    commandId: string
    preflight: PreflightResult | undefined
    settings: OperatorSessionSettings | undefined
  } | null>(null)
  const [connectionState, setConnectionState] =
    useState<OperatorController['connectionState']>('connecting')
  const [streamError, setStreamError] = useState<string | null>(null)
  const [telemetry, setTelemetry] = useState<OperatorTelemetry[]>([])
  const capabilitiesQuery = useQuery({
    queryKey: operatorKeys.capabilities(),
    queryFn: fetchOperatorCapabilities,
    staleTime: 30_000,
  })
  const statusQuery = useQuery<OperatorStatus>({
    queryKey: operatorKeys.status(),
    queryFn: fetchOperatorStatus,
    structuralSharing: (current, incoming) =>
      selectNewerOperatorStatus(current as OperatorStatus | undefined, incoming as OperatorStatus),
    refetchInterval: capabilitiesQuery.data?.enabled ? false : 10_000,
    refetchOnWindowFocus: true,
    refetchOnReconnect: true,
  })

  const updateStatus = (status: OperatorStatus) => {
    queryClient.setQueryData<OperatorStatus>(operatorKeys.status(), (current) =>
      selectNewerOperatorStatus(current, status),
    )
  }
  const startMutation = useMutation({
    mutationFn: ({
      mode,
      commandId,
      preflight,
      settings,
    }: {
      mode: OperatorMode
      commandId: string
      preflight: PreflightResult | undefined
      settings: OperatorSessionSettings | undefined
    }) => startOperatorSession(mode, commandId, preflight, settings),
    retry: 1,
    retryDelay: 0,
    onSuccess: (status) => {
      startCommandRef.current = null
      preflightMutation.reset()
      updateStatus(status)
    },
  })
  const commandMutation = useMutation({
    mutationFn: ({
      status,
      action,
      commandId,
    }: {
      status: OperatorStatus
      action: OperatorAction
      commandId: string
    }) => sendOperatorCommand(status, action, commandId),
    retry: 1,
    retryDelay: 0,
    onSuccess: updateStatus,
  })
  const stopMutation = useMutation({
    mutationFn: ({ status, commandId }: { status: OperatorStatus; commandId: string }) =>
      stopOperatorSession(status, commandId),
    retry: 1,
    retryDelay: 0,
    onSuccess: updateStatus,
  })
  const preflightMutation = useMutation({
    mutationFn: ({
      mode,
      commandId,
      uploadRequested,
    }: {
      mode: OperatorMode
      commandId: string
      uploadRequested: boolean
    }) => createOperatorPreflight(mode, commandId, uploadRequested),
    onSuccess: () => {
      startCommandRef.current = null
    },
  })

  useEffect(() => {
    if (!capabilitiesQuery.data?.enabled) return
    const controller = new AbortController()
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined

    const connect = async () => {
      let disconnectMessage = 'Operator event stream disconnected'
      try {
        await streamOperatorEvents(
          lastEventIdRef.current,
          (event) => {
            const current = queryClient.getQueryData<OperatorStatus>(operatorKeys.status())
            const selected = selectNewerOperatorStatus(current, event.status)
            if (selected !== event.status) return
            lastEventIdRef.current = event.eventId
            queryClient.setQueryData(operatorKeys.status(), selected)
            if (selected.latestTelemetry) {
              setTelemetry((current) => {
                if (current.at(-1)?.elapsedS === selected.latestTelemetry?.elapsedS) {
                  return current
                }
                return [...current.slice(-179), selected.latestTelemetry!]
              })
            }
            setConnectionState('connected')
            setStreamError(null)
            recordDiagnosticEvent('operator', 'status', {
              state: selected.state,
              revision: selected.revision,
              sessionId: selected.sessionId,
            })
          },
          controller.signal,
        )
      } catch (error) {
        disconnectMessage = errorMessage(error) ?? 'Unknown stream error'
      }
      if (!controller.signal.aborted) {
        setConnectionState('retrying')
        setStreamError(disconnectMessage)
        lastEventIdRef.current = null
        recordDiagnosticEvent('operator', 'stream-error', {
          message: disconnectMessage,
        })
        await queryClient.invalidateQueries({ queryKey: operatorKeys.status() })
      }
      if (!controller.signal.aborted) reconnectTimer = setTimeout(() => void connect(), 1_000)
    }

    void connect()
    return () => {
      controller.abort()
      if (reconnectTimer) clearTimeout(reconnectTimer)
    }
  }, [capabilitiesQuery.data?.enabled, queryClient])

  const startSession = (mode: OperatorMode, settings?: OperatorSessionSettings) => {
    const previous = startCommandRef.current
    const command =
      previous?.mode === mode
        ? previous
        : { mode, commandId: createCommandId(), preflight: preflightMutation.data, settings }
    startCommandRef.current = command
    startMutation.mutate(command)
  }
  const sendCommand = (action: OperatorAction) => {
    const status = queryClient.getQueryData<OperatorStatus>(operatorKeys.status())
    if (!status?.sessionId) return
    commandMutation.mutate({ status, action, commandId: createCommandId() })
  }
  const stopSession = () => {
    const status = queryClient.getQueryData<OperatorStatus>(operatorKeys.status())
    if (!status?.sessionId) return
    stopMutation.mutate({ status, commandId: createCommandId() })
  }
  const runPreflight = (mode: OperatorMode, uploadRequested = false) => {
    preflightMutation.mutate({ mode, commandId: createCommandId(), uploadRequested })
  }

  const error =
    errorMessage(startMutation.error) ??
    errorMessage(commandMutation.error) ??
    errorMessage(stopMutation.error) ??
    errorMessage(capabilitiesQuery.error) ??
    errorMessage(statusQuery.error) ??
    errorMessage(preflightMutation.error) ??
    streamError
  const effectiveConnectionState = capabilitiesQuery.data?.enabled ? connectionState : 'disabled'

  return {
    capabilities: capabilitiesQuery.data,
    status: statusQuery.data,
    isLoading: capabilitiesQuery.isLoading || statusQuery.isLoading,
    isPending:
      startMutation.isPending ||
      commandMutation.isPending ||
      stopMutation.isPending ||
      preflightMutation.isPending,
    error,
    connectionState: effectiveConnectionState,
    preflight: preflightMutation.data,
    telemetry,
    runPreflight,
    startSession,
    sendCommand,
    stopSession,
  }
}
