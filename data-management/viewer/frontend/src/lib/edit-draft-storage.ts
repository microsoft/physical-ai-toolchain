import {
  deleteMetadata,
  deleteMetadataByPrefixes,
  getMetadata,
  setMetadata,
} from '@/lib/offline-storage'
import type { EpisodeAnnotation, EpisodeData } from '@/types'
import type { EpisodeEditOperations, SavedEditBaseline } from '@/types/episode-edit'

const DRAFT_PREFIX = 'draft-v2'
const LEGACY_DRAFT_PREFIXES = ['edit-draft:', 'annotation-draft:', 'label-draft:']
const fallbackDraftStorage = new Map<string, unknown>()
const resourceWriteQueues = new Map<string, Promise<void>>()
let legacyDraftsPurged = false
let draftRevision = 0
const draftListeners = new Set<() => void>()

export function getDraftRevision(): number {
  return draftRevision
}

export function hasPendingDraftWrites(): boolean {
  return resourceWriteQueues.size > 0
}

export function subscribeDraftChanges(listener: () => void): () => void {
  draftListeners.add(listener)
  return () => {
    draftListeners.delete(listener)
  }
}

export type DraftResourceKind = 'annotation' | 'labels' | 'episode-edit'

export interface DraftSource {
  sourceId: string
  sourceRevision: string
}

export function episodeDraftSource(episode: EpisodeData | null): DraftSource | null {
  return episode?.sourceId && episode.sourceRevision
    ? { sourceId: episode.sourceId, sourceRevision: episode.sourceRevision }
    : null
}

export function sameDraftSource(
  first: DraftSource | null | undefined,
  second: DraftSource | null | undefined,
): boolean {
  return (
    !!first &&
    !!second &&
    first.sourceId === second.sourceId &&
    first.sourceRevision === second.sourceRevision
  )
}

export interface DraftResource {
  kind: DraftResourceKind
  datasetId: string
  episodeIndex?: number
}

export interface DraftEnvelope<TBaseline, TDraft> {
  sourceScopes?: Record<number, DraftSource>
  schemaVersion: 2
  principalScopeId: string
  resource: DraftResource
  baseEtag: string | null
  baseline: TBaseline
  draft: TDraft
  generation: number
  updatedAt: string
}

export type DraftEnvelopeInput<TBaseline, TDraft> = Omit<
  DraftEnvelope<TBaseline, TDraft>,
  'schemaVersion' | 'updatedAt'
>

export interface PersistedAnnotationDraft {
  sourceBinding?: DraftSource | null
  draft: EpisodeAnnotation
  baseline: EpisodeAnnotation
  baseEtag: string | null
}

export interface PersistedLabelDraft {
  sourceScopes?: Record<number, DraftSource>
  availableLabels: string[]
  episodeLabels: Record<number, string[]>
  savedEpisodeLabels: Record<number, string[]>
  baseEtag: string | null
}

export type ConflictResolution = 'local' | 'server'

export function mergeThreeWay<T>(
  baseline: T,
  local: T,
  server: T,
  resolution: ConflictResolution,
): T {
  if (Object.is(local, baseline)) return structuredClone(server)
  if (Object.is(server, baseline) || Object.is(local, server)) return structuredClone(local)
  if (
    baseline === null ||
    local === null ||
    server === null ||
    typeof baseline !== 'object' ||
    typeof local !== 'object' ||
    typeof server !== 'object' ||
    Array.isArray(baseline) ||
    Array.isArray(local) ||
    Array.isArray(server)
  ) {
    return structuredClone(resolution === 'local' ? local : server)
  }

  const keys = new Set([
    ...Object.keys(baseline as object),
    ...Object.keys(local as object),
    ...Object.keys(server as object),
  ])
  const merged: Record<string, unknown> = {}
  for (const key of keys) {
    merged[key] = mergeThreeWay(
      (baseline as Record<string, unknown>)[key],
      (local as Record<string, unknown>)[key],
      (server as Record<string, unknown>)[key],
      resolution,
    )
  }
  return merged as T
}

export function mergeLabelSets(baseline: string[], local: string[], server: string[]): string[] {
  const baselineSet = new Set(baseline)
  const localSet = new Set(local)
  const removedLocally = new Set(baseline.filter((label) => !localSet.has(label)))
  const addedLocally = local.filter((label) => !baselineSet.has(label))
  return [
    ...server.filter((label) => !removedLocally.has(label)),
    ...addedLocally.filter((label) => !server.includes(label)),
  ]
}

interface LabelDraftState {
  availableLabels: string[]
  episodeLabels: Record<number, string[]>
}

function getPersistedDraftKey(principalScopeId: string, resource: DraftResource): string {
  return [
    DRAFT_PREFIX,
    principalScopeId,
    resource.kind,
    resource.datasetId,
    resource.episodeIndex ?? 'dataset',
  ].join(':')
}

async function purgeLegacyDrafts(): Promise<void> {
  if (legacyDraftsPurged) return
  legacyDraftsPurged = true
  for (const key of fallbackDraftStorage.keys()) {
    if (LEGACY_DRAFT_PREFIXES.some((prefix) => key.startsWith(prefix))) {
      fallbackDraftStorage.delete(key)
    }
  }
  if (typeof indexedDB !== 'undefined') {
    await deleteMetadataByPrefixes(LEGACY_DRAFT_PREFIXES)
  }
}

async function runForResource<T>(key: string, operation: () => Promise<T>): Promise<T> {
  const previous = resourceWriteQueues.get(key) ?? Promise.resolve()
  let result!: T
  const current = previous.then(async () => {
    result = await operation()
  })
  const settled = current.then(
    () => undefined,
    () => undefined,
  )
  resourceWriteQueues.set(key, settled)
  try {
    await current
  } finally {
    if (resourceWriteQueues.get(key) === settled) resourceWriteQueues.delete(key)
    draftRevision += 1
    draftListeners.forEach((listener) => listener())
  }
  return result
}

async function readEnvelope<TBaseline, TDraft>(
  key: string,
  principalScopeId: string,
  resource: DraftResource,
): Promise<DraftEnvelope<TBaseline, TDraft> | undefined> {
  const value =
    typeof indexedDB === 'undefined'
      ? fallbackDraftStorage.get(key)
      : await getMetadata<unknown>(key)
  if (!value || typeof value !== 'object') return undefined
  const envelope = value as DraftEnvelope<TBaseline, TDraft>
  if (
    envelope.schemaVersion !== 2 ||
    envelope.principalScopeId !== principalScopeId ||
    envelope.resource.kind !== resource.kind ||
    envelope.resource.datasetId !== resource.datasetId ||
    envelope.resource.episodeIndex !== resource.episodeIndex
  ) {
    return undefined
  }
  return envelope
}

export async function loadPersistedDraftEnvelope<TBaseline, TDraft>(
  principalScopeId: string,
  resource: DraftResource,
): Promise<DraftEnvelope<TBaseline, TDraft> | undefined> {
  await purgeLegacyDrafts()
  const key = getPersistedDraftKey(principalScopeId, resource)
  await resourceWriteQueues.get(key)
  return readEnvelope<TBaseline, TDraft>(key, principalScopeId, resource)
}

export async function persistDraftEnvelope<TBaseline, TDraft>(
  input: DraftEnvelopeInput<TBaseline, TDraft>,
): Promise<boolean> {
  await purgeLegacyDrafts()
  const key = getPersistedDraftKey(input.principalScopeId, input.resource)
  return runForResource(key, async () => {
    const current = await readEnvelope<TBaseline, TDraft>(
      key,
      input.principalScopeId,
      input.resource,
    )
    if (current && input.generation <= current.generation) return false
    const envelope: DraftEnvelope<TBaseline, TDraft> = {
      ...input,
      schemaVersion: 2,
      updatedAt: new Date().toISOString(),
    }
    if (typeof indexedDB === 'undefined') fallbackDraftStorage.set(key, envelope)
    else await setMetadata(key, envelope)
    return true
  })
}

async function persistNextEnvelope<TBaseline, TDraft>(
  principalScopeId: string,
  resource: DraftResource,
  baseEtag: string | null,
  baseline: TBaseline,
  draft: TDraft | null,
  sourceScopes?: Record<number, DraftSource>,
): Promise<void> {
  await purgeLegacyDrafts()
  const key = getPersistedDraftKey(principalScopeId, resource)
  await runForResource(key, async () => {
    const current = await readEnvelope<TBaseline, TDraft>(key, principalScopeId, resource)
    if (draft === null) {
      if (typeof indexedDB === 'undefined') fallbackDraftStorage.delete(key)
      else await deleteMetadata(key)
      return
    }
    const envelope: DraftEnvelope<TBaseline, TDraft> = {
      sourceScopes,
      schemaVersion: 2,
      principalScopeId,
      resource,
      baseEtag,
      baseline,
      draft,
      generation: (current?.generation ?? 0) + 1,
      updatedAt: new Date().toISOString(),
    }
    if (typeof indexedDB === 'undefined') fallbackDraftStorage.set(key, envelope)
    else await setMetadata(key, envelope)
  })
}

export function loadPersistedEditDraft(
  datasetId: string,
  episodeIndex: number,
  principalScopeId: string,
): Promise<DraftEnvelope<SavedEditBaseline | null, EpisodeEditOperations> | undefined> {
  return loadPersistedDraftEnvelope(principalScopeId, {
    kind: 'episode-edit',
    datasetId,
    episodeIndex,
  })
}

export function persistEditDraft(
  datasetId: string,
  episodeIndex: number,
  principalScopeId: string,
  operations: EpisodeEditOperations | null,
  baseline: SavedEditBaseline | null = null,
): Promise<void> {
  return persistNextEnvelope(
    principalScopeId,
    { kind: 'episode-edit', datasetId, episodeIndex },
    baseline?.etag ?? null,
    baseline,
    operations,
  )
}

export function loadPersistedAnnotationDraft(
  datasetId: string,
  episodeIndex: number,
  principalScopeId: string,
): Promise<DraftEnvelope<EpisodeAnnotation, EpisodeAnnotation> | undefined> {
  return loadPersistedDraftEnvelope(principalScopeId, {
    kind: 'annotation',
    datasetId,
    episodeIndex,
  })
}

export function persistAnnotationDraft(
  datasetId: string,
  episodeIndex: number,
  principalScopeId: string,
  draft: PersistedAnnotationDraft | null,
): Promise<void> {
  return persistNextEnvelope(
    principalScopeId,
    { kind: 'annotation', datasetId, episodeIndex },
    draft?.baseEtag ?? null,
    draft?.baseline ?? (null as never),
    draft?.draft ?? null,
    draft?.sourceBinding ? { [episodeIndex]: draft.sourceBinding } : undefined,
  )
}

export function loadPersistedLabelDraft(
  datasetId: string,
  principalScopeId: string,
): Promise<DraftEnvelope<LabelDraftState, LabelDraftState> | undefined> {
  return loadPersistedDraftEnvelope(principalScopeId, { kind: 'labels', datasetId })
}

export function persistLabelDraft(
  datasetId: string,
  principalScopeId: string,
  value: PersistedLabelDraft | null,
): Promise<void> {
  const baseline = value
    ? { availableLabels: value.availableLabels, episodeLabels: value.savedEpisodeLabels }
    : (null as never)
  const draft = value
    ? { availableLabels: value.availableLabels, episodeLabels: value.episodeLabels }
    : null
  return persistNextEnvelope(
    principalScopeId,
    { kind: 'labels', datasetId },
    value?.baseEtag ?? null,
    baseline,
    draft,
    value?.sourceScopes,
  )
}

export async function clearPersistedEditDraftsForTests(): Promise<void> {
  await Promise.resolve()
  while (resourceWriteQueues.size > 0) {
    await Promise.all(resourceWriteQueues.values())
  }
  fallbackDraftStorage.clear()
  if (typeof indexedDB !== 'undefined') {
    await deleteMetadataByPrefixes([DRAFT_PREFIX, ...LEGACY_DRAFT_PREFIXES])
  }
  resourceWriteQueues.clear()
  legacyDraftsPurged = false
}
