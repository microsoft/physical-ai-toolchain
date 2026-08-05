import {
  Bot,
  Camera,
  CirclePlay,
  Gauge,
  Pause,
  RefreshCcw,
  Save,
  ScanSearch,
  Square,
  Trash2,
  Video,
} from 'lucide-react'
import { useEffect, useState } from 'react'

import type { OperatorMode, OperatorSessionSettings } from '@/api/operator'
import { OperatorCameraPreview } from '@/components/operator/OperatorCameraPreview'
import { OperatorSessionConfig } from '@/components/operator/OperatorSessionConfig'
import { OperatorTelemetryPlot } from '@/components/operator/OperatorTelemetryPlot'
import { OperatorTrajectoryPlot } from '@/components/operator/OperatorTrajectoryPlot'
import { Button } from '@/components/ui/button'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import type { OperatorController } from '@/hooks/use-operator'

interface OperatorWorkspaceProps {
  operator: OperatorController
}

export function OperatorWorkspace({ operator }: OperatorWorkspaceProps) {
  const { capabilities, status } = operator
  const cleanupBlocked = status?.cleanupUnconfirmed === true
  const canStart =
    capabilities?.enabled === true &&
    !cleanupBlocked &&
    status !== undefined &&
    ['idle', 'completed', 'cancelled', 'failed'].includes(status.state)
  const [now, setNow] = useState(() => Date.now())
  const [mode, setMode] = useState<OperatorMode>('teleoperate')
  const cameras = capabilities?.cameras ?? [
    { name: 'wrist', defaultFps: 30 },
    { name: 'front', defaultFps: 30 },
  ]
  const [settings, setSettings] = useState<OperatorSessionSettings>({
    controlFps: 60,
    cameraFps: { wrist: 30, front: 30 },
    maxRelativeTarget: null,
    datasetName: 'so101-demo',
    task: 'Pick <obj> from <loc1> and place in <obj2>',
    saveDestination: 'local',
    hubRepoId: null,
    numEpisodes: 50,
    episodeTimeS: 60,
    resetTimeS: 30,
    rolloutTimeS: 30,
  })
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1_000)
    return () => clearInterval(timer)
  }, [])
  useEffect(() => {
    if (status?.state !== 'running' || status.mode !== 'record') return
    const handleKeyDown = (event: KeyboardEvent) => {
      const target = event.target
      if (
        target instanceof HTMLInputElement ||
        target instanceof HTMLTextAreaElement ||
        (target instanceof HTMLElement && target.isContentEditable)
      ) {
        return
      }
      const action = {
        ArrowRight: 'save',
        ArrowLeft: 'rerecord',
        ArrowUp: 'finish',
        ' ': status.recordingPhase === 'paused' ? 'resume' : 'pause',
      } as const
      const selected = action[event.key as keyof typeof action]
      if (selected) {
        event.preventDefault()
        operator.sendCommand(selected)
      } else if (event.key === 'ArrowDown') {
        event.preventDefault()
        operator.stopSession()
      }
    }
    globalThis.addEventListener('keydown', handleKeyDown)
    return () => globalThis.removeEventListener('keydown', handleKeyDown)
  }, [operator, status?.mode, status?.recordingPhase, status?.state])
  const selectMode = (nextMode: OperatorMode) => {
    setMode(nextMode)
    setSettings((current) => ({
      ...current,
      controlFps: nextMode === 'teleoperate' ? 60 : 30,
      maxRelativeTarget: nextMode === 'policy' ? 2 : null,
    }))
  }
  const preflightCurrent =
    operator.preflight?.lifecycle === 'completed' &&
    operator.preflight.mode === mode &&
    operator.preflight.startEligible &&
    Date.parse(operator.preflight.expiresAt) > now
  const robots = capabilities?.robots ?? []
  const actuatorCount = robots[0]?.actuatorCount
  const activeControlFps = status?.sessionSettings?.controlFps ?? settings.controlFps
  const episodeNumber = (status?.episodeIndex ?? 0) + 1
  const episodeTotal = status?.sessionSettings?.numEpisodes ?? settings.numEpisodes
  const episodeProgress = Math.min(100, (episodeNumber / episodeTotal) * 100)

  return (
    <div className="bg-background flex h-full min-h-0 flex-col overflow-auto">
      <header className="border-b px-6 py-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold">Operator</h2>
            <p className="text-muted-foreground text-sm">
              {status?.mode === 'record' ? 'Recording session' : 'SO-101 leader and follower'}
            </p>
          </div>
          <div className="flex items-center gap-2 text-sm">
            <span className="text-muted-foreground">State</span>
            <span className="font-medium capitalize">{status?.state ?? 'loading'}</span>
            {operator.connectionState === 'retrying' && (
              <span className="text-destructive font-medium">Reconnecting</span>
            )}
          </div>
        </div>
        <div className="text-muted-foreground mt-3 flex flex-wrap items-center gap-x-5 gap-y-2 border-t pt-3 text-xs">
          <div className="text-foreground flex items-center gap-1.5 font-medium">
            <Gauge className="h-3.5 w-3.5" />
            <span>{activeControlFps} FPS</span>
          </div>
          {cameras.map((camera) => (
            <div key={camera.name} className="flex items-center gap-1.5">
              <Camera className="h-3.5 w-3.5" />
              <span className="text-foreground">
                {camera.name.charAt(0).toUpperCase() + camera.name.slice(1)} camera
              </span>
              <span>
                · {status?.sessionSettings?.cameraFps[camera.name] ?? camera.defaultFps} fps
              </span>
            </div>
          ))}
          {robots.map((robot) => (
            <div key={robot.role} className="flex items-center gap-1.5" title={robot.name}>
              <Bot className="h-3.5 w-3.5" />
              <span className="text-foreground">
                {robot.embodiment} {robot.role}
              </span>
              <span>· {robot.name}</span>
            </div>
          ))}
          {actuatorCount && (
            <span className="text-foreground font-medium">{actuatorCount} actuators</span>
          )}
        </div>
      </header>

      <div className="grid min-h-0 flex-1 gap-0 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <section className="space-y-5 p-6" aria-label="Operator telemetry">
          <div
            className="grid items-start gap-4 sm:grid-cols-2 xl:grid-cols-3"
            aria-label="Operator visualizations"
          >
            {cameras.map((camera) => (
              <OperatorCameraPreview
                key={camera.name}
                active={status?.state === 'running'}
                camera={camera.name}
              />
            ))}
            <div className="sm:col-span-2 xl:col-span-1">
              <OperatorTrajectoryPlot samples={operator.telemetry} />
            </div>
          </div>
          <OperatorTelemetryPlot samples={operator.telemetry} />
        </section>

        <aside className="bg-card order-first border-t p-5 lg:order-none lg:border-t-0 lg:border-l">
          <div className="mb-4 space-y-2" aria-live="polite">
            {cleanupBlocked && (
              <p className="text-destructive text-sm">
                Worker cleanup was not confirmed. Verify hardware before reconnecting.
              </p>
            )}
            {status?.error && <p className="text-destructive text-sm">{status.error}</p>}
            {operator.error && operator.error !== status?.error && (
              <p className="text-destructive text-sm">{operator.error}</p>
            )}
          </div>
          {!capabilities?.enabled ? (
            <div className="space-y-2">
              <h3 className="font-medium">Operator unavailable</h3>
              <p className="text-muted-foreground text-sm">
                {capabilities?.reason ?? 'Operator capabilities are loading'}
              </p>
            </div>
          ) : capabilities.preflightEnabled &&
            !['starting', 'running', 'stopping'].includes(status?.state ?? '') ? (
            <div className="space-y-4">
              <div>
                <h3 className="font-medium">SO-101 readiness</h3>
                <p className="text-muted-foreground mt-1 text-sm">
                  Preflight is read-only. Starting a session can move the follower arm.
                </p>
              </div>
              <Tabs value={mode} onValueChange={(value) => selectMode(value as OperatorMode)}>
                <TabsList className="grid w-full grid-cols-3">
                  <TabsTrigger value="teleoperate">Teleoperate</TabsTrigger>
                  <TabsTrigger value="record">Record</TabsTrigger>
                  {capabilities.modes.includes('policy') && (
                    <TabsTrigger value="policy">Policy</TabsTrigger>
                  )}
                </TabsList>
              </Tabs>
              <OperatorSessionConfig
                cameras={cameras}
                disabled={operator.isPending || status?.state === 'running'}
                mode={mode}
                settings={settings}
                onChange={setSettings}
              />
              <Button
                type="button"
                onClick={() =>
                  operator.runPreflight(mode, settings.saveDestination === 'local_and_hub')
                }
                disabled={
                  operator.isPending ||
                  (settings.saveDestination === 'local_and_hub' && !settings.hubRepoId)
                }
              >
                <ScanSearch className="mr-2 h-4 w-4" />
                Run SO-101 Preflight
              </Button>
              {operator.preflight && (
                <div className="space-y-2" aria-label="Preflight results">
                  {operator.preflight.checks.map((check) => (
                    <div key={check.name} className="border-t pt-2 text-sm">
                      <div className="flex justify-between gap-2">
                        <span className="font-medium">{check.name.replaceAll('_', ' ')}</span>
                        <span
                          className={
                            check.outcome === 'blocking'
                              ? 'text-destructive'
                              : 'text-muted-foreground'
                          }
                        >
                          {check.outcome}
                        </span>
                      </div>
                      <p className="text-muted-foreground mt-1 text-xs">{check.detail}</p>
                      {check.remediation && (
                        <p className="mt-1 text-xs">Action: {check.remediation}</p>
                      )}
                    </div>
                  ))}
                  <p className="text-xs">
                    Lifecycle: {operator.preflight.lifecycle} · Start eligible:{' '}
                    {operator.preflight.startEligible ? 'yes' : 'no'} · Ownership complete:{' '}
                    {operator.preflight.ownershipComplete ? 'yes' : 'no'}
                  </p>
                  <p className="text-muted-foreground text-xs">
                    Expires {operator.preflight.expiresAt}
                  </p>
                  {capabilities.sessionStartEnabled && canStart && preflightCurrent && (
                    <Button
                      type="button"
                      onClick={() => operator.startSession(mode, settings)}
                      disabled={operator.isPending}
                    >
                      <CirclePlay className="mr-2 h-4 w-4" />
                      {mode === 'teleoperate'
                        ? 'Start Teleoperation'
                        : mode === 'record'
                          ? 'Start Recording'
                          : 'Start Policy'}
                    </Button>
                  )}
                </div>
              )}
            </div>
          ) : canStart ? (
            <div className="space-y-4">
              <div>
                <h3 className="font-medium">Start session</h3>
                <p className="text-muted-foreground mt-1 text-sm">
                  The simulated adapter exercises the same session contract used by hardware.
                </p>
              </div>
              <div className="grid gap-2">
                <Button
                  type="button"
                  onClick={() => void operator.startSession('teleoperate')}
                  disabled={operator.isPending}
                >
                  <CirclePlay className="mr-2 h-4 w-4" />
                  Start Teleoperation
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => void operator.startSession('record')}
                  disabled={operator.isPending}
                >
                  <Video className="mr-2 h-4 w-4" />
                  Start Recording
                </Button>
              </div>
            </div>
          ) : cleanupBlocked ? (
            <div className="space-y-2">
              <h3 className="font-medium">Reconnect blocked</h3>
              <p className="text-muted-foreground text-sm">
                Check that the robot and cameras are disconnected before restarting the backend.
              </p>
            </div>
          ) : (
            <div className="space-y-5">
              <div>
                <h3 className="font-medium capitalize">{status?.mode ?? 'Operator'} active</h3>
                <p className="text-muted-foreground mt-1 text-xs">
                  Session {status?.sessionId.slice(0, 8)} · revision {status?.revision}
                </p>
                {status?.mode === 'record' && (
                  <>
                    <div
                      className="bg-muted/50 mt-4 border border-l-4 border-l-cyan-500 p-3"
                      data-testid="episode-progress"
                    >
                      <div className="flex items-end justify-between gap-3">
                        <div>
                          <p className="text-muted-foreground text-[10px] font-semibold tracking-wider uppercase">
                            Recording progress
                          </p>
                          <p className="mt-1 text-lg font-semibold">
                            Episode {episodeNumber} of {episodeTotal}
                          </p>
                        </div>
                        <span className="text-3xl font-semibold tabular-nums">
                          {episodeNumber.toString().padStart(2, '0')}
                        </span>
                      </div>
                      <div className="bg-border mt-3 h-1.5 overflow-hidden" aria-hidden="true">
                        <div
                          className="h-full bg-cyan-500 transition-[width]"
                          style={{ width: `${episodeProgress}%` }}
                        />
                      </div>
                      {status.datasetId && (
                        <p className="text-muted-foreground mt-2 truncate text-xs">
                          {status.datasetId}
                        </p>
                      )}
                    </div>
                    {status.recordingPhase === 'paused' && (
                      <p className="mt-1 text-xs">
                        Recording paused. Teleoperation remains active.
                      </p>
                    )}
                  </>
                )}
                {status?.actualHz != null && (
                  <p className="text-muted-foreground mt-1 text-xs">
                    {status.actualHz.toFixed(1)} / {status.targetHz?.toFixed(0)} Hz · p95{' '}
                    {status.loopP95Ms?.toFixed(1)} ms · overruns {status.overruns ?? 0}
                  </p>
                )}
                {status?.latestWorkerLog && (
                  <p className="text-muted-foreground mt-2 text-xs">{status.latestWorkerLog}</p>
                )}
                {status?.uploadStatus === 'succeeded' && (
                  <p className="mt-2 text-xs">Hugging Face upload completed</p>
                )}
                {status?.uploadStatus === 'failed' && (
                  <p className="text-destructive mt-2 text-xs">
                    {status.uploadError ?? 'Hugging Face upload failed; local dataset is preserved'}
                  </p>
                )}
              </div>

              {status?.state === 'running' && status.mode === 'record' && (
                <div className="grid gap-2">
                  {operator.isPending && (
                    <p className="text-muted-foreground text-xs" role="status">
                      Processing episode data. Stop Session remains available.
                    </p>
                  )}
                  <Button
                    type="button"
                    variant="outline"
                    title={
                      status.recordingPhase === 'paused'
                        ? 'Resume recording (Space)'
                        : 'Pause recording (Space)'
                    }
                    onClick={() =>
                      void operator.sendCommand(
                        status.recordingPhase === 'paused' ? 'resume' : 'pause',
                      )
                    }
                    disabled={operator.isPending}
                  >
                    {status.recordingPhase === 'paused' ? (
                      <CirclePlay className="mr-2 h-4 w-4" />
                    ) : (
                      <Pause className="mr-2 h-4 w-4" />
                    )}
                    {status.recordingPhase === 'paused' ? 'Resume Recording' : 'Pause Recording'}
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    title="Save episode (Right Arrow)"
                    onClick={() => void operator.sendCommand('save')}
                    disabled={operator.isPending}
                  >
                    <Save className="mr-2 h-4 w-4" />
                    Save Episode
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    title="Discard episode (Left Arrow)"
                    onClick={() => void operator.sendCommand('rerecord')}
                    disabled={operator.isPending}
                  >
                    <RefreshCcw className="mr-2 h-4 w-4" />
                    Discard Episode
                  </Button>
                  <Button
                    type="button"
                    title="Finish recording (Up Arrow)"
                    onClick={() => void operator.sendCommand('finish')}
                    disabled={operator.isPending}
                  >
                    <Square className="mr-2 h-4 w-4" />
                    Finish Recording
                  </Button>
                  <Button
                    type="button"
                    variant="destructive"
                    title="Cancel this session and delete all of its recordings"
                    onClick={() => void operator.stopSession()}
                    disabled={operator.isPending}
                  >
                    <Trash2 className="mr-2 h-4 w-4" />
                    Discard Recording
                  </Button>
                </div>
              )}
            </div>
          )}
        </aside>
      </div>
    </div>
  )
}
