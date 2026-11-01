export const MAX_EPISODE_LIST = 10000

export interface EpisodeListParse {
  indices: number[]
  error: string | null
}

/** Format sorted episode indices compactly, for example `0, 2, 4-8`. */
export function formatEpisodeList(indices: readonly number[]): string {
  const sorted = [...new Set(indices)].sort((first, second) => first - second)
  const parts: string[] = []
  for (let start = 0; start < sorted.length;) {
    let end = start
    while (end + 1 < sorted.length && sorted[end + 1] === sorted[end] + 1) end += 1
    if (end - start >= 2) parts.push(`${sorted[start]}-${sorted[end]}`)
    else for (let index = start; index <= end; index += 1) parts.push(String(sorted[index]))
    start = end + 1
  }
  return parts.join(', ')
}

/** Parse comma-separated episode numbers and inclusive ranges such as `0,2,4-8`. */
export function parseEpisodeList(text: string, known?: ReadonlySet<number>): EpisodeListParse {
  const indices = new Set<number>()
  for (const token of text
    .split(',')
    .map((part) => part.trim())
    .filter(Boolean)) {
    const match = /^(\d+)(?:\s*-\s*(\d+))?$/.exec(token)
    if (!match)
      return { indices: [], error: `"${token}" is not an episode number or a range like 4-8.` }
    const start = Number(match[1])
    const end = match[2] === undefined ? start : Number(match[2])
    if (end < start) return { indices: [], error: `Range ${token} must go from low to high.` }
    if (end - start + 1 + indices.size > MAX_EPISODE_LIST)
      return { indices: [], error: `Enter at most ${MAX_EPISODE_LIST} episodes.` }
    for (let index = start; index <= end; index += 1) indices.add(index)
  }
  const sorted = [...indices].sort((first, second) => first - second)
  const unknown = known ? sorted.filter((index) => !known.has(index)) : []
  if (unknown.length)
    return {
      indices: sorted,
      error: `Not in this dataset: ${formatEpisodeList(unknown.slice(0, 20))}${unknown.length > 20 ? ', ...' : ''}.`,
    }
  return { indices: sorted, error: null }
}
