import type { FrameInsertion } from '@/types/episode-edit'

/**
 * Compute effective index accounting for insertions and removals.
 */
export function getEffectiveIndex(
  originalIndex: number,
  insertedFrames: Map<number, FrameInsertion>,
  removedFrames: Set<number>,
): number {
  let offset = 0

  for (const afterIdx of insertedFrames.keys()) {
    if (afterIdx < originalIndex && !removedFrames.has(afterIdx)) {
      offset++
    }
  }

  for (const removedIdx of removedFrames) {
    if (removedIdx < originalIndex) {
      offset--
    }
  }

  return originalIndex + offset
}

/**
 * Convert effective index back to original frame index.
 */
export function getOriginalIndex(
  effectiveIndex: number,
  insertedFrames: Map<number, FrameInsertion>,
  removedFrames: Set<number>,
): number | null {
  const insertionPositions: number[] = []

  for (const afterIdx of insertedFrames.keys()) {
    if (!removedFrames.has(afterIdx)) {
      const effectivePos = getEffectiveIndex(afterIdx, insertedFrames, removedFrames) + 1
      insertionPositions.push(effectivePos)
    }
  }

  insertionPositions.sort((a, b) => a - b)

  if (insertionPositions.includes(effectiveIndex)) {
    return null
  }

  let insertionsBefore = 0
  for (const pos of insertionPositions) {
    if (pos < effectiveIndex) {
      insertionsBefore++
    }
  }

  const candidateOriginal = effectiveIndex - insertionsBefore
  let removedBefore = 0
  const sortedRemovals = Array.from(removedFrames).sort((a, b) => a - b)

  for (const removedIdx of sortedRemovals) {
    if (removedIdx <= candidateOriginal + removedBefore) {
      removedBefore++
    }
  }

  return candidateOriginal + removedBefore
}

/**
 * Get the total effective frame count after edits.
 */
export function getEffectiveFrameCount(
  originalCount: number,
  insertedFrames: Map<number, FrameInsertion>,
  removedFrames: Set<number>,
): number {
  let validInsertions = 0

  for (const afterIdx of insertedFrames.keys()) {
    if (!removedFrames.has(afterIdx) && afterIdx < originalCount - 1) {
      validInsertions++
    }
  }

  return originalCount - removedFrames.size + validInsertions
}
