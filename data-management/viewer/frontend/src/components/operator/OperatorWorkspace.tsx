import { CirclePlay, CircleStop, Pause, RotateCcw, Save, ScanSearch, Square } from 'lucide-react'
import { useEffect, useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import type { OperatorController } from '@/hooks/use-operator'
import type { OperatorMode, OperatorSessionSettings } from '@/types'

import { redactOperatorText } from './operator-display'
import { OperatorCalibrationPanel } from './OperatorCalibrationPanel'
import { OperatorCameraPreview } from './OperatorCameraPreview'
import { OperatorMonitoring } from './OperatorMonitoring'
import { OperatorSessionConfig } from './OperatorSessionConfig'

interface OperatorWorkspaceProps {
  operator: OperatorController
}

const STARTABLE_STATES = ['idle', 'stopped', 'completed', 'cancelled', 'failed']
const STOPPABLE_STATES = ['starting', 'running', 'stopping', 'failed']

function initialSettings(): OperatorSessionSettings {
  return {
    controlFps: 60,
    cameraFps: {},
    maxRelativeTarget: null,
    datasetRoot: null,
    datasetId: 'operator-session',
    repoId: null,
    task: '',
    saveDestination: 'local',
    hubRepoId: null,
    numEpisodes: 10,
    episodeTimeS: 60,
    resetTimeS: 15,
    rolloutTimeS: 30,
    policyPython: null,
    policyCheckpoint: null,
    policyCudaVisibleDevices: null,
  }
}

function stateTone(state: string | undefined) {
  if (state === 'running') return 'success' as const
  if (state === 'failed') return 'danger' as const
  if (state === 'starting' || state === 'stopping') return 'warning' as const
  return 'neutral' as const
}

export function OperatorWorkspace({ operator }: OperatorWorkspaceProps) {
  const [mode, setMode] = useState<OperatorMode>('teleoperate')
  const [settings, setSettings] = useState(initialSettings)
  const [currentTime, setCurrentTime] = useState(Date.now)
  const { capabilities, status } = operator

  useEffect(() => {
    const timer = setInterval(() => setCurrentTime(Date.now()), 1_000)
    return () => clearInterval(timer)
  }, [])

  if (operator.isLoading) {
    return (
      <div className="space-y-4 p-6" role="status">
        <span className="sr-only">Loading operator workspace</span>
        <Skeleton className="h-8 w-52" />
        <Skeleton className="h-72 w-full" />
      </div>
    )
  }

  if (!capabilities?.enabled) {
    return (
      <section className="space-y-2 p-6" aria-labelledby="operator-unavailable-heading">
        <h2 id="operator-unavailable-heading" className="text-lg font-semibold">
          Operator unavailable
        </h2>
        <p className="text-muted-foreground text-sm">
          {redactOperatorText(capabilities?.reason ?? 'Operator capabilities are unavailable')}
        </p>
      </section>
    )
  }

  const selectedProfile = capabilities.profiles[0] ?? null
  const physicalOperation = capabilities.adapterMode === 'lerobot'
  const profileReady = !physicalOperation || selectedProfile !== null
  const calibrationReady = !physicalOperation || Boolean(operator.calibration?.valid)
  const preflightCurrent =
    !capabilities.preflightEnabled ||
    (operator.preflight?.lifecycle === 'completed' &&
      operator.preflight.mode === mode &&
      operator.preflight.profile === selectedProfile &&
      operator.preflight.startEligible &&
      Date.parse(operator.preflight.expiresAt) > currentTime)
  const canStart =
    capabilities.sessionStartEnabled &&
    capabilities.modes.includes(mode) &&
    profileReady &&
    calibrationReady &&
    preflightCurrent &&
    !status?.cleanupUnconfirmed &&
    STARTABLE_STATES.includes(status?.state ?? '') &&
    !operator.isPending
  const canStop =
    Boolean(status?.sessionId) &&
    (STOPPABLE_STATES.includes(status?.state ?? '') || status?.cleanupUnconfirmed === true)
  const previewActive = status?.state === 'running' && physicalOperation
  const stateLabel = status?.cleanupUnconfirmed
    ? 'Cleanup unconfirmed'
    : operator.isPending
      ? 'Pending command'
      : status?.state === 'idle'
        ? 'Idle'
        : (status?.state ?? 'Unavailable')

  const selectMode = (nextMode: OperatorMode) => {
    setMode(nextMode)
    setSettings((current) => ({
      ...current,
      controlFps: nextMode === 'teleoperate' ? 60 : 30,
      maxRelativeTarget: nextMode === 'policy' ? 2 : null,
    }))
  }

  const start = () => {
    if (!canStart) return
    operator.startSession({
      mode,
      profile: selectedProfile,
      preflightId: operator.preflight?.preflightId ?? null,
      preflightFingerprint: operator.preflight?.resourceFingerprint ?? null,
      settings,
    })
  }

  return (
    <div className="bg-background flex h-full min-h-0 flex-col overflow-auto">
      <header className="flex flex-wrap items-start justify-between gap-3 border-b px-4 py-3 sm:px-6">
        <div>
          <h2 className="text-lg font-semibold">Live operator</h2>
          <p className="text-muted-foreground text-sm">
            {physicalOperation ? 'Physical operation' : 'Simulation'}
          </p>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-2">
          <Badge
            variant="status"
            tone={status?.cleanupUnconfirmed ? 'danger' : stateTone(status?.state)}
          >
            {stateLabel}
          </Badge>
          {operator.connectionState === 'retrying' && (
            <Badge variant="status" tone="warning">
              Reconnecting
            </Badge>
          )}
          {canStop && (
            <Button type="button" variant="destructive" size="sm" onClick={operator.stopSession}>
              <CircleStop className="h-4 w-4" aria-hidden="true" />
              Stop session
            </Button>
          )}
        </div>
      </header>

      {!physicalOperation && (
        <p className="border-b px-4 py-2 text-sm sm:px-6">
          Simulation exercises the session workflow and does not move physical hardware.
        </p>
      )}
      {(operator.error || status?.error || status?.cleanupUnconfirmed) && (
        <div
          className="border-destructive/40 text-destructive border-b px-4 py-2 text-sm sm:px-6"
          role="alert"
        >
          {status?.cleanupUnconfirmed
            ? 'Worker cleanup was not confirmed. Verify hardware ownership before reconnecting.'
            : redactOperatorText(status?.error ?? operator.error ?? 'Operator command failed')}
        </div>
      )}

      <div className="grid min-h-0 flex-1 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <main className="min-w-0 space-y-5 p-4 sm:p-6" aria-label="Worker-owned monitoring">
          <div className="grid gap-4 sm:grid-cols-2">
            {capabilities.cameras.length > 0 ? (
              capabilities.cameras.map((camera) => (
                <OperatorCameraPreview
                  key={camera.name}
                  camera={camera.name}
                  active={previewActive}
                />
              ))
            ) : (
              <section className="bg-muted/20 text-muted-foreground flex aspect-4/3 items-center justify-center border p-4 text-center text-sm">
                No worker camera previews are configured
              </section>
            )}
          </div>
          <OperatorMonitoring samples={operator.telemetry} />
        </main>

        <aside
          className="order-first space-y-5 border-b p-4 sm:p-5 lg:order-none lg:border-b-0 lg:border-l"
          aria-label="Operator controls"
        >
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
            {capabilities.robots.map((robot) => (
              <span key={robot.role}>
                <span className="font-medium capitalize">{robot.role}</span>{' '}
                <span className="text-muted-foreground">
                  {robot.embodiment} · {robot.actuatorCount} actuators
                </span>
              </span>
            ))}
          </div>

          {physicalOperation && (
            <OperatorCalibrationPanel
              report={operator.calibration}
              error={operator.calibrationError}
              disabled={operator.isPending || !STARTABLE_STATES.includes(status?.state ?? '')}
              onCheck={operator.checkCalibration}
            />
          )}

          <section className="space-y-4" aria-labelledby="operator-session-heading">
            <div>
              <h3 id="operator-session-heading" className="text-sm font-semibold">
                Session
              </h3>
              <p className="text-muted-foreground mt-1 text-xs">
                Configure limits, verify readiness, then start the selected operation.
              </p>
            </div>
            <Tabs value={mode} onValueChange={(value) => selectMode(value as OperatorMode)}>
              <TabsList className="grid w-full grid-cols-3">
                {capabilities.modes.includes('teleoperate') && (
                  <TabsTrigger value="teleoperate">Teleoperate</TabsTrigger>
                )}
                {capabilities.modes.includes('record') && (
                  <TabsTrigger value="record">Record</TabsTrigger>
                )}
                {capabilities.modes.includes('policy') && (
                  <TabsTrigger value="policy">Policy</TabsTrigger>
                )}
              </TabsList>
            </Tabs>
            <OperatorSessionConfig
              cameras={capabilities.cameras}
              disabled={operator.isPending || !STARTABLE_STATES.includes(status?.state ?? '')}
              mode={mode}
              settings={settings}
              onChange={setSettings}
            />
            {capabilities.preflightEnabled && selectedProfile && (
              <Button
                type="button"
                variant="outline"
                className="w-full"
                disabled={operator.isPending || !calibrationReady}
                onClick={() =>
                  operator.runPreflight({
                    profile: selectedProfile,
                    mode,
                    uploadRequested: settings.saveDestination === 'local_and_hub',
                  })
                }
              >
                <ScanSearch className="h-4 w-4" aria-hidden="true" />
                Run hardware preflight
              </Button>
            )}
            {operator.preflight && (
              <div className="space-y-2 border-t pt-3 text-xs" aria-label="Preflight results">
                {operator.preflight.checks.length === 0 && (
                  <p className="font-medium">Hardware preflight passed</p>
                )}
                {operator.preflight.checks.map((check) => (
                  <div key={check.name}>
                    <p className="flex justify-between gap-3 font-medium">
                      <span>{check.name.replaceAll('_', ' ')}</span>
                      <span className={check.outcome === 'blocking' ? 'text-destructive' : ''}>
                        {check.outcome}
                      </span>
                    </p>
                    <p className="text-muted-foreground">{redactOperatorText(check.detail)}</p>
                  </div>
                ))}
              </div>
            )}
            <Button type="button" className="w-full" disabled={!canStart} onClick={start}>
              <CirclePlay className="h-4 w-4" aria-hidden="true" />
              Start {mode === 'teleoperate' ? 'teleoperation' : mode}
            </Button>
          </section>

          {status?.state === 'running' && status.mode === 'record' && (
            <section className="grid gap-2 border-t pt-4" aria-label="Recording lifecycle commands">
              <Button
                type="button"
                variant="outline"
                disabled={operator.isPending}
                onClick={() =>
                  operator.sendCommand(status.recordingPhase === 'paused' ? 'resume' : 'pause')
                }
              >
                <Pause className="h-4 w-4" aria-hidden="true" />
                {status.recordingPhase === 'paused' ? 'Resume recording' : 'Pause recording'}
              </Button>
              <Button
                type="button"
                variant="outline"
                disabled={operator.isPending}
                onClick={() => operator.sendCommand('save')}
              >
                <Save className="h-4 w-4" aria-hidden="true" />
                Save episode
              </Button>
              <Button
                type="button"
                variant="outline"
                disabled={operator.isPending}
                onClick={() => operator.sendCommand('rerecord')}
              >
                <RotateCcw className="h-4 w-4" aria-hidden="true" />
                Rerecord episode
              </Button>
              <Button
                type="button"
                disabled={operator.isPending}
                onClick={() => operator.sendCommand('finish')}
              >
                <Square className="h-4 w-4" aria-hidden="true" />
                Finish recording
              </Button>
            </section>
          )}
        </aside>
      </div>
    </div>
  )
}
