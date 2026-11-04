import { useQuery } from '@tanstack/react-query'
import { SlidersHorizontal } from 'lucide-react'
import { useRef, useState } from 'react'

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
  search: string
  labelValues: string[]
  annotation: Tristate
  edits: Tristate
  labels: Tristate
  resources: Tristate
  /** Provenance origin to match, or `any`. */
  provenance: string
  judge: Tristate
}

const ANY_FILTERS: Filters = {
  search: '',
  labelValues: [],
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
    `episode ${item.episodeIndex} ${(item.labels ?? []).join(' ')}`
      .toLowerCase()
      .includes(filters.search.trim().toLowerCase()) &&
    (filters.labelValues.length === 0 ||
      filters.labelValues.some((label) => item.labels?.includes(label))) &&
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
    item.labels?.length ? `labels: ${item.labels.join(', ')}` : null,
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
        <ul className="list-disc pl-5 text-sm [overflow-wrap:anywhere]">
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
  const [filtersExpanded, setFiltersExpanded] = useState(false)
  const [reviewSelected, setReviewSelected] = useState(false)
  const selectedOnlyRef = useRef<HTMLButtonElement>(null)
  const episodeCheckboxes = useRef(new Map<number, HTMLButtonElement>())
  const [identity, setIdentity] = useState<DraftIdentity>(createDraftIdentity)
  const [review, setReview] = useState<ReviewState | null>(null)
  const [notice, setNotice] = useState<Notice | null>(null)
  const [ambiguous, setAmbiguous] = useState<PendingSubmit | null>(null)
  const [previewing, setPreviewing] = useState(false)
  const [submitting, setSubmitting] = useState(false)

  const itemByIndex = new Map(items.map((item) => [item.episodeIndex, item]))
  const availableRows = [...itemByIndex.values()]
  const liveSelected = selected.filter((index) => itemByIndex.has(index))
  const selectedRows = liveSelected.flatMap((index) => {
    const item = itemByIndex.get(index)
    return item ? [item] : []
  })
  const orderByIndex = new Map(liveSelected.map((index, position) => [index, position + 1]))
  const shownRows = (reviewSelected ? selectedRows : availableRows).filter((item) =>
    matchesFilters(item, filters),
  )
  const shownSelectedCount = shownRows.filter((item) => orderByIndex.has(item.episodeIndex)).length
  const labelCounts = new Map<string, number>()
  for (const item of availableRows) {
    for (const label of new Set(item.labels ?? [])) {
      labelCounts.set(label, (labelCounts.get(label) ?? 0) + 1)
    }
  }
  const labelOptions = [...labelCounts].sort(([left], [right]) => left.localeCompare(right))
  const activeFilterCount =
    Number(filters.search.trim() !== '') +
    filters.labelValues.length +
    [
      filters.annotation,
      filters.edits,
      filters.labels,
      filters.resources,
      filters.provenance,
      filters.judge,
    ].filter((value) => value !== 'any').length
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

  function describeActionStatus(): string {
    if (ambiguous) return 'Submission outcome unknown.'
    if (submitting) return 'Submitting release...'
    if (previewing) return 'Preparing preview...'
    if (disabled) return 'Release creation is unavailable.'
    if (!inventoryReady) return 'Waiting for source inventory.'
    if (liveSelected.length === 0) return 'No episodes selected.'
    if (!readiness.ready)
      return `Not ready: ${readiness.reason ?? 'saved episode context is loading.'}`
    if (!destination) return 'Destination required.'
    if (!name.trim()) return 'Release name required.'
    if (!currentReview) return 'Preview required before submission.'
    if (blocking.length > 0) return 'Preview has blocking issues.'
    if (!finalized) return 'Finalization declaration required.'
    if (requiresProcessingAck && !currentReview.processingAcknowledged)
      return 'Processing acknowledgment required.'
    if (requiresLocalDiskAck && !currentReview.localDiskAcknowledged)
      return 'Local disk acknowledgment required.'
    return 'Ready to submit.'
  }

  function resetDraft() {
    setSelected([])
    setDestinationChoice('')
    setName('')
    setDescription('')
    setFinalized(false)
    setFilters(ANY_FILTERS)
    setFiltersExpanded(false)
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
        className="flex h-[92dvh] max-h-[980px] w-[calc(100%-2rem)] max-w-[1120px] flex-col overflow-hidden p-4 sm:p-6"
        onCloseAutoFocus={onCloseAutoFocus}
      >
        <DialogHeader className="shrink-0">
          <DialogTitle>Release curated dataset</DialogTitle>
          <DialogDescription>
            Select source episodes, preview the release, then submit. Target: {RELEASE_TARGET}{' '}
            (fixed).
          </DialogDescription>
        </DialogHeader>

        <fieldset
          disabled={formLocked}
          className="m-0 grid min-h-0 min-w-0 flex-1 grid-cols-1 content-start gap-4 overflow-y-auto border-0 p-1.5"
        >
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
                rows={2}
                className="min-h-16"
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

          <section aria-labelledby="release-selection-heading" className="grid min-w-0 gap-3">
            <div className="flex items-center justify-between gap-3">
              <h3 id="release-selection-heading" className="text-sm font-medium">
                Episodes
              </h3>
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="sm:hidden"
                aria-expanded={filtersExpanded}
                aria-controls="release-episode-filters"
                onClick={() => setFiltersExpanded((previous) => !previous)}
              >
                <SlidersHorizontal className="mr-2 size-4" aria-hidden="true" />
                Filters{activeFilterCount > 0 ? ` (${activeFilterCount})` : ''}
              </Button>
            </div>

            <div className="grid min-w-0 gap-4 sm:grid-cols-[180px_minmax(0,1fr)] lg:grid-cols-[220px_minmax(0,1fr)]">
              <div
                id="release-episode-filters"
                className={`${filtersExpanded ? 'grid' : 'hidden'} min-w-0 content-start gap-3 sm:grid`}
              >
                <div className="grid gap-2">
                  <Label htmlFor="release-search">Search episodes</Label>
                  <Input
                    id="release-search"
                    type="search"
                    value={filters.search}
                    onChange={(event) =>
                      setFilters((previous) => ({ ...previous, search: event.target.value }))
                    }
                  />
                </div>
                <fieldset className="grid min-w-0 gap-2">
                  <legend className="mb-2 text-sm font-medium">Match any label</legend>
                  <div className="grid max-h-40 gap-2 overflow-y-auto p-1">
                    {labelOptions.map(([label, count], position) => (
                      <div key={label} className="flex items-start gap-2">
                        <Checkbox
                          id={`release-label-${position}`}
                          checked={filters.labelValues.includes(label)}
                          onCheckedChange={(checked) =>
                            setFilters((previous) => ({
                              ...previous,
                              labelValues:
                                checked === true
                                  ? [...previous.labelValues, label]
                                  : previous.labelValues.filter((value) => value !== label),
                            }))
                          }
                        />
                        <Label
                          htmlFor={`release-label-${position}`}
                          className="min-w-0 leading-5 [overflow-wrap:anywhere]"
                        >
                          {label} ({count})
                        </Label>
                      </div>
                    ))}
                    {labelOptions.length === 0 && (
                      <p className="text-muted-foreground text-sm">No saved labels.</p>
                    )}
                  </div>
                </fieldset>
                <details>
                  <summary className="cursor-pointer text-sm font-medium">More filters</summary>
                  <div className="mt-3 grid gap-3">
                    <TristateSelect
                      id="filter-annotation"
                      label="Annotation"
                      value={filters.annotation}
                      onChange={(value) =>
                        setFilters((previous) => ({ ...previous, annotation: value }))
                      }
                    />
                    <TristateSelect
                      id="filter-edits"
                      label="Edits"
                      value={filters.edits}
                      onChange={(value) =>
                        setFilters((previous) => ({ ...previous, edits: value }))
                      }
                    />
                    <TristateSelect
                      id="filter-labels"
                      label="Labels"
                      value={filters.labels}
                      onChange={(value) =>
                        setFilters((previous) => ({ ...previous, labels: value }))
                      }
                    />
                    <TristateSelect
                      id="filter-resources"
                      label="Resources"
                      value={filters.resources}
                      onChange={(value) =>
                        setFilters((previous) => ({ ...previous, resources: value }))
                      }
                    />
                    <div className="grid gap-1">
                      <Label htmlFor="filter-provenance">Provenance origin</Label>
                      <select
                        id="filter-provenance"
                        value={filters.provenance}
                        onChange={(event) =>
                          setFilters((previous) => ({
                            ...previous,
                            provenance: event.target.value,
                          }))
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
                      onChange={(value) =>
                        setFilters((previous) => ({ ...previous, judge: value }))
                      }
                    />
                  </div>
                </details>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => setFilters(ANY_FILTERS)}
                >
                  Reset filters
                </Button>
              </div>

              <div className="grid min-w-0 content-start gap-3">
                <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
                  <div className="flex items-center gap-2">
                    <Checkbox
                      id="release-selected-only"
                      ref={selectedOnlyRef}
                      checked={reviewSelected}
                      onCheckedChange={(checked) => setReviewSelected(checked === true)}
                    />
                    <Label htmlFor="release-selected-only">Selected only</Label>
                  </div>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={!inventoryReady}
                    onClick={() => appendSelection(availableRows.map((item) => item.episodeIndex))}
                  >
                    Select all
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={liveSelected.length === 0}
                    onClick={() => setSelected([])}
                  >
                    Clear selection
                  </Button>
                </div>
                <p
                  id="release-selection-status"
                  aria-live="polite"
                  aria-atomic="true"
                  className="text-muted-foreground text-sm"
                >
                  {liveSelected.length} of {availableRows.length}
                  {!inventoryReady ? ' loaded' : ''} selected; {shownRows.length} shown;{' '}
                  {liveSelected.length - shownSelectedCount} selected outside this view.
                  {shownRows.length === 0 && (
                    <span className="block">
                      {reviewSelected
                        ? 'No selected episodes match these filters.'
                        : 'No episodes match these filters.'}
                    </span>
                  )}
                </p>

                {!inventoryReady && (
                  <p className="text-muted-foreground text-sm">
                    The full source inventory is still loading. Select all and release are disabled.
                  </p>
                )}

                <div className="overflow-hidden rounded-md border">
                  <div className="bg-muted/40 flex items-center gap-3 border-b px-3 py-3">
                    <Checkbox
                      id="release-select-shown"
                      disabled={shownRows.length === 0}
                      checked={
                        shownSelectedCount === 0
                          ? false
                          : shownSelectedCount === shownRows.length
                            ? true
                            : 'indeterminate'
                      }
                      onCheckedChange={(checked) => {
                        const shownIndices = new Set(shownRows.map((item) => item.episodeIndex))
                        if (checked === true) appendSelection([...shownIndices])
                        else
                          setSelected((previous) =>
                            previous.filter((index) => !shownIndices.has(index)),
                          )
                      }}
                    />
                    <Label htmlFor="release-select-shown">Select shown</Label>
                  </div>
                  <ol
                    aria-label="Episodes"
                    className="divide-border max-h-[420px] min-h-32 divide-y overflow-y-auto"
                  >
                    {shownRows.map((item, rowIndex) => {
                      const order = orderByIndex.get(item.episodeIndex)
                      const checkboxId = `release-episode-${item.episodeIndex}`
                      return (
                        <li
                          key={item.episodeIndex}
                          className={`grid grid-cols-[auto_minmax(0,1fr)_auto] items-start gap-3 px-3 py-3 ${order !== undefined ? 'bg-accent/50' : ''}`}
                        >
                          <Checkbox
                            id={checkboxId}
                            ref={(element) => {
                              if (element) episodeCheckboxes.current.set(item.episodeIndex, element)
                              else episodeCheckboxes.current.delete(item.episodeIndex)
                            }}
                            checked={order !== undefined}
                            onCheckedChange={() => {
                              if (reviewSelected && order !== undefined) {
                                const neighbor = shownRows[rowIndex + 1] ?? shownRows[rowIndex - 1]
                                const target = neighbor
                                  ? episodeCheckboxes.current.get(neighbor.episodeIndex)
                                  : selectedOnlyRef.current
                                target?.focus()
                              }
                              toggleIndex(item.episodeIndex)
                            }}
                          />
                          <div className="grid min-w-0 gap-1">
                            <Label htmlFor={checkboxId} className="leading-4">
                              Episode {item.episodeIndex}
                            </Label>
                            <span className="text-muted-foreground text-xs [overflow-wrap:anywhere]">
                              {describeRow(item)}
                            </span>
                          </div>
                          {order !== undefined && (
                            <span className="text-sm font-medium tabular-nums">#{order}</span>
                          )}
                        </li>
                      )
                    })}
                  </ol>
                </div>

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
              </div>
            </div>
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

        <p
          id="release-action-status"
          role="status"
          className="text-muted-foreground shrink-0 text-sm"
        >
          {describeActionStatus()}
        </p>
        <DialogFooter
          className={
            ambiguous
              ? 'shrink-0 gap-2'
              : 'shrink-0 flex-row flex-wrap justify-end gap-2 sm:space-x-0'
          }
        >
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
                variant={currentReview ? 'outline' : 'default'}
                aria-label={previewing ? 'Previewing...' : 'Preview release'}
                aria-describedby="release-action-status"
                onClick={() => void handlePreview()}
                disabled={!canPreview}
              >
                <span className="sm:hidden">{previewing ? 'Previewing...' : 'Preview'}</span>
                <span className="hidden sm:inline">
                  {previewing ? 'Previewing...' : 'Preview release'}
                </span>
              </Button>
              <Button
                variant={currentReview ? 'default' : 'outline'}
                aria-label={submitting ? 'Submitting...' : 'Submit release'}
                aria-describedby="release-action-status"
                onClick={handleSubmit}
                disabled={!canSubmit}
              >
                <span className="sm:hidden">{submitting ? 'Submitting...' : 'Submit'}</span>
                <span className="hidden sm:inline">
                  {submitting ? 'Submitting...' : 'Submit release'}
                </span>
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
