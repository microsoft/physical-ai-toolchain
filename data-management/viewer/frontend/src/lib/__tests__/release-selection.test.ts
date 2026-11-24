import { describe, expect, it } from 'vitest'

import {
  clearShownReleaseEpisodes,
  orderedReleaseEpisodes,
  selectShownReleaseEpisodes,
  summarizeReleaseSelection,
  toggleReleaseEpisode,
} from '../release-selection'

describe('toggleReleaseEpisode', () => {
  it('adds and removes one episode without mutating the previous selection', () => {
    const before = new Set([2])

    const added = toggleReleaseEpisode(before, 7)
    const removed = toggleReleaseEpisode(added, 2)

    expect([...added].sort((a, b) => a - b)).toEqual([2, 7])
    expect([...removed]).toEqual([7])
    expect([...before]).toEqual([2])
  })
})

describe('selectShownReleaseEpisodes and clearShownReleaseEpisodes', () => {
  it('keeps selections outside the shown page when selecting the page', () => {
    const selected = selectShownReleaseEpisodes(new Set([40]), [2, 3])

    expect([...selected].sort((a, b) => a - b)).toEqual([2, 3, 40])
  })

  it('clears only the shown episodes and keeps off-page selections', () => {
    const selected = clearShownReleaseEpisodes(new Set([2, 3, 40]), [2, 3])

    expect([...selected]).toEqual([40])
  })
})

describe('orderedReleaseEpisodes', () => {
  it('returns selected episodes in ascending index order regardless of insertion order', () => {
    expect(orderedReleaseEpisodes(new Set([7, 2, 40]))).toEqual([2, 7, 40])
  })
})

describe('summarizeReleaseSelection', () => {
  it('reports population, visible, selected, and selected-visible counts', () => {
    const summary = summarizeReleaseSelection(new Set([2, 40]), {
      population: 120,
      visible: [2, 3],
    })

    expect(summary).toEqual({ population: 120, visible: 2, selected: 2, selectedVisible: 1 })
  })
})
