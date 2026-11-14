/**
 * Multi-page episode selection for curated dataset releases.
 *
 * Selections are sets of source episode indices that persist across pages and filters.
 * Every helper returns a new set and never mutates its input.
 */

/** Counts shown beside the release selection. */
export interface ReleaseSelectionSummary {
  /** Episodes in the full source population. */
  population: number
  /** Episodes on the currently visible page or filter. */
  visible: number
  /** Selected episodes across the whole population. */
  selected: number
  /** Selected episodes within the visible set. */
  selectedVisible: number
}

/**
 * Toggle one episode in the selection.
 *
 * @param selected - Current selection; not modified.
 * @param index - Source episode index to add or remove.
 * @returns A new selection with `index` flipped.
 */
export function toggleReleaseEpisode(selected: ReadonlySet<number>, index: number): Set<number> {
  const next = new Set(selected)
  if (next.has(index)) {
    next.delete(index)
  } else {
    next.add(index)
  }
  return next
}

/**
 * Add every visible episode to the selection, keeping selections outside the visible set.
 *
 * @param selected - Current selection; not modified.
 * @param shown - Episode indices on the visible page or filter.
 * @returns A new selection containing the previous selection plus `shown`.
 */
export function selectShownReleaseEpisodes(
  selected: ReadonlySet<number>,
  shown: readonly number[],
): Set<number> {
  return new Set([...selected, ...shown])
}

/**
 * Remove every visible episode from the selection, keeping selections outside the visible set.
 *
 * @param selected - Current selection; not modified.
 * @param shown - Episode indices on the visible page or filter.
 * @returns A new selection without any episode in `shown`.
 */
export function clearShownReleaseEpisodes(
  selected: ReadonlySet<number>,
  shown: readonly number[],
): Set<number> {
  const remove = new Set(shown)
  return new Set([...selected].filter((index) => !remove.has(index)))
}

/**
 * Selected episode indices in the canonical ascending release order.
 *
 * @param selected - Current selection.
 * @returns Sorted copy of the selected indices.
 */
export function orderedReleaseEpisodes(selected: ReadonlySet<number>): number[] {
  return [...selected].sort((left, right) => left - right)
}

/**
 * Summarize the selection against the full population and the visible set.
 *
 * @param selected - Current selection.
 * @param scope - Full population size and the episode indices currently visible.
 * @returns Counts for the selection summary.
 */
export function summarizeReleaseSelection(
  selected: ReadonlySet<number>,
  scope: { population: number; visible: readonly number[] },
): ReleaseSelectionSummary {
  return {
    population: scope.population,
    visible: scope.visible.length,
    selected: selected.size,
    selectedVisible: scope.visible.filter((index) => selected.has(index)).length,
  }
}
