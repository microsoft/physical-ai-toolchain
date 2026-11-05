import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, ChevronRight, LoaderCircle, RefreshCw, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import {
  fetchSourceEpisodeMedia,
  getReleaseArtifact,
  getSourceRelease,
  listSourceEpisodes,
  listSourceReleases,
  type ReleaseInventoryItem,
  type ReleaseJobState,
  type ReleaseJobStatus,
  type ReleaseRequest,
  ReleaseRequestError,
  type ReleaseResult,
  type ReleaseSubmission,
  type SourceEpisode,
  type SourceRelease,
} from '@/api/releases'
import { ReleaseDialog, type ReleaseItem } from '@/components/release/ReleaseDialog'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { usePrincipalContext } from '@/hooks/use-principal-context'
import { useRelease } from '@/hooks/use-release'
import { useEpisodeStore } from '@/stores'

const EPISODE_PAGE_SIZE = 50
const TERMINAL_STATES: ReadonlySet<ReleaseJobState> = new Set<ReleaseJobState>([
  'committed',
  'cancelled',
])
const CANCELLABLE_STATES: ReadonlySet<ReleaseJobState> = new Set<ReleaseJobState>([
  'accepted',
  'running',
  'needs_input',
])
const RECOVERABLE_STATES: ReadonlySet<ReleaseJobState> = new Set<ReleaseJobState>([
  'sealed',
  'committing',
  'cancelling',
])

const STATE_LABELS: Record<ReleaseJobState, string> = {
  accepted: 'Accepted',
  running: 'Running',
  needs_input: 'Needs input',
  cancelling: 'Cancelling',
  sealed: 'Sealed',
  committing: 'Committing',
  committed: 'Committed',
  cancelled: 'Cancelled',
}

export interface ReleaseWorkspaceProps {
  /** Source dataset identifier. */
  datasetId: string
}

/**
 * Format a byte count for display.
 *
 * @param bytes - Byte count.
 * @returns Human-readable size, or `Unknown` for invalid values.
 */
function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return 'Unknown'
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`
}

/**
 * Describe a failed release request without exposing raw error messages.
 *
 * @param error - Error from a release request.
 * @returns Short message containing only the backend error code.
 */
function describeError(error: unknown): string {
  if (error instanceof ReleaseRequestError) return `Release request failed: ${error.code}.`
  return 'Release request failed.'
}

/**
 * Map one inventory row to the selection shape the release dialog expects.
 *
 * @param item - Inventory row from the full source population.
 * @returns Dialog row.
 */
function toReleaseItem(item: ReleaseInventoryItem): ReleaseItem {
  return {
    episodeIndex: item.episodeIndex,
    detailed: item.detailed,
    annotation: item.annotation ?? undefined,
    edits: item.edits ?? undefined,
    labels: item.labels ?? [],
    provenance: item.provenance ?? null,
    judge: item.judge ?? null,
    resources: item.resources ?? null,
  }
}

/**
 * Query key for the verified releases of one source.
 *
 * @param scopeId - Principal scope identifier.
 * @param datasetId - Source dataset identifier.
 * @returns Query key.
 */
function sourceReleasesKey(scopeId: string, datasetId: string) {
  return ['releases', 'source-releases', scopeId, datasetId] as const
}

/**
 * Save a committed artifact as a local file. The object URL is revoked after the download starts.
 *
 * @param jobId - Committed job identifier.
 * @param name - Artifact name from the verified result.
 */
async function downloadArtifact(jobId: string, name: string): Promise<void> {
  const artifact = await getReleaseArtifact(jobId, name)
  const payload = artifact.content
  const text = typeof payload === 'string' ? payload : null
  const blob = new Blob([text ?? JSON.stringify(payload, null, 2)], {
    type: text === null ? 'application/json' : 'text/plain',
  })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `${name}.${text === null ? 'json' : 'txt'}`
  document.body.append(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 0)
}

function WorkspaceNotice({ message }: { message: string }) {
  return (
    <section aria-label="Releases" className="p-4">
      <p className="text-muted-foreground text-sm">{message}</p>
    </section>
  )
}

function describeJobStatus(status: ReleaseJobStatus | undefined, failed: boolean): string {
  if (failed) return 'Job status could not be loaded.'
  if (!status) return 'Loading job status...'
  if (status.state === 'needs_input') {
    return `Needs input: ${status.refusalCode ?? 'submission_refused'}. Cancel this job and submit a corrected request.`
  }
  if (status.recoveryRequired && !status.cancelRequested) {
    return 'Stalled. Recovery required. Cancel job to remove partial output, or Recover job to retry.'
  }
  const parts = [`${STATE_LABELS[status.state]}.`]
  if (status.state === 'cancelling' && status.cleanupState === 'failed') {
    parts.push('Cleanup failed. Retry cleanup to remove the remaining output.')
  }
  if (status.cancelRequested) parts.push('Cancellation requested.')
  else if (status.state === 'accepted') parts.push('Waiting for a release worker.')
  if (status.state === 'committed') parts.push('Verified result is available below.')
  return parts.join(' ')
}

const IO_COUNTER_LABELS: Record<string, string> = {
  affectedDownloadBytes: 'Affected input read',
  sourceVerificationBytes: 'Source verification read',
  storageCopyBytes: 'Copied within storage',
  generatedUploadBytes: 'Generated output uploaded',
  destinationVerificationBytes: 'Destination verification read',
}

function JobDetails({ status }: { status: ReleaseJobStatus }) {
  const counters = Object.entries(status.ioCounters)
  return (
    <div className="grid gap-3 text-sm">
      <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1">
        <dt className="text-muted-foreground">Destination now</dt>
        <dd>{formatBytes(status.destinationCurrentBytes)}</dd>
        <dt className="text-muted-foreground">Destination peak</dt>
        <dd>{formatBytes(status.destinationPeakBytes)}</dd>
        <dt className="text-muted-foreground">Scratch now</dt>
        <dd>{formatBytes(status.scratchBytes)}</dd>
        <dt className="text-muted-foreground">Scratch peak</dt>
        <dd>{formatBytes(status.peakScratchBytes)}</dd>
        <dt className="text-muted-foreground">Effects stopped</dt>
        <dd>{status.effectsStopped ? 'Yes' : 'No'}</dd>
        <dt className="text-muted-foreground">Cleanup</dt>
        <dd>{status.cleanupState}</dd>
        <dt className="text-muted-foreground">Capacity</dt>
        <dd>{status.capacityState}</dd>
      </dl>
      {counters.length > 0 && (
        <ul aria-label="I/O counters" className="grid gap-1">
          {counters.map(([name, value]) => (
            <li key={name} className="flex justify-between gap-4">
              <span className="text-muted-foreground">{IO_COUNTER_LABELS[name] ?? name}</span>
              <span className="shrink-0 tabular-nums">{formatBytes(value)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function EpisodeMedia({
  datasetId,
  handle,
  episodeIndex,
  camera,
  window,
}: {
  datasetId: string
  handle: string
  episodeIndex: number
  camera: string
  window?: [number, number]
}) {
  const [url, setUrl] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let active = true
    const controller = new AbortController()
    let objectUrl: string | null = null
    setUrl(null)
    setFailed(false)
    fetchSourceEpisodeMedia(datasetId, handle, episodeIndex, camera, controller.signal)
      .then((blob) => {
        if (!active) return
        objectUrl = URL.createObjectURL(blob)
        setUrl(objectUrl)
      })
      .catch(() => {
        if (active) setFailed(true)
      })
    return () => {
      active = false
      controller.abort()
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [datasetId, handle, episodeIndex, camera])

  if (failed) return <p className="text-sm">Media unavailable for this camera.</p>
  if (!url) return <p className="text-muted-foreground text-sm">Loading media...</p>
  return (
    <video
      aria-label={`Camera ${camera} for episode ${episodeIndex}`}
      src={url}
      controls
      muted
      onLoadedMetadata={(event) => {
        if (window) event.currentTarget.currentTime = window[0]
      }}
      onSeeking={(event) => {
        if (!window) return
        const video = event.currentTarget
        if (video.currentTime < window[0]) video.currentTime = window[0]
        else if (video.currentTime > window[1]) video.currentTime = window[1]
      }}
      onTimeUpdate={(event) => {
        if (!window) return
        const video = event.currentTarget
        if (video.currentTime < window[0]) video.currentTime = window[0]
        if (video.currentTime >= window[1]) {
          video.pause()
          if (video.currentTime > window[1]) video.currentTime = window[1]
        }
      }}
      className="bg-muted w-full max-w-2xl rounded-md"
    />
  )
}

function ReleaseDetail({
  datasetId,
  scopeId,
  release,
  onClose,
}: {
  datasetId: string
  scopeId: string
  release: SourceRelease
  onClose: () => void
}) {
  const [selected, setSelected] = useState<SourceEpisode | null>(null)
  const [camera, setCamera] = useState<string | null>(null)
  const episodeButton = useRef<HTMLButtonElement>(null)
  const episodes = useInfiniteQuery({
    queryKey: ['releases', 'source-episodes', scopeId, datasetId, release.handle],
    queryFn: ({ pageParam }) =>
      listSourceEpisodes(datasetId, release.handle, {
        cursor: pageParam,
        limit: EPISODE_PAGE_SIZE,
      }),
    initialPageParam: 0,
    getNextPageParam: (page) => page.nextCursor ?? undefined,
  })
  const rows = episodes.data?.pages.flatMap((page) => page.items) ?? []
  const cameras = selected?.cameras ?? []
  const activeCamera = camera !== null && cameras.includes(camera) ? camera : (cameras[0] ?? null)

  return (
    <section
      id="release-detail-panel"
      aria-labelledby="release-detail-heading"
      className="grid gap-4"
    >
      <div className="flex items-center justify-between gap-2">
        <h3 id="release-detail-heading" className="text-sm font-medium">
          Release details (read-only)
        </h3>
        <Button
          variant="ghost"
          size="icon"
          aria-label="Close release details"
          title="Close release details"
          onClick={onClose}
        >
          <X className="size-4" aria-hidden="true" />
        </Button>
      </div>
      <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
        <dt className="text-muted-foreground">Episodes</dt>
        <dd>{release.totalEpisodes}</dd>
        <dt className="text-muted-foreground">Annotations</dt>
        <dd>{release.annotations.length}</dd>
        <dt className="text-muted-foreground">Labels</dt>
        <dd>{Object.keys(release.labels?.episodes ?? {}).length}</dd>
      </dl>
      <details>
        <summary className="cursor-pointer text-sm">
          Saved annotations, labels and provenance
        </summary>
        <pre className="max-h-64 overflow-auto text-xs break-all whitespace-pre-wrap">
          {JSON.stringify({ annotations: release.annotations, labels: release.labels }, null, 2)}
        </pre>
      </details>
      <details>
        <summary className="cursor-pointer text-sm">Original frozen evidence</summary>
        <pre className="max-h-64 overflow-auto text-xs break-all whitespace-pre-wrap">
          {JSON.stringify(release.frozenEvidence, null, 2)}
        </pre>
      </details>

      <div className="grid gap-2">
        <p className="text-sm font-medium">Episodes in release</p>
        {episodes.isError && <p className="text-sm">Episodes could not be loaded.</p>}
        <ul
          aria-label="Release episodes"
          className="divide-border max-h-64 divide-y overflow-y-auto rounded-md border"
        >
          {rows.map((episode) => (
            <li key={episode.episodeIndex}>
              <button
                ref={selected?.episodeIndex === episode.episodeIndex ? episodeButton : null}
                type="button"
                aria-expanded={selected?.episodeIndex === episode.episodeIndex}
                aria-controls="release-episode-panel"
                onClick={() => {
                  setSelected(selected?.episodeIndex === episode.episodeIndex ? null : episode)
                  setCamera(null)
                }}
                className="hover:bg-muted flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-sm"
              >
                <span className="flex shrink-0 items-center gap-2">
                  {selected?.episodeIndex === episode.episodeIndex ? (
                    <ChevronDown className="size-4" aria-hidden="true" />
                  ) : (
                    <ChevronRight className="size-4" aria-hidden="true" />
                  )}
                  Episode {episode.episodeIndex}
                </span>
                <span className="text-muted-foreground truncate text-xs">
                  {episode.instruction}
                </span>
              </button>
            </li>
          ))}
        </ul>
        {episodes.hasNextPage && (
          <Button
            variant="outline"
            size="sm"
            className="justify-self-start"
            onClick={() => void episodes.fetchNextPage()}
            disabled={episodes.isFetchingNextPage}
          >
            Load more episodes
          </Button>
        )}
      </div>

      {selected && (
        <section
          id="release-episode-panel"
          aria-labelledby="release-episode-heading"
          className="grid gap-3"
        >
          <div className="flex items-center justify-between gap-2">
            <h4 id="release-episode-heading" className="text-sm font-medium">
              Episode {selected.episodeIndex} details
            </h4>
            <Button
              variant="ghost"
              size="icon"
              aria-label="Close episode details"
              title="Close episode details"
              onClick={() => {
                setSelected(null)
                setCamera(null)
                episodeButton.current?.focus()
              }}
            >
              <X className="size-4" aria-hidden="true" />
            </Button>
          </div>
          <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
            <dt className="text-muted-foreground">Instruction</dt>
            <dd>{selected.instruction}</dd>
            <dt className="text-muted-foreground">FPS</dt>
            <dd>{selected.fps}</dd>
            <dt className="text-muted-foreground">Length</dt>
            <dd>{selected.length}</dd>
          </dl>
          {cameras.length > 0 && (
            <div role="group" aria-label="Cameras" className="flex flex-wrap gap-2">
              {cameras.map((name) => (
                <Button
                  key={name}
                  variant="outline"
                  size="sm"
                  aria-pressed={activeCamera === name}
                  onClick={() => setCamera(name)}
                >
                  {name}
                </Button>
              ))}
            </div>
          )}
          {activeCamera && (
            <EpisodeMedia
              datasetId={datasetId}
              handle={release.handle}
              episodeIndex={selected.episodeIndex}
              camera={activeCamera}
              window={selected.videoWindows?.[activeCamera]}
            />
          )}
        </section>
      )}
    </section>
  )
}

function ReleaseWorkspaceContent({
  datasetId,
  scopeId,
  sourceId,
}: {
  datasetId: string
  scopeId: string
  sourceId: string | undefined
}) {
  const queryClient = useQueryClient()
  const release = useRelease(datasetId, scopeId, sourceId)
  const [dialogOpen, setDialogOpen] = useState(false)
  const createButton = useRef<HTMLButtonElement>(null)
  const workspaceRegion = useRef<HTMLElement>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [selectedHandle, setSelectedHandle] = useState<string | null>(null)
  const [collapsedJobId, setCollapsedJobId] = useState<string | null>(null)
  const jobButton = useRef<HTMLButtonElement>(null)
  const releaseButton = useRef<HTMLButtonElement>(null)
  const jobExpanded = release.activeJobId !== null && collapsedJobId !== release.activeJobId

  const capabilities = release.capabilities.data
  const capabilityError = release.capabilities.error
  const releaseAvailable = capabilities?.jobStore === true && capabilities?.planning === true
  const destinations = capabilities?.destinations.map((destination) => destination.alias) ?? []

  const status = release.status.data
  const activeState = status?.state
  const activeTerminal = activeState !== undefined && TERMINAL_STATES.has(activeState)
  const jobActive = release.activeJobId !== null && !activeTerminal
  const createBlocked = jobActive || release.hasPendingSubmission
  const canCancel =
    status !== undefined &&
    CANCELLABLE_STATES.has(status.state) &&
    !status.cancelRequested &&
    !release.isCancelling
  const canRecover =
    status !== undefined &&
    (RECOVERABLE_STATES.has(status.state) || status.recoveryRequired) &&
    !release.isRecovering
  const cleanupFailed = status?.state === 'cancelling' && status.cleanupState === 'failed'
  const canClearJob = release.activeJobId !== null && (activeTerminal || release.status.isError)

  const sourceReleases = useQuery({
    queryKey: sourceReleasesKey(scopeId, datasetId),
    queryFn: () => listSourceReleases(datasetId),
    enabled: releaseAvailable,
  })

  const detail = useQuery({
    queryKey: ['releases', 'source-release', scopeId, datasetId, selectedHandle],
    queryFn: () => getSourceRelease(datasetId, selectedHandle as string),
    enabled: releaseAvailable && selectedHandle !== null,
  })

  const jobRows = release.jobs.data?.pages.flatMap((page) => page.items) ?? []

  // A committed job adds a verified release to the collection.
  useEffect(() => {
    if (activeState === 'committed') {
      void queryClient.invalidateQueries({ queryKey: sourceReleasesKey(scopeId, datasetId) })
    }
  }, [activeState, queryClient, scopeId, datasetId])

  const result: ReleaseResult | undefined = release.result.data
  const dialogItems: ReleaseItem[] = release.inventoryItems.map(toReleaseItem)
  const inventoryMessage = release.inventory.isError
    ? 'Source inventory could not be loaded. Release selection is unavailable.'
    : release.inventoryProgress.total === null
      ? 'Loading source inventory...'
      : `Loading source inventory: ${release.inventoryProgress.loaded} of ${release.inventoryProgress.total} episodes loaded.`
  const retryInventory = () => {
    void release.inventory.refetch()
  }

  const capabilityMessage =
    capabilityError instanceof ReleaseRequestError &&
    (capabilityError.status === 401 || capabilityError.status === 403)
      ? 'Release access is denied for this account.'
      : 'Release capabilities could not be loaded.'

  const runAction = async (action: () => Promise<unknown>) => {
    setNotice(null)
    try {
      await action()
    } catch (error) {
      setNotice(describeError(error))
    }
  }

  const submitFromDialog = async (request: ReleaseRequest, submission: ReleaseSubmission) => {
    setNotice(null)
    await release.submit(request, submission)
  }

  return (
    <section ref={workspaceRegion} tabIndex={-1} aria-label="Releases" className="grid gap-6 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg font-semibold">Curated releases</h2>
        <Button
          ref={createButton}
          onClick={() => setDialogOpen(true)}
          disabled={!releaseAvailable || createBlocked}
          aria-describedby={createBlocked ? 'release-create-hint' : undefined}
        >
          Create release
        </Button>
      </div>
      {createBlocked && (
        <p id="release-create-hint" className="text-muted-foreground text-sm">
          Finish, cancel, or discard the active or pending release before creating another.
        </p>
      )}

      {capabilityError && (
        <Alert variant="destructive">
          <AlertDescription>{capabilityMessage}</AlertDescription>
        </Alert>
      )}
      {!capabilityError && capabilities && !releaseAvailable && (
        <p className="text-muted-foreground text-sm">
          Releases are not available for this deployment.
        </p>
      )}
      {release.inventory.isError && (
        <Alert variant="destructive">
          <AlertDescription className="grid gap-2">
            <span>{inventoryMessage}</span>
            <Button variant="outline" size="sm" onClick={retryInventory}>
              <RefreshCw className="mr-2 size-4" aria-hidden="true" />
              Retry inventory
            </Button>
          </AlertDescription>
        </Alert>
      )}
      {releaseAvailable && !release.inventoryReady && !release.inventory.isError && (
        <p role="status" className="text-muted-foreground flex items-center gap-2 text-sm">
          <LoaderCircle
            className="size-4 shrink-0 animate-spin motion-reduce:animate-none"
            aria-hidden="true"
          />
          {inventoryMessage}
        </p>
      )}

      {notice && (
        <Alert variant="destructive">
          <AlertDescription>{notice}</AlertDescription>
        </Alert>
      )}

      {release.hasPendingSubmission && !release.isSubmitting && !release.activeJobId && (
        <Alert>
          <AlertDescription className="grid gap-3">
            <span>
              A previous submission may have been accepted but its response was not received. Look
              it up before creating another release.
            </span>
            {release.pendingLookup.isError && (
              <span>
                No accepted job was found yet. Retry the lookup or discard the submission.
              </span>
            )}
            <span className="flex flex-wrap gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => void release.pendingLookup.refetch()}
                disabled={release.pendingLookup.isFetching}
              >
                Look up submission
              </Button>
              <Button variant="ghost" size="sm" onClick={() => release.startNew()}>
                Discard submission
              </Button>
            </span>
          </AlertDescription>
        </Alert>
      )}

      {jobExpanded && (
        <section
          id="release-job-panel"
          aria-labelledby="release-job-heading"
          className="grid gap-3 rounded-md border p-3"
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 id="release-job-heading" className="text-sm font-medium">
              Job{' '}
              <span className="font-mono" title={release.activeJobId ?? undefined}>
                {release.activeJobId?.slice(0, 8)}
              </span>
            </h3>
            <div className="flex flex-wrap gap-2">
              {status && !TERMINAL_STATES.has(status.state) && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => void release.status.refetch()}
                  disabled={release.status.isFetching}
                >
                  Refresh status
                </Button>
              )}
              <Button
                variant="outline"
                size="sm"
                disabled={!canCancel}
                onClick={() => void runAction(() => release.cancel())}
              >
                Cancel job
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={!canRecover}
                onClick={() => void runAction(() => release.recover())}
              >
                {release.isRecovering ? (
                  <LoaderCircle aria-hidden="true" className="mr-2 size-4 animate-spin" />
                ) : (
                  <RefreshCw aria-hidden="true" className="mr-2 size-4" />
                )}
                {cleanupFailed ? 'Retry cleanup' : 'Recover job'}
              </Button>
              {canClearJob && (
                <Button variant="ghost" size="sm" onClick={() => release.startNew()}>
                  Clear saved job
                </Button>
              )}
              <Button
                variant="ghost"
                size="icon"
                aria-label="Close job details"
                title="Close job details"
                onClick={() => {
                  setCollapsedJobId(release.activeJobId)
                  ;(jobButton.current ?? workspaceRegion.current)?.focus()
                }}
              >
                <X className="size-4" aria-hidden="true" />
              </Button>
            </div>
          </div>
          <p role="status" aria-live="polite" className="text-sm">
            {describeJobStatus(status, release.status.isError)}
          </p>
          {status && <JobDetails status={status} />}
        </section>
      )}

      {jobExpanded && status?.state === 'committed' && (
        <section
          aria-labelledby="release-result-heading"
          className="grid gap-3 rounded-md border p-3"
        >
          <h3 id="release-result-heading" className="text-sm font-medium">
            Verified result
          </h3>
          {release.result.isError && (
            <Alert variant="destructive">
              <AlertDescription>The verified result could not be loaded.</AlertDescription>
            </Alert>
          )}
          {result && (
            <ul className="flex flex-wrap gap-2">
              {result.artifacts.map((name) => (
                <li key={name}>
                  <Button
                    variant="outline"
                    size="sm"
                    aria-label={`Download ${name}`}
                    onClick={() => void runAction(() => downloadArtifact(status.jobId, name))}
                  >
                    Download {name}
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {releaseAvailable && (
        <section aria-labelledby="release-jobs-heading" className="grid gap-3">
          <h3 id="release-jobs-heading" className="text-sm font-medium">
            Release jobs for this source
          </h3>
          {release.jobs.isError ? (
            <p className="text-sm">Release jobs could not be loaded.</p>
          ) : (
            <>
              <ul aria-label="Release jobs" className="divide-border divide-y rounded-md border">
                {jobRows.map((job) => (
                  <li
                    key={job.jobId}
                    className="flex flex-wrap items-center justify-between gap-3 px-3 py-2 text-sm"
                  >
                    <span className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1">
                      <span className="font-mono text-xs">{job.jobId.slice(0, 8)}</span>
                      <CreationDate value={job.createdAt} />
                    </span>
                    <span>{job.recoveryRequired ? 'Stalled' : STATE_LABELS[job.state]}</span>
                    <Button
                      ref={release.activeJobId === job.jobId ? jobButton : null}
                      variant="outline"
                      size="sm"
                      aria-label={`${jobExpanded && release.activeJobId === job.jobId ? 'Close' : 'Open'} job ${job.jobId.slice(0, 8)}`}
                      aria-expanded={jobExpanded && release.activeJobId === job.jobId}
                      aria-controls="release-job-panel"
                      onClick={() => {
                        setCollapsedJobId(
                          jobExpanded && release.activeJobId === job.jobId ? job.jobId : null,
                        )
                        if (release.activeJobId !== job.jobId) release.selectJob(job.jobId)
                      }}
                    >
                      {jobExpanded && release.activeJobId === job.jobId ? 'Close job' : 'Open job'}
                    </Button>
                  </li>
                ))}
              </ul>
              {!release.jobs.isLoading && jobRows.length === 0 && (
                <p className="text-muted-foreground text-sm">No release jobs for this source.</p>
              )}
              {release.jobs.hasNextPage && (
                <Button
                  variant="outline"
                  size="sm"
                  className="justify-self-start"
                  onClick={() => void release.jobs.fetchNextPage()}
                  disabled={release.jobs.isFetchingNextPage}
                >
                  Load more jobs
                </Button>
              )}
            </>
          )}
        </section>
      )}

      <section aria-labelledby="release-collection-heading" className="grid gap-3">
        <h3 id="release-collection-heading" className="text-sm font-medium">
          Source releases
        </h3>
        {sourceReleases.isLoading && (
          <p className="text-muted-foreground text-sm">Loading releases...</p>
        )}
        {sourceReleases.isError && (
          <Alert variant="destructive">
            <AlertDescription>Source releases could not be loaded.</AlertDescription>
          </Alert>
        )}
        {sourceReleases.data && sourceReleases.data.items.length === 0 && (
          <p className="text-muted-foreground text-sm">No verified releases for this source.</p>
        )}
        <ul aria-label="Source releases" className="divide-border divide-y rounded-md border">
          {(sourceReleases.data?.items ?? []).map((item) => (
            <li key={item.handle}>
              <button
                ref={selectedHandle === item.handle ? releaseButton : null}
                type="button"
                aria-expanded={selectedHandle === item.handle}
                aria-controls="release-detail-panel"
                onClick={() =>
                  setSelectedHandle(selectedHandle === item.handle ? null : item.handle)
                }
                className="hover:bg-muted flex w-full flex-wrap items-center justify-between gap-3 px-3 py-2 text-left text-sm"
              >
                <span className="flex min-w-0 items-center gap-2">
                  {selectedHandle === item.handle ? (
                    <ChevronDown className="size-4 shrink-0" aria-hidden="true" />
                  ) : (
                    <ChevronRight className="size-4 shrink-0" aria-hidden="true" />
                  )}
                  <span className="min-w-0 [overflow-wrap:anywhere]">
                    {item.datasetName ?? 'Unnamed release'}
                  </span>
                </span>
                <span className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1">
                  <CreationDate value={item.createdAt} />
                  <span className="text-muted-foreground font-mono text-xs break-all">
                    {item.packageId}
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>
        {selectedHandle && detail.isError && (
          <Alert variant="destructive">
            <AlertDescription>The selected release could not be loaded.</AlertDescription>
          </Alert>
        )}
        {selectedHandle && detail.data && (
          <ReleaseDetail
            key={selectedHandle}
            datasetId={datasetId}
            scopeId={scopeId}
            release={detail.data}
            onClose={() => {
              setSelectedHandle(null)
              ;(releaseButton.current ?? workspaceRegion.current)?.focus()
            }}
          />
        )}
      </section>

      <ReleaseDialog
        datasetId={datasetId}
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        onCloseAutoFocus={(event) => {
          event.preventDefault()
          if (createButton.current && !createButton.current.disabled) createButton.current.focus()
          else workspaceRegion.current?.focus()
        }}
        items={dialogItems}
        inventoryReady={release.inventoryReady}
        inventoryMessage={inventoryMessage}
        inventoryFailed={release.inventory.isError}
        onRetryInventory={retryInventory}
        onRequestDetails={release.requestDetails}
        detailError={release.detailError}
        destinations={destinations}
        onPreview={release.preview}
        onCancelPreparation={release.cancelPreparation}
        onSubmit={submitFromDialog}
        onDiscardSubmission={() => release.startNew()}
        disabled={!releaseAvailable || createBlocked}
      />
    </section>
  )
}

/** Displays durable creation time in the browser's locale and timezone, without inventing legacy dates. */
function CreationDate({ value }: { value?: string | null }) {
  const date = value ? new Date(value) : null
  if (!date || Number.isNaN(date.getTime())) {
    return <span className="text-muted-foreground text-xs">Date unavailable</span>
  }
  return (
    <time
      dateTime={value ?? undefined}
      title={value ?? undefined}
      className="text-muted-foreground text-xs"
    >
      Created{' '}
      {new Intl.DateTimeFormat(undefined, {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        timeZoneName: 'short',
      }).format(date)}
    </time>
  )
}

/**
 * Releases tab for one source dataset: create a release from the full inventory, follow the active job,
 * browse verified source releases, and download committed artifacts.
 *
 * Cancel, recover, and clearing the saved job happen only on explicit clicks. Verified results are shown
 * only after the job commits. The workspace resets when the dataset, principal scope, or source identity changes.
 * Pane toggles only change visibility; closing a job pane preserves its saved reference and execution.
 *
 * @param props - Source dataset identifier.
 * @returns Release workspace element.
 */
export function ReleaseWorkspace({ datasetId }: ReleaseWorkspaceProps) {
  const principal = usePrincipalContext()
  const sourceId = useEpisodeStore((state) => state.currentEpisode?.sourceId)
  const sourceRevision = useEpisodeStore((state) => state.currentEpisode?.sourceRevision)
  const scopeId = principal.data?.scopeId

  if (principal.error) return <WorkspaceNotice message="Dataset access could not be verified." />
  if (!scopeId) return <WorkspaceNotice message="Checking dataset access..." />

  return (
    <ReleaseWorkspaceContent
      key={JSON.stringify([datasetId, scopeId, sourceId ?? null, sourceRevision ?? null])}
      datasetId={datasetId}
      scopeId={scopeId}
      sourceId={sourceId ?? undefined}
    />
  )
}
