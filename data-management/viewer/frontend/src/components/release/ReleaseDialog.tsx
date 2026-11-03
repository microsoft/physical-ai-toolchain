import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import {
  type ReleasePreview,
  type ReleaseRequest,
  ReleaseRequestError,
  type ReleaseSubmission,
} from '@/api/releases'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
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
import { Textarea } from '@/components/ui/textarea'
import { useEpisodeReadiness } from '@/hooks/use-episode-readiness'
import { fetchPrincipalContext } from '@/lib/principal-context'

/** Provenance origins recorded for one episode. */
export interface ReleaseProvenance {
  origins: string[]
}

/** Saved judge analysis for one episode. Freshness is shown as recorded, never inferred. */
export interface ReleaseJudge {
  status: string
  freshness: string
  confidence?: number | null
  outcome?: string | null
}

/** Episode row shown in the release selection list. */
export interface ReleaseItem {
  episodeIndex: number
  annotation?: boolean
  edits?: boolean
  labels?: string[] | null
  resources?: Record<string, unknown>[] | null
  provenance?: ReleaseProvenance | null
  judge?: ReleaseJudge | null
}

export interface ReleaseDialogProps {
  datasetId: string
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Restore focus to the owning workspace when the controlled dialog closes. */
  onCloseAutoFocus?: (event: Event) => void
  /** Full source population when `inventoryReady` is true; otherwise the loaded subset. */
  items: ReleaseItem[]
  /** Whether `items` holds the complete source inventory. Gates select-all and release. */
  inventoryReady: boolean
  /** Destination aliases from the release capabilities response. */
  destinations: string[]
  onPreview: (request: ReleaseRequest) => Promise<ReleasePreview>
  /** Rejects on failure. Transport errors and 5xx responses freeze the draft for retry. */
  onSubmit: (request: ReleaseRequest, submission: ReleaseSubmission) => Promise<void>
  disabled?: boolean
}

type ReleaseRequestDraft = ReleaseRequest & { description?: string }

type Tristate = 'any' | 'yes' | 'no'

interface Filters {
  annotation: Tristate
  edits: Tristate
  labels: Tristate
  resources: Tristate
  /** Provenance origin to match, or `any`. */
  provenance: string
  judge: Tristate
}

const ANY_FILTERS: Filters = {
  annotation: 'any',
  edits: 'any',
  labels: 'any',
  resources: 'any',
  provenance: 'any',
  judge: 'any',
}

interface DraftIdentity {
  packageId: string
  attemptId: string
}

interface ReviewState {
  key: string
  request: ReleaseRequestDraft
  preview: ReleasePreview
  processingAcknowledged: boolean
  localDiskAcknowledged: boolean
}

interface PendingSubmit {
  key: string
  request: ReleaseRequestDraft
  submission: ReleaseSubmission
}

interface Notice {
  key: string
  message: string
}

const RELEASE_TARGET = 'v3'

function createDraftIdentity(): DraftIdentity {
  return { packageId: crypto.randomUUID(), attemptId: crypto.randomUUID() }
}

function matchesTristate(value: boolean, filter: Tristate): boolean {
  return filter === 'any' || (filter === 'yes') === value
}

/** Cross-filter: an item must satisfy every active filter. */
function matchesFilters(item: ReleaseItem, filters: Filters): boolean {
  return (
    matchesTristate(item.annotation === true, filters.annotation) &&
    matchesTristate(item.edits === true, filters.edits) &&
    matchesTristate((item.labels?.length ?? 0) > 0, filters.labels) &&
    matchesTristate((item.resources?.length ?? 0) > 0, filters.resources) &&
    (filters.provenance === 'any' ||
      (item.provenance?.origins ?? []).includes(filters.provenance)) &&
    matchesTristate(item.judge != null, filters.judge)
  )
}

function toSnakeKey(key: string): string {
  return key
    .replace(/([a-z0-9])([A-Z])/g, '$1_$2')
    .replace(/[-\s]+/g, '_')
    .toLowerCase()
}

/** Recursively converts object keys to snake_case for the backend contract. */
function toSnakeCase(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(toSnakeCase)
  if (value !== null && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value).map(([key, entry]) => [toSnakeKey(key), toSnakeCase(entry)]),
    )
  }
  return value
}

function readList(summary: ReleasePreview['summary'], key: string): string[] {
  const value = summary[key]
  if (!Array.isArray(value)) return []
  return value.map((entry) => (typeof entry === 'string' ? entry : JSON.stringify(entry)))
}

/**
 * Destination kind from the preview summary. Null when the preview does not report one, which
 * makes local disk acknowledgment required.
 */
function readDestinationKind(summary: ReleasePreview['summary']): string | null {
  const value = summary.destinationKind
  return typeof value === 'string' ? value : null
}

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

function describeError(error: unknown): string {
  if (error instanceof ReleaseRequestError) return `Release request failed: ${error.code}.`
  return 'Release request failed.'
}

/** Keep the same request when the response does not establish that submission was refused. */
function isAmbiguousFailure(error: unknown): boolean {
  if (error instanceof ReleaseRequestError) return !error.isDefinitiveRejection
  return true
}

function describeJudge(judge: ReleaseJudge): string {
  const parts = [
    judge.status,
    `freshness ${judge.freshness}`,
    typeof judge.confidence === 'number' ? `confidence ${judge.confidence}` : null,
    judge.outcome ? `outcome ${judge.outcome}` : null,
  ]
  return `judge ${parts.filter((part): part is string => part !== null).join(', ')}`
}

function describeRow(item: ReleaseItem): string {
  const parts = [
    item.annotation ? 'annotated' : null,
    item.edits ? 'edited' : null,
    item.labels?.length ? `${item.labels.length} label(s)` : null,
    item.resources?.length ? `${item.resources.length} resource(s)` : null,
    item.provenance?.origins.length ? `origin: ${item.provenance.origins.join(', ')}` : null,
    item.judge ? describeJudge(item.judge) : null,
  ]
  return parts.filter((part): part is string => part !== null).join(', ')
}

/**
 * Counts provenance origins and judge analyses across rows. Freshness is reported as recorded.
 *
 * @param rows - Selected dialog rows.
 * @returns Provenance and judge summary lines.
 */
function summarizeEvidence(rows: ReleaseItem[]): { provenance: string; judge: string } {
  const origins = new Map<string, number>()
  const freshness = new Map<string, number>()
  let judged = 0
  for (const row of rows) {
    for (const origin of new Set(row.provenance?.origins ?? [])) {
      origins.set(origin, (origins.get(origin) ?? 0) + 1)
    }
    if (row.judge) {
      judged += 1
      freshness.set(row.judge.freshness, (freshness.get(row.judge.freshness) ?? 0) + 1)
    }
  }
  const originText = [...origins]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([origin, count]) => `${origin} ${count}`)
    .join(', ')
  const freshnessText = [...freshness]
    .map(([value, count]) => `; freshness ${value}: ${count}`)
    .join('')
  return {
    provenance: `Provenance origins: ${originText || 'none'}`,
    judge: `Judge analyses: ${judged}${freshnessText}`,
  }
}

function TristateSelect({
  id,
  label,
  value,
  onChange,
}: {
  id: string
  label: string
  value: Tristate
  onChange: (value: Tristate) => void
}) {
  return (
    <div className="grid gap-1">
      <Label htmlFor={id}>{label}</Label>
      <select
        id={id}
        value={value}
        onChange={(event) => onChange(event.target.value as Tristate)}
        className="border-input bg-background h-9 rounded-md border px-3 text-sm"
      >
        <option value="any">Any</option>
        <option value="yes">Yes</option>
        <option value="no">No</option>
      </select>
    </div>
  )
}

function SummaryList({ title, items, empty }: { title: string; items: string[]; empty: string }) {
  return (
    <div className="grid gap-1">
      <p className="text-sm font-medium">{title}</p>
      {items.length === 0 ? (
        <p className="text-muted-foreground text-sm">{empty}</p>
      ) : (
        <ul className="list-disc pl-5 text-sm">
          {items.map((item, index) => (
            <li key={`${index}-${item}`}>{item}</li>
          ))}
        </ul>
      )}
    </div>
  )
}

/**
 * Dialog for selecting source episodes, previewing a curated dataset release, and submitting it
 * with an idempotency key. Selection is ordered by click sequence.
 */
export function ReleaseDialog({
  datasetId,
  open,
  onOpenChange,
  onCloseAutoFocus,
  items,
  inventoryReady,
  destinations,
  onPreview,
  onSubmit,
  disabled = false,
}: ReleaseDialogProps) {
  const principal = useQuery({
    queryKey: ['auth', 'principal-context'],
    queryFn: fetchPrincipalContext,
    staleTime: Infinity,
  })
  const [selected, setSelected] = useState<number[]>([])
  const [destinationChoice, setDestinationChoice] = useState('')
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [finalized, setFinalized] = useState(false)
  const [filters, setFilters] = useState<Filters>(ANY_FILTERS)
  const [reviewSelected, setReviewSelected] = useState(false)
  const [identity, setIdentity] = useState<DraftIdentity>(createDraftIdentity)
  const [review, setReview] = useState<ReviewState | null>(null)
  const [notice, setNotice] = useState<Notice | null>(null)
  const [ambiguous, setAmbiguous] = useState<PendingSubmit | null>(null)
  const [previewing, setPreviewing] = useState(false)
  const [submitting, setSubmitting] = useState(false)

  const itemByIndex = new Map(items.map((item) => [item.episodeIndex, item]))
  const liveSelected = selected.filter((index) => itemByIndex.has(index))
  const selectedRows = liveSelected.flatMap((index) => {
    const item = itemByIndex.get(index)
    return item ? [item] : []
  })
  const orderByIndex = new Map(liveSelected.map((index, position) => [index, position + 1]))
  const filteredRows = items.filter((item) => matchesFilters(item, filters))
  const shownRows = reviewSelected ? selectedRows : filteredRows
  const provenanceOrigins = [
    ...new Set(items.flatMap((item) => item.provenance?.origins ?? [])),
  ].sort()
  const selectedEvidence = summarizeEvidence(selectedRows)
  const destination = destinations.includes(destinationChoice)
    ? destinationChoice
    : (destinations[0] ?? '')

  const readiness = useEpisodeReadiness(datasetId, liveSelected, open && liveSelected.length > 0)

  const request: ReleaseRequestDraft = {
    destination,
    packageId: identity.packageId,
    attemptId: identity.attemptId,
    datasetId,
    datasetName: name.trim(),
    episodeIndices: liveSelected,
    resources: selectedRows.flatMap((row) =>
      (row.resources ?? []).map((resource) => toSnakeCase(resource) as Record<string, unknown>),
    ),
    finalized,
    description: description.trim() || undefined,
  }

  // Any change to the body or to the principal or readiness invalidates the preview.
  const draftKey = JSON.stringify({
    request,
    ready: readiness.ready,
    scopeId: principal.data?.scopeId ?? null,
    authMode: principal.data?.authMode ?? null,
  })
  const currentReview = review?.key === draftKey ? review : null
  const contextKey = JSON.stringify([
    datasetId,
    principal.data?.scopeId,
    principal.data?.authMode,
    identity.attemptId,
  ])
  const currentNotice =
    notice?.key === draftKey || notice?.key === contextKey ? notice.message : null

  const summary = currentReview?.preview.summary
  const destinationKind = summary ? readDestinationKind(summary) : null
  const actions = summary ? readList(summary, 'actions') : []
  const blocking = summary ? readList(summary, 'blocking') : []
  const warnings = summary ? readList(summary, 'warnings') : []
  const requiresProcessingAck = actions.length > 0
  const requiresLocalDiskAck = destinationKind !== 'blob'
  const acknowledgmentsReady =
    (!requiresProcessingAck || currentReview?.processingAcknowledged === true) &&
    (!requiresLocalDiskAck || currentReview?.localDiskAcknowledged === true)

  const formLocked = disabled || ambiguous !== null || previewing || submitting
  const baseReady =
    !disabled && inventoryReady && readiness.ready && liveSelected.length > 0 && destination !== ''
  const canPreview = !formLocked && baseReady && name.trim() !== '' && !previewing
  const canSubmit =
    !formLocked &&
    baseReady &&
    finalized &&
    currentReview !== null &&
    blocking.length === 0 &&
    acknowledgmentsReady

  function resetDraft() {
    setSelected([])
    setDestinationChoice('')
    setName('')
    setDescription('')
    setFinalized(false)
    setFilters(ANY_FILTERS)
    setReviewSelected(false)
    setIdentity(createDraftIdentity())
    setReview(null)
    setNotice(null)
    setAmbiguous(null)
  }

  function handleOpenChange(next: boolean) {
    if (!next && (ambiguous !== null || submitting)) return
    onOpenChange(next)
  }

  function toggleIndex(index: number) {
    setSelected((previous) =>
      previous.includes(index) ? previous.filter((value) => value !== index) : [...previous, index],
    )
  }

  function appendSelection(indices: number[]) {
    setSelected((previous) => {
      const next = new Set(previous)
      indices.forEach((index) => next.add(index))
      return [...next]
    })
  }

  function updateAcknowledgment(
    field: 'processingAcknowledged' | 'localDiskAcknowledged',
    value: boolean,
  ) {
    setReview((previous) =>
      previous && previous.key === draftKey ? { ...previous, [field]: value } : previous,
    )
  }

  async function handlePreview() {
    const key = draftKey
    setPreviewing(true)
    setNotice(null)
    setReview(null)
    try {
      const preview = await onPreview(request)
      setReview({
        key,
        request,
        preview,
        processingAcknowledged: false,
        localDiskAcknowledged: false,
      })
    } catch (error) {
      setNotice({ key, message: describeError(error) })
    } finally {
      setPreviewing(false)
    }
  }

  async function submitPending(pending: PendingSubmit) {
    setSubmitting(true)
    setNotice(null)
    try {
      await onSubmit(pending.request, pending.submission)
      resetDraft()
      onOpenChange(false)
    } catch (error) {
      if (isAmbiguousFailure(error)) {
        setAmbiguous(pending)
      } else {
        setReview(null)
        setNotice({
          key:
            error instanceof ReleaseRequestError && error.code === 'stale_preview'
              ? contextKey
              : pending.key,
          message: describeError(error),
        })
      }
    } finally {
      setSubmitting(false)
    }
  }

  function handleSubmit() {
    if (!currentReview) return
    void submitPending({
      key: draftKey,
      request: currentReview.request,
      submission: {
        previewDigest: currentReview.preview.digest,
        processingDigest: currentReview.preview.summary.processingDigest ?? '',
        localDiskAcknowledged: requiresLocalDiskAck && currentReview.localDiskAcknowledged,
        idempotencyKey: crypto.randomUUID(),
      },
    })
  }

  function handleRetry() {
    if (ambiguous) void submitPending(ambiguous)
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent
        className="max-h-[90vh] overflow-y-auto sm:max-w-[720px]"
        onCloseAutoFocus={onCloseAutoFocus}
      >
        <DialogHeader>
          <DialogTitle>Release curated dataset</DialogTitle>
          <DialogDescription>
            Select source episodes, preview the release, then submit. Target: {RELEASE_TARGET}{' '}
            (fixed).
          </DialogDescription>
        </DialogHeader>

        <fieldset disabled={formLocked} className="m-0 grid min-w-0 gap-4 border-0 p-0">
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="grid gap-2">
              <Label htmlFor="release-name">Release name</Label>
              <Input
                id="release-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                required
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="release-destination">Destination</Label>
              <select
                id="release-destination"
                value={destination}
                onChange={(event) => setDestinationChoice(event.target.value)}
                disabled={destinations.length === 0}
                className="border-input bg-background h-9 rounded-md border px-3 text-sm"
              >
                {destinations.map((alias) => (
                  <option key={alias} value={alias}>
                    {alias}
                  </option>
                ))}
              </select>
            </div>
            <div className="grid gap-2 sm:col-span-2">
              <Label htmlFor="release-description">Description</Label>
              <Textarea
                id="release-description"
                value={description}
                onChange={(event) => setDescription(event.target.value)}
              />
            </div>
          </div>

          <div className="flex items-center gap-2">
            <Checkbox
              id="release-finalized"
              checked={finalized}
              onCheckedChange={(checked) => setFinalized(checked === true)}
            />
            <Label htmlFor="release-finalized">
              I declare the local source dataset is finalized and will not change during release.
            </Label>
          </div>

          <section aria-labelledby="release-selection-heading" className="grid gap-3">
            <h3 id="release-selection-heading" className="text-sm font-medium">
              Episodes
            </h3>

            <div className="grid gap-3 sm:grid-cols-3">
              <TristateSelect
                id="filter-annotation"
                label="Annotation"
                value={filters.annotation}
                onChange={(value) => setFilters((previous) => ({ ...previous, annotation: value }))}
              />
              <TristateSelect
                id="filter-edits"
                label="Edits"
                value={filters.edits}
                onChange={(value) => setFilters((previous) => ({ ...previous, edits: value }))}
              />
              <TristateSelect
                id="filter-labels"
                label="Labels"
                value={filters.labels}
                onChange={(value) => setFilters((previous) => ({ ...previous, labels: value }))}
              />
              <TristateSelect
                id="filter-resources"
                label="Resources"
                value={filters.resources}
                onChange={(value) => setFilters((previous) => ({ ...previous, resources: value }))}
              />
              <div className="grid gap-1">
                <Label htmlFor="filter-provenance">Provenance origin</Label>
                <select
                  id="filter-provenance"
                  value={filters.provenance}
                  onChange={(event) =>
                    setFilters((previous) => ({ ...previous, provenance: event.target.value }))
                  }
                  className="border-input bg-background h-9 rounded-md border px-3 text-sm"
                >
                  <option value="any">Any</option>
                  {provenanceOrigins.map((origin) => (
                    <option key={origin} value={origin}>
                      {origin}
                    </option>
                  ))}
                </select>
              </div>
              <TristateSelect
                id="filter-judge"
                label="Judge analysis"
                value={filters.judge}
                onChange={(value) => setFilters((previous) => ({ ...previous, judge: value }))}
              />
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={!inventoryReady}
                onClick={() => appendSelection(items.map((item) => item.episodeIndex))}
              >
                Select all
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={shownRows.length === 0}
                onClick={() => appendSelection(shownRows.map((item) => item.episodeIndex))}
              >
                Select shown
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={selected.length === 0}
                onClick={() => setSelected([])}
              >
                Clear selection
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                aria-pressed={reviewSelected}
                onClick={() => setReviewSelected((previous) => !previous)}
              >
                Review selected
              </Button>
              <span className="text-muted-foreground text-sm">
                Selected {liveSelected.length} of {items.length}; {shownRows.length} shown.
              </span>
            </div>

            {!inventoryReady && (
              <p className="text-muted-foreground text-sm">
                The full source inventory is still loading. Select all and release are disabled.
              </p>
            )}

            <ol
              aria-label="Episodes"
              className="divide-border max-h-64 divide-y overflow-y-auto rounded-md border"
            >
              {shownRows.map((item) => {
                const order = orderByIndex.get(item.episodeIndex)
                const checkboxId = `release-episode-${item.episodeIndex}`
                return (
                  <li key={item.episodeIndex} className="flex items-center gap-3 px-3 py-2">
                    <Checkbox
                      id={checkboxId}
                      checked={order !== undefined}
                      onCheckedChange={() => toggleIndex(item.episodeIndex)}
                    />
                    <Label htmlFor={checkboxId} className="flex-1">
                      Episode {item.episodeIndex}
                    </Label>
                    <span className="text-muted-foreground text-xs">{describeRow(item)}</span>
                    {order !== undefined && (
                      <span className="text-sm font-medium tabular-nums">#{order}</span>
                    )}
                  </li>
                )
              })}
            </ol>

            {liveSelected.length > 0 && (
              <div className="text-muted-foreground grid gap-1 text-sm">
                <p>{selectedEvidence.provenance}</p>
                <p>{selectedEvidence.judge}</p>
              </div>
            )}

            {liveSelected.length > 0 && !readiness.ready && (
              <p role="status" className="text-sm text-amber-700 dark:text-amber-400">
                {readiness.reason}
              </p>
            )}
          </section>

          {currentReview && (
            <section
              aria-labelledby="release-preview-heading"
              aria-live="polite"
              className="grid gap-3 rounded-md border p-3"
            >
              <h3 id="release-preview-heading" className="text-sm font-medium">
                Preview
              </h3>
              <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
                <dt className="text-muted-foreground">Episodes</dt>
                <dd>{currentReview.request.episodeIndices.length}</dd>
                <dt className="text-muted-foreground">Reserved bytes</dt>
                <dd>{formatBytes(currentReview.preview.reservedBytes)}</dd>
                {destinationKind === 'local' && (
                  <>
                    <dt className="text-muted-foreground">Backend available space</dt>
                    <dd>
                      {typeof summary?.availableBytes === 'number'
                        ? formatBytes(summary.availableBytes)
                        : 'Unknown'}
                    </dd>
                  </>
                )}
                <dt className="text-muted-foreground">Digest</dt>
                <dd className="font-mono text-xs break-all">{currentReview.preview.digest}</dd>
              </dl>
              <SummaryList title="Blocking" items={blocking} empty="None" />
              <SummaryList title="Warnings" items={warnings} empty="None" />
              <SummaryList title="Processing actions" items={actions} empty="None" />

              {requiresProcessingAck && (
                <div className="flex items-start gap-2">
                  <Checkbox
                    id="release-ack-processing"
                    checked={currentReview.processingAcknowledged}
                    onCheckedChange={(checked) =>
                      updateAcknowledgment('processingAcknowledged', checked === true)
                    }
                  />
                  <Label htmlFor="release-ack-processing">
                    I acknowledge the {actions.length} processing action(s) listed above.
                  </Label>
                </div>
              )}

              {requiresLocalDiskAck && (
                <div className="flex items-start gap-2">
                  <Checkbox
                    id="release-ack-local-disk"
                    checked={currentReview.localDiskAcknowledged}
                    onCheckedChange={(checked) =>
                      updateAcknowledgment('localDiskAcknowledged', checked === true)
                    }
                  />
                  <Label htmlFor="release-ack-local-disk">
                    I acknowledge local disk use of{' '}
                    {formatBytes(currentReview.preview.reservedBytes)}
                    {destinationKind === null ? ' (destination kind not reported)' : ''}.
                  </Label>
                </div>
              )}
            </section>
          )}
        </fieldset>

        {currentNotice && (
          <Alert variant="destructive">
            <AlertDescription>{currentNotice}</AlertDescription>
          </Alert>
        )}

        {ambiguous && (
          <Alert variant="destructive">
            <AlertDescription>
              The submission outcome is unknown. Retry sends the same request with the same
              idempotency key. Discarding may leave a duplicate job, so check existing release jobs
              first.
            </AlertDescription>
          </Alert>
        )}

        <DialogFooter>
          {ambiguous ? (
            <>
              <Button variant="outline" onClick={resetDraft} disabled={submitting}>
                Discard and start over
              </Button>
              <Button onClick={handleRetry} disabled={submitting}>
                Retry submission
              </Button>
            </>
          ) : (
            <>
              <Button variant="outline" onClick={() => handleOpenChange(false)}>
                Cancel
              </Button>
              <Button
                variant="secondary"
                onClick={() => void handlePreview()}
                disabled={!canPreview}
              >
                {previewing ? 'Previewing...' : 'Preview release'}
              </Button>
              <Button onClick={handleSubmit} disabled={!canSubmit}>
                {submitting ? 'Submitting...' : 'Submit release'}
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
