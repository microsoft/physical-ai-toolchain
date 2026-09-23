import { useEffect, useRef, useState } from 'react'

import {
  loadPersistedReviewDecisionDraft,
  persistReviewDecisionDraft,
} from '@/lib/edit-draft-storage'

export function useReviewReasonDraft(
  datasetId: string,
  episodeIndex: number,
  principalScopeId: string,
) {
  const [reasonCodes, setReasonCodes] = useState<string[]>([])
  const hydratedKeyRef = useRef<string | null>(null)
  const key = `${principalScopeId}:${datasetId}:${episodeIndex}`

  useEffect(() => {
    let active = true
    hydratedKeyRef.current = null
    setReasonCodes([])
    if (!datasetId || episodeIndex < 0 || !principalScopeId) return

    void loadPersistedReviewDecisionDraft(datasetId, episodeIndex, principalScopeId).then(
      (draft) => {
        if (!active) return
        setReasonCodes(draft?.draft.reasonCodes ?? [])
        hydratedKeyRef.current = key
      },
    )
    return () => {
      active = false
    }
  }, [datasetId, episodeIndex, key, principalScopeId])

  useEffect(() => {
    if (hydratedKeyRef.current !== key) return
    void persistReviewDecisionDraft(
      datasetId,
      episodeIndex,
      principalScopeId,
      reasonCodes.length > 0 ? { reasonCodes } : null,
    )
  }, [datasetId, episodeIndex, key, principalScopeId, reasonCodes])

  return { reasonCodes, setReasonCodes }
}
