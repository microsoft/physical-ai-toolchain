import { deleteMetadata, getMetadata, setMetadata } from '@/lib/offline-storage'
import type { EpisodeEditOperations } from '@/types/episode-edit'

const EDIT_DRAFT_PREFIX = 'edit-draft'
const fallbackDraftStorage = new Map<string, EpisodeEditOperations>()

function getPersistedDraftKey(datasetId: string, episodeIndex: number) {
  return `${EDIT_DRAFT_PREFIX}:${datasetId}:${episodeIndex}`
}

export async function loadPersistedEditDraft(
  datasetId: string,
  episodeIndex: number,
): Promise<EpisodeEditOperations | undefined> {
  const key = getPersistedDraftKey(datasetId, episodeIndex)

  if (typeof indexedDB === 'undefined') {
    return fallbackDraftStorage.get(key)
  }

  return getMetadata<EpisodeEditOperations>(key)
}

export async function persistEditDraft(
  datasetId: string,
  episodeIndex: number,
  operations: EpisodeEditOperations | null,
): Promise<void> {
  const key = getPersistedDraftKey(datasetId, episodeIndex)

  if (typeof indexedDB === 'undefined') {
    if (!operations) {
      fallbackDraftStorage.delete(key)
      return
    }

    fallbackDraftStorage.set(key, operations)
    return
  }

  if (!operations) {
    await deleteMetadata(key)
    return
  }

  await setMetadata(key, operations)
}

export async function clearPersistedEditDraftsForTests(): Promise<void> {
  fallbackDraftStorage.clear()
}
