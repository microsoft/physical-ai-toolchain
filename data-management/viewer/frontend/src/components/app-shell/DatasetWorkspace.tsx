import { ChevronDown, ChevronLeft, Play, RefreshCw, Square } from 'lucide-react'
import { useMemo, useState } from 'react'

import { Button } from '@/components/ui/button'
import { useEpisodeReadiness } from '@/hooks/use-episode-readiness'
import { usePrincipalContext } from '@/hooks/use-principal-context'
import { useJudgeDataset, useJudgeSamples, useVlmJudgeBatch } from '@/hooks/use-vlm-judge-batch'
import { formatEpisodeList, parseEpisodeList } from '@/lib/episode-list'
import { useEpisodeStore } from '@/stores'
import type {
  JudgeResetPreview,
  JudgeSampleReference,
  JudgeWithdrawalField,
} from '@/types/vlm-judge'

const MAX_GROUNDING = 25

function describeWithdrawal(field: JudgeWithdrawalField, completed: boolean): string {
  const runs = (ids?: string[]) => (ids?.length ? ` Runs: ${ids.join(', ')}.` : '')
  if (field.disposition === 'removable_fields')
    return `${completed ? 'Removed' : 'Will be removed'}: AI value.${runs(field.runIds)}`
  if (field.disposition === 'preserved_human') return 'Kept: a person edited this value.'
  if (field.reason === 'legacy_value_restored')
    return `Kept: the value existed before AI labeling; only the AI contribution is withdrawn.${runs(field.runIds)}`
  if (field.disposition === 'legacy_unknown') return 'Kept: origin unknown (saved before tracking).'
  if (field.reason === 'unlisted_machine_run')
    return `Not removed: written by AI runs missing from this job history.${runs(field.blockingRunIds)}`
  if (field.reason === 'multiple_human_authors')
    return `Not removed: ${field.humanAuthors ?? 'several'} people edited this value; edit the episode labels directly.`
  return 'Not removed: ownership could not be resolved.'
}

function WithdrawalFields({
  fields,
  completed,
}: {
  fields: JudgeWithdrawalField[]
  completed: boolean
}) {
  return (
    <ul className="max-h-48 space-y-1 overflow-y-auto text-sm">
      {fields.map((field) => (
        <li key={`${field.episodeIndex}:${field.field}`} className="break-words">
          Episode {field.episodeIndex}, <span>{field.field}</span>:{' '}
          {describeWithdrawal(field, completed)}
        </li>
      ))}
    </ul>
  )
}

interface DatasetWorkspaceProps {
  datasetId: string
  open: boolean
  enabled: boolean
  onToggle: () => void
}

export function DatasetWorkspace({ datasetId, open, enabled, onToggle }: DatasetWorkspaceProps) {
  const principal = usePrincipalContext()
  const sourceId = useEpisodeStore((state) => state.currentEpisode?.sourceId)
  const sourceRevision = useEpisodeStore((state) => state.currentEpisode?.sourceRevision)
  return (
    <DatasetWorkspaceContent
      key={JSON.stringify([datasetId, principal.data?.scopeId, sourceId, sourceRevision])}
      datasetId={datasetId}
      open={open}
      enabled={enabled && !!principal.data && !principal.error}
      unavailableReason={
        !enabled
          ? 'VLM judge is disabled.'
          : principal.error
            ? 'Dataset access could not be verified.'
            : 'Checking dataset access...'
      }
      accessError={enabled && !!principal.error}
      onToggle={onToggle}
    />
  )
}

function DatasetWorkspaceContent({
  datasetId,
  open,
  enabled,
  onToggle,
  unavailableReason,
  accessError,
}: DatasetWorkspaceProps & { unavailableReason: string; accessError: boolean }) {
  const [targetText, setTargetText] = useState('')
  const [method, setMethod] = useState('gvl')
  const [approvalId, setApprovalId] = useState('')
  const [groundingText, setGroundingText] = useState<string | null>(null)
  const [reviewId, setReviewId] = useState<string>()
  const [acknowledgeExceptions, setAcknowledgeExceptions] = useState(false)
  const [preview, setPreview] = useState<JudgeResetPreview | null>(null)
  const [acknowledgeReset, setAcknowledgeReset] = useState(false)
  const [snapshotId, setSnapshotId] = useState<string>()
  const [jobOffset, setJobOffset] = useState(0)
  const [views, setViews] = useState<string[]>([])
  const availableCameras = useEpisodeStore((state) => state.currentEpisode?.cameras) ?? []
  const workspace = useJudgeDataset(datasetId, enabled, reviewId, snapshotId, jobOffset)
  const batch = useVlmJudgeBatch(datasetId)
  const inventoryItems = workspace.inventory.data?.items
  const known = useMemo(
    () => (inventoryItems ? new Set(inventoryItems) : undefined),
    [inventoryItems],
  )
  const annotated = workspace.annotated.data
  const usable = workspace.usable.data
  const notUsable = usable ? (annotated ?? []).filter((index) => !usable.includes(index)) : []
  const groundingValue = groundingText ?? formatEpisodeList((usable ?? []).slice(0, MAX_GROUNDING))
  const grounding = parseEpisodeList(groundingValue, known)
  const sampleIndices = grounding.indices
  const groundingError =
    grounding.error ??
    (sampleIndices.length > MAX_GROUNDING
      ? `Use at most ${MAX_GROUNDING} grounding episodes.`
      : null)
  const parsedTargets = parseEpisodeList(targetText, known)
  const targets = targetText.trim()
    ? parsedTargets.indices
    : (inventoryItems ?? []).filter((index) => !sampleIndices.includes(index))
  const targetError = targetText.trim() ? parsedTargets.error : null
  const readiness = useEpisodeReadiness(
    datasetId,
    [...new Set([...targets, ...sampleIndices])],
    open && enabled,
  )
  const samples = useJudgeSamples(
    datasetId,
    groundingError ? [] : sampleIndices,
    workspace.groundingAuthors ?? {},
    open && enabled,
    workspace.groundingLabelRevisions ?? {},
  )
  const jobs = workspace.jobs.data?.items ?? []
  const busy = batch.isPending || workspace.isPending
  const canMutate = enabled && !workspace.denied
  const resetStatus = workspace.reset.data?.status
  const resetPublished = workspace.reset.data?.resources?.[0]?.status === 'succeeded'
  const resetBlocked =
    ['running', 'partial'].includes(resetStatus ?? '') ||
    (resetStatus === 'conflicted' && !resetPublished)
  const resetBlockReason = !resetBlocked
    ? null
    : resetStatus === 'running'
      ? 'A label removal is running. Batch judging resumes when it finishes.'
      : resetStatus === 'partial'
        ? 'A label removal stopped before finishing. Retry it under Remove AI-applied labels.'
        : 'A label removal was not applied because labels changed after its preview. Create a new preview under Remove AI-applied labels.'
  const approved = workspace.approvals.data?.items.find(
    (approval) => approval.id === approvalId && approval.current,
  )
  const approvalJob =
    approved &&
    (jobs.find((job) => job.id === approved.sampleJobId) ??
      (workspace.review.data?.id === approved.sampleJobId ? workspace.review.data : undefined))
  const effectiveMethod = approvalJob?.config.processMethod ?? method
  const effectiveViews = approvalJob?.config.views ?? views
  const launchable =
    canMutate &&
    targets.length > 0 &&
    !targetError &&
    !!approved &&
    !!approvalJob &&
    readiness.ready &&
    !busy &&
    !resetBlocked &&
    !workspace.inventory.error &&
    !workspace.approvals.error
  const pinInventory = () => setSnapshotId(workspace.inventory.data?.snapshotId)
  const submit = (mode: 'judge' | 'judge-and-label') => {
    if (!launchable) return
    void batch
      .submit({
        indices: targets,
        mode,
        approvalId,
        options: { processMethod: effectiveMethod, views: effectiveViews },
      })
      .catch(() => undefined)
  }
  const act = (kind: 'cancel' | 'retry', jobId: string) => {
    void workspace.act({ kind, jobId }).catch(() => undefined)
  }
  const references = Object.fromEntries(
    (samples.data ?? [])
      .filter((sample) => sample.reference && sampleIndices.includes(sample.index))
      .map((sample) => [sample.index, sample.reference]),
  ) as Record<number, JudgeSampleReference>
  const unusable = (samples.data ?? [])
    .filter((sample) => !sample.reference && sampleIndices.includes(sample.index))
    .map((sample) => sample.index)
  const samplesReady =
    canMutate &&
    sampleIndices.length > 0 &&
    !groundingError &&
    sampleIndices.every((index) => !!references[index]) &&
    readiness.ready &&
    !samples.error &&
    !samples.isFetching &&
    !busy &&
    !resetBlocked
  const review = workspace.review.data
  const blockReason = (missing: string | null) =>
    !enabled
      ? null
      : (resetBlockReason ??
        (busy ? 'Waiting for the current operation to finish.' : null) ??
        (!readiness.ready ? readiness.reason : null) ??
        missing)
  const sampleBlockReason = blockReason(
    groundingError
      ? 'Fix the grounding episode list.'
      : sampleIndices.length === 0
        ? 'Enter at least one grounding episode.'
        : samples.error
          ? 'Saved annotations could not be loaded.'
          : samples.isFetching
            ? 'Loading saved human annotations.'
            : unusable.length
              ? `Episodes ${formatEpisodeList(unusable)} need a saved task instruction and one SUCCESS, FAILURE or PARTIAL label, or a saved task-completeness rating.`
              : null,
  )
  const targetBlockReason = blockReason(
    targetError
      ? 'Fix the target episode list.'
      : targets.length === 0
        ? 'Enter at least one target episode.'
        : !approved || !approvalJob
          ? 'Evaluate the grounding episodes, review them and approve the configuration first, or select a current approval.'
          : workspace.inventory.error || workspace.approvals.error
            ? 'Refresh the dataset state before submitting.'
            : null,
  )
  const exceptions = review?.targets?.some((target) => target.comparison !== 'agreement')
  const evaluateSamples = () => {
    if (!samplesReady) return
    void batch
      .submit({
        mode: 'sample',
        indices: sampleIndices,
        samples: references,
        options: { processMethod: effectiveMethod, views: effectiveViews },
      })
      .then((job) => {
        setReviewId(job.id)
        setAcknowledgeExceptions(false)
      })
      .catch(() => undefined)
  }
  const previewReset = (includeUnlistedRuns = false) => {
    setPreview(null)
    setAcknowledgeReset(false)
    void workspace
      .act({ kind: 'preview-reset', includeUnlistedRuns })
      .then((result) => {
        if ('summary' in result) setPreview(result)
      })
      .catch(() => undefined)
  }

  return (
    <section aria-label="Dataset workspace" className="border-b">
      <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-2">
        <Button
          id="dataset-workspace-toggle"
          variant="ghost"
          aria-expanded={open}
          aria-controls="dataset-workspace-content"
          onClick={onToggle}
        >
          <ChevronDown aria-hidden="true" className="mr-2 size-4" /> Dataset workspace
        </Button>
        {canMutate && (
          <span className="text-muted-foreground text-sm" role="status">
            {jobs.filter((job) => job.status === 'queued' || job.status === 'running').length}{' '}
            active jobs
          </span>
        )}
      </div>
      <div id="dataset-workspace-content" hidden={!open} className="space-y-6 p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-lg font-semibold">Dataset analysis</h2>
          <div className="flex gap-2">
            {canMutate && (
              <Button
                variant="outline"
                size="icon"
                aria-label="Refresh dataset jobs"
                title="Refresh dataset jobs"
                onClick={() => {
                  setSnapshotId(undefined)
                  setPreview(null)
                  setAcknowledgeReset(false)
                  workspace.refresh()
                }}
              >
                <RefreshCw aria-hidden="true" className="size-4" />
              </Button>
            )}
            <Button variant="outline" onClick={onToggle}>
              <ChevronLeft aria-hidden="true" className="mr-1 size-4" />
              Return to episode
            </Button>
          </div>
        </div>
        {!enabled && (
          <p role={accessError ? 'alert' : 'status'}>
            <span>Judge operations are unavailable.</span> <span>{unavailableReason}</span>
          </p>
        )}
        {enabled && workspace.denied && <p role="alert">Dataset operation access is denied.</p>}
        {canMutate && (
          <>
            {(workspace.error || batch.error) && (
              <p role="alert">Dataset operation failed. Refresh saved state before retrying.</p>
            )}
            <section aria-label="Grounding episodes" className="space-y-3">
              <h3 className="font-semibold">Grounding episodes</h3>
              <p className="text-muted-foreground text-sm">
                Saved outcome labels and task instructions are used as baselines. Instructions from
                templates and dataset tasks are accepted without changing their source. Label
                baselines may include AI-applied labels and are not independent human validation.
              </p>
              {(workspace.annotated.isPending || workspace.inventory.isPending) && (
                <p role="status">Loading annotated episodes...</p>
              )}
              {workspace.inventory.error && (
                <p role="alert">Episode inventory could not be loaded.</p>
              )}
              {workspace.annotated.error && (
                <p role="alert">Annotated episodes could not be loaded.</p>
              )}
              {annotated && usable && (
                <div className="space-y-1 text-sm break-words">
                  <p>
                    {annotated.length} of {workspace.inventory.data?.total ?? '?'} episodes with
                    saved labels or annotations; {usable.length} usable for grounding
                    {usable.length ? `: ${formatEpisodeList(usable)}` : '.'}
                  </p>
                  {notUsable.length > 0 && <p>Not usable yet: {formatEpisodeList(notUsable)}.</p>}
                </div>
              )}
              <div className="grid max-w-xl gap-1 text-sm">
                <label htmlFor="grounding-episodes">Grounding episodes</label>
                <input
                  id="grounding-episodes"
                  value={groundingValue}
                  placeholder="0,2,4-8"
                  aria-invalid={!!groundingError}
                  aria-describedby={
                    groundingError ? 'grounding-hint grounding-error' : 'grounding-hint'
                  }
                  onChange={(event) => {
                    pinInventory()
                    setGroundingText(event.target.value)
                  }}
                  className="rounded border p-2"
                />
                <p id="grounding-hint" className="text-muted-foreground">
                  Comma-separated episode numbers or ranges, up to {MAX_GROUNDING} episodes. A
                  grounding episode needs a saved task instruction and one SUCCESS, FAILURE or
                  PARTIAL label, or a saved task-completeness rating. Instruction source is
                  unrestricted.
                </p>
                {groundingError && (
                  <p id="grounding-error" className="text-destructive">
                    {groundingError}
                  </p>
                )}
              </div>
              {samples.error && (
                <p role="alert">
                  Saved annotations could not be loaded. Refresh before evaluating.
                </p>
              )}
              <Button
                disabled={!enabled || !samplesReady}
                aria-describedby={sampleBlockReason ? 'sample-block-reason' : undefined}
                onClick={evaluateSamples}
              >
                <Play aria-hidden="true" className="mr-1 size-4" />
                Evaluate grounding episodes
              </Button>
              {sampleBlockReason && (
                <p id="sample-block-reason" className="text-muted-foreground text-sm">
                  {sampleBlockReason}
                </p>
              )}
              {review && (
                <div className="space-y-2">
                  <h4 className="font-medium">Job review: {review.status}</h4>
                  <ul>
                    {review.targets?.map((target) => (
                      <li key={target.episodeIndex}>
                        Episode {target.episodeIndex}: {target.comparison ?? target.status};{' '}
                        {target.sample?.baselineSource === 'label' ? 'saved label' : 'saved rating'}{' '}
                        {String(target.sample?.humanOutcome ?? 'unknown')}; judge{' '}
                        {String(target.result?.outcomeSuccess ?? 'inconclusive')}
                        {target.error && `; error: ${target.error}`}
                      </li>
                    ))}
                  </ul>
                  <label className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={acknowledgeExceptions}
                      onChange={(event) => setAcknowledgeExceptions(event.target.checked)}
                    />
                    I reviewed disagreements and inconclusive results
                  </label>
                  <Button
                    disabled={
                      !canMutate ||
                      busy ||
                      review.mode !== 'sample' ||
                      review.status !== 'succeeded' ||
                      (!!exceptions && !acknowledgeExceptions)
                    }
                    onClick={() =>
                      void workspace
                        .act({ kind: 'approve', jobId: review.id, acknowledgeExceptions })
                        .then((result) => {
                          if ('configRevision' in result) setApprovalId(result.id)
                        })
                        .catch(() => undefined)
                    }
                  >
                    Approve configuration
                  </Button>
                </div>
              )}
            </section>
            <section aria-label="Approved configuration" className="flex flex-wrap items-end gap-4">
              <label className="grid gap-1 text-sm">
                Process method
                <select
                  disabled={!!approved}
                  value={effectiveMethod}
                  onChange={(event) => {
                    setMethod(event.target.value)
                    setApprovalId('')
                  }}
                  className="rounded border p-2"
                >
                  <option value="gvl">Shuffle-and-rank</option>
                  <option value="chronological">Chronological</option>
                </select>
              </label>
              <label className="grid gap-1 text-sm">
                Configuration approval
                <select
                  value={approvalId}
                  disabled={!workspace.approvals.data?.items.length}
                  onChange={(event) => {
                    setApprovalId(event.target.value)
                    const approval = workspace.approvals.data?.items.find(
                      (item) => item.id === event.target.value,
                    )
                    if (approval) setReviewId(approval.sampleJobId)
                  }}
                  className="max-w-full rounded border p-2"
                >
                  <option value="">
                    {workspace.approvals.data?.items.length
                      ? 'Select approval'
                      : 'No approved configuration yet'}
                  </option>
                  {(workspace.approvals.data?.items ?? []).map((approval) => (
                    <option key={approval.id} value={approval.id} disabled={!approval.current}>
                      {approval.id}
                      {!approval.current ? ' (stale)' : ''}
                    </option>
                  ))}
                </select>
              </label>
              <fieldset className="basis-full space-y-1 text-sm" disabled={!!approved}>
                <legend>Camera views</legend>
                {availableCameras.length === 0 && <span>All available cameras</span>}
                <div className="flex flex-wrap gap-3">
                  {availableCameras.map((camera) => {
                    const selected = effectiveViews.length ? effectiveViews : availableCameras
                    return (
                      <label key={camera} className="flex items-center gap-1">
                        <input
                          type="checkbox"
                          checked={selected.includes(camera)}
                          disabled={selected.includes(camera) && selected.length === 1}
                          onChange={() =>
                            setViews(
                              selected.includes(camera)
                                ? selected.filter((value) => value !== camera)
                                : [...selected, camera],
                            )
                          }
                        />
                        {camera}
                      </label>
                    )
                  })}
                </div>
              </fieldset>
              {approvalJob && (
                <p className="basis-full text-sm break-all">
                  Approved configuration: {approvalJob.configRevision}; views:{' '}
                  {effectiveViews.join(', ') || 'all'}
                </p>
              )}
              <div className="grid max-w-xl basis-full gap-1 text-sm">
                <label htmlFor="target-episodes">Target episodes</label>
                <input
                  id="target-episodes"
                  value={targetText}
                  placeholder="All episodes not used for grounding"
                  aria-invalid={!!targetError}
                  aria-describedby={targetError ? 'target-hint target-error' : 'target-hint'}
                  onChange={(event) => {
                    pinInventory()
                    setTargetText(event.target.value)
                  }}
                  className="rounded border p-2"
                />
                <p id="target-hint" className="text-muted-foreground">
                  Comma-separated episode numbers or ranges, such as 0,2,4-8. Leave empty to judge
                  every episode not used for grounding.
                </p>
                {targetError && (
                  <p id="target-error" className="text-destructive">
                    {targetError}
                  </p>
                )}
                <p role="status">
                  {targetError ? 0 : targets.length} of {workspace.inventory.data?.total ?? 0}{' '}
                  episodes selected
                </p>
              </div>
              {workspace.approvals.error && (
                <p role="alert">Approval status is unavailable. Refresh before submitting.</p>
              )}
              <Button
                disabled={!launchable}
                aria-describedby={targetBlockReason ? 'target-block-reason' : undefined}
                onClick={() => submit('judge')}
              >
                <Play aria-hidden="true" className="mr-1 size-4" />
                Judge targets
              </Button>
              <Button
                variant="outline"
                disabled={!launchable}
                aria-describedby={targetBlockReason ? 'target-block-reason' : undefined}
                onClick={() => submit('judge-and-label')}
              >
                <Play aria-hidden="true" className="mr-1 size-4" />
                Judge and label targets
              </Button>
              {targetBlockReason && (
                <p id="target-block-reason" className="basis-full text-sm">
                  {targetBlockReason}
                </p>
              )}
            </section>
            <section aria-label="Durable jobs" className="space-y-3">
              <h3 className="font-semibold">Jobs</h3>
              {workspace.jobs.error && (
                <p role="status">Job refresh failed. Previously loaded jobs remain visible.</p>
              )}
              <ul className="divide-y">
                {jobs.map((job) => (
                  <li
                    key={job.id}
                    className="flex flex-wrap items-center justify-between gap-3 py-3"
                  >
                    <div className="min-w-0 text-sm">
                      <p className="break-all">
                        {job.id} · {job.status}
                      </p>
                      <p>
                        {job.judged} judged · {job.applied} applied · {job.errors} errors ·{' '}
                        {job.total} total
                      </p>
                    </div>
                    {job.mode === 'sample' && (
                      <Button
                        variant="outline"
                        onClick={() => {
                          setReviewId(job.id)
                          setAcknowledgeExceptions(false)
                        }}
                      >
                        Review samples
                      </Button>
                    )}
                    {job.mode !== 'sample' && (
                      <Button variant="outline" onClick={() => setReviewId(job.id)}>
                        Job details
                      </Button>
                    )}
                    {['queued', 'running'].includes(job.status) && (
                      <Button
                        variant="outline"
                        disabled={busy}
                        aria-label={`Cancel job ${job.id}`}
                        onClick={() => act('cancel', job.id)}
                      >
                        <Square aria-hidden="true" className="mr-1 size-3" />
                        Cancel
                      </Button>
                    )}
                    {['partial', 'failed', 'cancelled'].includes(job.status) && (
                      <Button
                        variant="outline"
                        disabled={busy}
                        aria-label={`Retry job ${job.id}`}
                        onClick={() => act('retry', job.id)}
                      >
                        <RefreshCw aria-hidden="true" className="mr-1 size-4" />
                        Retry
                      </Button>
                    )}
                  </li>
                ))}
              </ul>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  disabled={!jobOffset}
                  onClick={() => setJobOffset(Math.max(0, jobOffset - 25))}
                >
                  Previous jobs
                </Button>
                <Button
                  variant="outline"
                  disabled={jobOffset + 25 >= (workspace.jobs.data?.total ?? 0)}
                  onClick={() => setJobOffset(jobOffset + 25)}
                >
                  Next jobs
                </Button>
              </div>
            </section>
            <section aria-label="Remove AI-applied labels" className="space-y-3 border-t pt-4">
              <h3 className="font-semibold">Remove AI-applied labels</h3>
              <Button
                variant="outline"
                disabled={busy || !canMutate}
                onClick={() => previewReset()}
              >
                Preview label removal
              </Button>
              {preview && (
                <div className="space-y-3">
                  <dl className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
                    <div>
                      <dt>Removable fields</dt>
                      <dd>{preview.summary.removableFields}</dd>
                    </div>
                    <div>
                      <dt>Accepted unchanged</dt>
                      <dd>{preview.summary.acceptedUnchanged}</dd>
                    </div>
                    <div>
                      <dt>Human fields preserved</dt>
                      <dd>{preview.summary.preservedHuman}</dd>
                    </div>
                    <div>
                      <dt>Legacy unknown</dt>
                      <dd>{preview.summary.legacyUnknown}</dd>
                    </div>
                    <div>
                      <dt>Conflicts</dt>
                      <dd>{preview.summary.conflicts}</dd>
                    </div>
                    <div>
                      <dt>Episodes</dt>
                      <dd>{preview.summary.episodes}</dd>
                    </div>
                  </dl>
                  <WithdrawalFields fields={preview.summary.fields} completed={false} />
                  {(preview.summary.unlistedRunIds?.length ?? 0) > 0 && (
                    <div className="space-y-2 text-sm">
                      <p>
                        AI output from {preview.summary.unlistedRunIds!.length} run(s) is not in
                        this job history, for example after the job directory changed:{' '}
                        {preview.summary.unlistedRunIds!.join(', ')}.
                      </p>
                      <Button
                        variant="outline"
                        disabled={busy || !canMutate}
                        onClick={() => previewReset(true)}
                      >
                        Preview including missing runs
                      </Button>
                    </div>
                  )}
                  <label className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={acknowledgeReset}
                      onChange={(event) => setAcknowledgeReset(event.target.checked)}
                    />
                    I reviewed this removal preview
                  </label>
                  <Button
                    variant="destructive"
                    disabled={!canMutate || !acknowledgeReset || busy}
                    onClick={() =>
                      void workspace
                        .act({ kind: 'confirm-reset', previewId: preview.id })
                        .then(() => {
                          setPreview(null)
                          setAcknowledgeReset(false)
                        })
                        .catch(() => undefined)
                    }
                  >
                    Confirm label removal
                  </Button>
                </div>
              )}
              {resetStatus && <p role="status">Label removal: {resetStatus}</p>}
              {resetStatus === 'partial' && (
                <Button
                  variant="outline"
                  disabled={busy}
                  onClick={() => void workspace.act({ kind: 'retry-reset' }).catch(() => undefined)}
                >
                  Retry label removal
                </Button>
              )}
              {resetStatus === 'conflicted' && workspace.reset.data && (
                <div role="alert" className="space-y-2 text-sm">
                  <p>
                    {resetPublished
                      ? `Label removal finished with conflicts: ${workspace.reset.data.summary.removableFields} AI value(s) were removed and ${workspace.reset.data.summary.conflicts} were left unchanged.`
                      : 'Labels changed after the preview, so nothing was removed. Create a new preview.'}
                  </p>
                  {resetPublished && (
                    <WithdrawalFields
                      fields={workspace.reset.data.summary.fields.filter(
                        (field) => field.disposition === 'conflicts',
                      )}
                      completed
                    />
                  )}
                </div>
              )}
              {resetStatus === 'succeeded' && workspace.reset.data && !preview && (
                <WithdrawalFields fields={workspace.reset.data.summary.fields} completed />
              )}
            </section>
          </>
        )}
      </div>
    </section>
  )
}
