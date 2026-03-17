import { beforeEach, describe, expect, it } from 'vitest'

import { useLabelStore } from '../label-store'

describe('useLabelStore', () => {
  beforeEach(() => {
    useLabelStore.getState().reset()
  })

  it('starts with default labels', () => {
    const state = useLabelStore.getState()
    expect(state.availableLabels).toEqual(['SUCCESS', 'FAILURE', 'PARTIAL'])
    expect(state.episodeLabels).toEqual({})
    expect(state.savedEpisodeLabels).toEqual({})
    expect(state.isLoaded).toBe(false)
    expect(state.filterLabels).toEqual([])
  })

  describe('setAvailableLabels', () => {
    it('replaces the available labels list', () => {
      useLabelStore.getState().setAvailableLabels(['A', 'B'])
      expect(useLabelStore.getState().availableLabels).toEqual(['A', 'B'])
    })
  })

  describe('addLabelOption', () => {
    it('adds a normalized uppercase label', () => {
      useLabelStore.getState().addLabelOption('  review  ')
      expect(useLabelStore.getState().availableLabels).toContain('REVIEW')
    })

    it('does not add duplicate labels', () => {
      useLabelStore.getState().addLabelOption('success')
      expect(useLabelStore.getState().availableLabels).toEqual(['SUCCESS', 'FAILURE', 'PARTIAL'])
    })

    it('ignores empty strings', () => {
      useLabelStore.getState().addLabelOption('  ')
      expect(useLabelStore.getState().availableLabels).toHaveLength(3)
    })
  })

  describe('removeLabelOption', () => {
    it('removes a label from options, episode assignments, and filters', () => {
      const store = useLabelStore.getState()

      store.setAvailableLabels(['SUCCESS', 'REVIEW'])
      store.setAllEpisodeLabels({ '1': ['SUCCESS', 'REVIEW'], '2': ['REVIEW'] })
      store.setEpisodeLabels(1, ['SUCCESS', 'REVIEW'])
      store.setEpisodeLabels(2, ['REVIEW'])
      store.setFilterLabels(['REVIEW'])

      store.removeLabelOption('review')

      const state = useLabelStore.getState()
      expect(state.availableLabels).toEqual(['SUCCESS'])
      expect(state.episodeLabels[1]).toEqual(['SUCCESS'])
      expect(state.episodeLabels[2]).toEqual([])
      expect(state.savedEpisodeLabels[1]).toEqual(['SUCCESS'])
      expect(state.savedEpisodeLabels[2]).toEqual([])
      expect(state.filterLabels).toEqual([])
    })
  })

  describe('setAllEpisodeLabels', () => {
    it('parses string keys to numeric indices', () => {
      useLabelStore
        .getState()
        .setAllEpisodeLabels({ '0': ['SUCCESS'], '5': ['FAILURE', 'PARTIAL'] })

      const state = useLabelStore.getState()
      expect(state.episodeLabels[0]).toEqual(['SUCCESS'])
      expect(state.episodeLabels[5]).toEqual(['FAILURE', 'PARTIAL'])
      expect(state.savedEpisodeLabels[0]).toEqual(['SUCCESS'])
      expect(state.savedEpisodeLabels[5]).toEqual(['FAILURE', 'PARTIAL'])
    })
  })

  describe('setEpisodeLabels', () => {
    it('sets labels for a specific episode', () => {
      useLabelStore.getState().setEpisodeLabels(3, ['SUCCESS'])
      expect(useLabelStore.getState().episodeLabels[3]).toEqual(['SUCCESS'])
      expect(useLabelStore.getState().savedEpisodeLabels[3]).toBeUndefined()
    })

    it('can commit the saved label baseline for a specific episode', () => {
      useLabelStore.getState().setAllEpisodeLabels({ '3': ['SUCCESS'] })
      useLabelStore.getState().setEpisodeLabels(3, ['FAILURE'])

      useLabelStore.getState().commitEpisodeLabels(3)

      const state = useLabelStore.getState()
      expect(state.episodeLabels[3]).toEqual(['FAILURE'])
      expect(state.savedEpisodeLabels[3]).toEqual(['FAILURE'])
    })
  })

  describe('toggleLabel', () => {
    it('adds a label when not present', () => {
      useLabelStore.getState().toggleLabel(0, 'SUCCESS')
      expect(useLabelStore.getState().episodeLabels[0]).toEqual(['SUCCESS'])
    })

    it('removes a label when already present', () => {
      useLabelStore.getState().toggleLabel(0, 'SUCCESS')
      useLabelStore.getState().toggleLabel(0, 'SUCCESS')
      expect(useLabelStore.getState().episodeLabels[0]).toEqual([])
    })

    it('handles multiple labels per episode', () => {
      useLabelStore.getState().toggleLabel(0, 'SUCCESS')
      useLabelStore.getState().toggleLabel(0, 'PARTIAL')

      expect(useLabelStore.getState().episodeLabels[0]).toEqual(['SUCCESS', 'PARTIAL'])
    })
  })

  describe('filter labels', () => {
    it('sets filter labels', () => {
      useLabelStore.getState().setFilterLabels(['SUCCESS', 'FAILURE'])
      expect(useLabelStore.getState().filterLabels).toEqual(['SUCCESS', 'FAILURE'])
    })

    it('toggles a filter label on', () => {
      useLabelStore.getState().toggleFilterLabel('SUCCESS')
      expect(useLabelStore.getState().filterLabels).toEqual(['SUCCESS'])
    })

    it('toggles a filter label off', () => {
      useLabelStore.getState().toggleFilterLabel('SUCCESS')
      useLabelStore.getState().toggleFilterLabel('SUCCESS')
      expect(useLabelStore.getState().filterLabels).toEqual([])
    })
  })

  describe('setLoaded', () => {
    it('marks the store as loaded', () => {
      useLabelStore.getState().setLoaded(true)
      expect(useLabelStore.getState().isLoaded).toBe(true)
    })
  })

  describe('reset', () => {
    it('restores initial state', () => {
      useLabelStore.getState().setAvailableLabels(['X'])
      useLabelStore.getState().toggleLabel(0, 'X')
      useLabelStore.getState().setLoaded(true)
      useLabelStore.getState().reset()

      const state = useLabelStore.getState()
      expect(state.availableLabels).toEqual(['SUCCESS', 'FAILURE', 'PARTIAL'])
      expect(state.episodeLabels).toEqual({})
      expect(state.savedEpisodeLabels).toEqual({})
      expect(state.isLoaded).toBe(false)
    })
  })
})
