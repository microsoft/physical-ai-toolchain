import { Ban, CheckCircle2, CircleAlert, Loader2, PackageCheck } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Progress } from '@/components/ui/progress'
import {
  useCancelRelease,
  useReleaseEligibility,
  useReleaseJob,
  useSubmitRelease,
} from '@/hooks/use-releases'
import { ApiClientError } from '@/lib/api-client'
import { recordDiagnosticEvent } from '@/lib/playback-diagnostics'
import type {
  ReleaseJobState,
  ReleaseSubmitRequest,
  ReleaseWorkflowResponse,
  ReviewDecision,
} from '@/types'

interface ReleaseDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  datasetId: string
  episodeIndex: number
  actorId: string
  decision: ReviewDecision | null
}

const ACTIVE_STATES = new Set<ReleaseJobState>(['queued', 'running', 'verifying', 'publishing'])

function persistedJobKey(datasetId: string): string {
  return `dataviewer:release-job:${datasetId}`
}

function humanize(value: string): string {
  return value.replaceAll('-', ' ')
}

function exclusionMessage(reasonCodes: string[]): string {
  if (reasonCodes.includes('source-identity-changed')) {
    return 'Saved episode data changed after acceptance. Run quality and accept the current saved version before release.'
  }
  return reasonCodes.map(humanize).join(', ')
}

function errorMessage(error: Error | null): string | null {
  if (!error) {
    return null
  }
  if (error instanceof ApiClientError && error.status === 409) {
    return 'Release request conflicts with the current state or an existing destination.'
  }
  return error.message
}

function ReleaseResult({ workflow }: { workflow: ReleaseWorkflowResponse }) {
  if (workflow.state === 'succeeded') {
    return (
      <div className="space-y-2" aria-live="polite">
        <div className="flex items-center gap-2 text-sm font-medium">
          <CheckCircle2 className="h-4 w-4 text-emerald-600" />
          Release verified
        </div>
        <dl className="grid gap-1 text-xs sm:grid-cols-[7rem_minmax(0,1fr)]">
          <dt className="text-muted-foreground">Manifest</dt>
          <dd className="break-all">{workflow.verification.manifestPath}</dd>
          <dt className="text-muted-foreground">Checksums</dt>
          <dd className="break-all">{workflow.verification.checksumsPath}</dd>
        </dl>
      </div>
    )
  }

  const active = ACTIVE_STATES.has(workflow.state)
  const percent = workflow.progress?.percent
  return (
    <div className="space-y-2 text-sm" aria-live="polite">
      <div className="flex items-center gap-2">
        {active ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <Ban className="h-4 w-4 text-rose-600" />
        )}
        <span className="capitalize">{humanize(workflow.state)}</span>
      </div>
      {active && (
        <div className="space-y-1.5">
          <div className="text-muted-foreground flex items-center justify-between text-xs">
            <span>{workflow.progress?.message ?? 'Processing release'}</span>
            {percent !== null && percent !== undefined && <span>{percent}% estimated</span>}
          </div>
          <Progress
            value={percent ?? undefined}
            className="h-2"
            aria-label="Estimated release progress"
          />
          <p className="text-muted-foreground text-xs">
            Keep Data Viewer open until this completes. Closing this dialog does not cancel the
            release; reopen Release to view its status.
          </p>
        </div>
      )}
      {workflow.conflict && <p className="text-destructive">{workflow.conflict}</p>}
    </div>
  )
}

export function ReleaseDialog({ open, onOpenChange, datasetId, actorId }: ReleaseDialogProps) {
  const [releaseId, setReleaseId] = useState('')
  const [reason, setReason] = useState('')
  const [destinationKind, setDestinationKind] = useState<'local' | 'azure'>('local')
  const [trackedJobId, setTrackedJobId] = useState<string | null>(() =>
    typeof window === 'undefined' ? null : window.localStorage.getItem(persistedJobKey(datasetId)),
  )
  const lastRecordedJobStateRef = useRef<string | null>(null)
  const previewRequest = useMemo<ReleaseSubmitRequest | null>(() => {
    if (!open) {
      return null
    }
    return {
      releaseId: 'eligibility-preview',
      datasetId,
      actorId,
      reason: 'Eligibility preflight',
      destinationKind,
      idempotencyKey: `preview-${datasetId}`,
      targetFormat: { name: 'lerobot', version: '3.0' },
      episodes: [],
    }
  }, [actorId, datasetId, destinationKind, open])
  const eligibility = useReleaseEligibility(previewRequest)
  const submit = useSubmitRelease()
  const selectedJobId = submit.data?.jobId ?? trackedJobId
  const job = useReleaseJob(selectedJobId)
  const cancel = useCancelRelease()
  const workflow = job.data ?? submit.data
  const excluded = eligibility.data?.excludedEpisodes ?? []
  const rejected = eligibility.data?.rejectedEpisodes ?? []
  const nonincluded = [...rejected, ...excluded]
  const groupedExclusions = Array.from(
    nonincluded.reduce((groups, episode) => {
      const reason = episode.reasonCodes[0] ?? 'not-eligible'
      groups.set(reason, [...(groups.get(reason) ?? []), episode.episodeIndex])
      return groups
    }, new Map<string, number[]>()),
  )
  const isEligible = Boolean(eligibility.data?.eligibleEpisodes.length)
  const visibleError = errorMessage(job.error ?? submit.error ?? eligibility.error ?? cancel.error)

  useEffect(() => {
    setTrackedJobId(window.localStorage.getItem(persistedJobKey(datasetId)))
  }, [datasetId, open])

  useEffect(() => {
    if (!submit.data) {
      return
    }
    setTrackedJobId(submit.data.jobId)
    window.localStorage.setItem(persistedJobKey(datasetId), submit.data.jobId)
  }, [datasetId, submit.data])

  useEffect(() => {
    if (!workflow || ACTIVE_STATES.has(workflow.state)) {
      return
    }
    window.localStorage.removeItem(persistedJobKey(datasetId))
  }, [datasetId, workflow])

  useEffect(() => {
    if (!workflow) {
      return
    }
    const jobStateKey = `${workflow.jobId}:${workflow.state}`
    if (lastRecordedJobStateRef.current === jobStateKey) {
      return
    }
    lastRecordedJobStateRef.current = jobStateKey
    recordDiagnosticEvent('release', 'job-state', {
      releaseId: workflow.releaseId,
      jobId: workflow.jobId,
      state: workflow.state,
      verified: workflow.verification.verified,
    })
  }, [workflow])

  const handleStartNewRelease = () => {
    submit.reset()
    setTrackedJobId(null)
    setReleaseId('')
    setReason('')
    setDestinationKind('local')
  }

  const handleSubmit = async () => {
    if (!releaseId.trim() || !reason.trim() || !isEligible) {
      return
    }
    const refreshed = await eligibility.refetch()
    if (!refreshed.data?.eligibleEpisodes.length) {
      return
    }
    submit.mutate({
      releaseId: releaseId.trim(),
      datasetId,
      actorId,
      reason: reason.trim(),
      destinationKind,
      idempotencyKey: `${releaseId.trim()}-${datasetId}`,
      targetFormat: { name: 'lerobot', version: '3.0' },
      episodes: [],
    })
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <PackageCheck className="h-5 w-5" />
            Create Dataset Release
          </DialogTitle>
          <DialogDescription>
            Publish accepted episodes as an immutable, verified LeRobot package. Save current
            annotation changes before creating the release.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4">
          <div className="grid gap-2 sm:grid-cols-2">
            <div className="grid gap-1.5">
              <Label htmlFor="release-id">Release ID</Label>
              <Input
                id="release-id"
                value={releaseId}
                onChange={(event) => setReleaseId(event.target.value)}
                disabled={Boolean(workflow)}
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="release-destination">Destination</Label>
              <select
                id="release-destination"
                className="border-input bg-background h-9 rounded-md border px-3 text-sm"
                value={destinationKind}
                onChange={(event) => setDestinationKind(event.target.value as 'local' | 'azure')}
                disabled={Boolean(workflow)}
              >
                <option value="local">Configured local release root</option>
                <option value="azure">Configured Azure Blob prefix</option>
              </select>
            </div>
          </div>

          <div className="grid gap-1.5">
            <Label htmlFor="release-reason">Reason</Label>
            <Input
              id="release-reason"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              disabled={Boolean(workflow)}
            />
          </div>

          {!workflow && (
            <p className="text-muted-foreground text-xs">
              The release runs in the background. Reloading or closing this dialog does not cancel
              it, and its status returns when Data Viewer reconnects.
            </p>
          )}

          <div className="grid gap-1 text-sm" aria-live="polite">
            <span className="text-muted-foreground text-xs font-medium uppercase">Readiness</span>
            {eligibility.isPending ? (
              <div className="flex items-center gap-2">
                <Loader2 className="h-4 w-4 animate-spin" />
                Checking release eligibility
              </div>
            ) : isEligible ? (
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                <CheckCircle2 className="h-4 w-4 text-emerald-600" />
                <span>{eligibility.data?.eligibleEpisodes.length ?? 0} included</span>
                <span>{nonincluded.length} excluded</span>
              </div>
            ) : eligibility.data ? (
              <div className="flex items-start gap-2">
                <CircleAlert className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
                No episodes are currently eligible. Resolve the exclusions below before creating a
                release.
              </div>
            ) : null}
            {groupedExclusions.map(([reason, episodeIndices]) => (
              <div key={reason} className="flex items-start gap-2">
                {reason === 'rejected' ? (
                  <Ban className="mt-0.5 h-4 w-4 shrink-0 text-rose-600" />
                ) : (
                  <CircleAlert className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
                )}
                <span>
                  {episodeIndices.length === 1 ? 'Episode' : 'Episodes'} {episodeIndices.join(', ')}
                  : {exclusionMessage([reason])}
                </span>
              </div>
            ))}
          </div>

          {workflow && <ReleaseResult workflow={workflow} />}
          {visibleError && <p className="text-destructive text-sm">{visibleError}</p>}
        </div>

        <DialogFooter>
          {workflow && ACTIVE_STATES.has(workflow.state) ? (
            <Button
              variant="destructive"
              onClick={() => cancel.mutate(workflow.jobId)}
              disabled={cancel.isPending}
            >
              Cancel Release
            </Button>
          ) : (
            <>
              <Button variant="outline" onClick={() => onOpenChange(false)}>
                Close
              </Button>
              {workflow ? (
                <Button onClick={handleStartNewRelease}>Start New Release</Button>
              ) : (
                <Button
                  onClick={handleSubmit}
                  disabled={
                    !isEligible ||
                    eligibility.isFetching ||
                    !releaseId.trim() ||
                    !reason.trim() ||
                    submit.isPending
                  }
                >
                  Create Release
                </Button>
              )}
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
