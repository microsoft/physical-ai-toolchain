import { waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as draftStorage from '@/lib/edit-draft-storage'
import { clearPersistedEditDraftsForTests } from '@/lib/edit-draft-storage'
import type { FrameInsertion } from '@/types/episode-edit'

import {
  getEffectiveFrameCount,
  getEffectiveIndex,
  getOriginalIndex,
  useEditStore,
} from '../edit-store'

describe('edit-store pure functions', () => {
  describe('getEffectiveIndex', () => {
    it('returns the same index when no edits exist', () => {
      expect(getEffectiveIndex(5, new Map(), new Set())).toBe(5)
    })

    it('offsets for insertions before the index', () => {
      const inserted = new Map<number, FrameInsertion>([
        [2, { afterFrameIndex: 2, interpolationFactor: 0.5 }],
      ])
      expect(getEffectiveIndex(5, inserted, new Set())).toBe(6)
    })

    it('does not offset for insertions after the index', () => {
      const inserted = new Map<number, FrameInsertion>([
        [10, { afterFrameIndex: 10, interpolationFactor: 0.5 }],
      ])
      expect(getEffectiveIndex(5, inserted, new Set())).toBe(5)
    })

    it('offsets for removals before the index', () => {
      expect(getEffectiveIndex(5, new Map(), new Set([1, 3]))).toBe(3)
    })

    it('handles combined insertions and removals', () => {
      const inserted = new Map<number, FrameInsertion>([
        [1, { afterFrameIndex: 1, interpolationFactor: 0.5 }],
      ])
      const removed = new Set([2])
      expect(getEffectiveIndex(5, inserted, removed)).toBe(5)
    })

    it('skips insertions after removed frames', () => {
      const inserted = new Map<number, FrameInsertion>([
        [3, { afterFrameIndex: 3, interpolationFactor: 0.5 }],
      ])
      const removed = new Set([3])
      expect(getEffectiveIndex(5, inserted, removed)).toBe(4)
    })
  })

  describe('getEffectiveFrameCount', () => {
    it('returns original count when no edits', () => {
      expect(getEffectiveFrameCount(100, new Map(), new Set())).toBe(100)
    })

    it('subtracts removed frames', () => {
      expect(getEffectiveFrameCount(100, new Map(), new Set([5, 10, 15]))).toBe(97)
    })

    it('adds valid insertions', () => {
      const inserted = new Map<number, FrameInsertion>([
        [5, { afterFrameIndex: 5, interpolationFactor: 0.5 }],
        [20, { afterFrameIndex: 20, interpolationFactor: 0.5 }],
      ])
      expect(getEffectiveFrameCount(100, inserted, new Set())).toBe(102)
    })

    it('ignores insertions after removed frames', () => {
      const inserted = new Map<number, FrameInsertion>([
        [5, { afterFrameIndex: 5, interpolationFactor: 0.5 }],
      ])
      const removed = new Set([5])
      expect(getEffectiveFrameCount(100, inserted, removed)).toBe(99)
    })

    it('ignores insertions after the last valid frame', () => {
      const inserted = new Map<number, FrameInsertion>([
        [99, { afterFrameIndex: 99, interpolationFactor: 0.5 }],
      ])
      expect(getEffectiveFrameCount(100, inserted, new Set())).toBe(100)
    })
  })

  describe('getOriginalIndex', () => {
    it('returns the same index when no edits exist', () => {
      expect(getOriginalIndex(5, new Map(), new Set())).toBe(5)
    })

    it('returns null for an inserted frame position', () => {
      const inserted = new Map<number, FrameInsertion>([
        [2, { afterFrameIndex: 2, interpolationFactor: 0.5 }],
      ])
      const effectiveInsertionPos = getEffectiveIndex(2, inserted, new Set()) + 1
      expect(getOriginalIndex(effectiveInsertionPos, inserted, new Set())).toBeNull()
    })
  })
})

describe('useEditStore', () => {
  beforeEach(async () => {
    vi.restoreAllMocks()
    useEditStore.getState().clear()
    await clearPersistedEditDraftsForTests()
  })

  describe('initializeEdit', () => {
    it('does not restore another principal in-memory draft', () => {
      useEditStore.getState().initializeEdit('ds-1', 0, 'author-a')
      useEditStore.getState().toggleFrameRemoval(2)
      useEditStore.getState().saveEpisodeDraft()
      useEditStore.getState().initializeEdit('ds-1', 0, 'author-b')
      expect(useEditStore.getState().principalScopeId).toBe('author-b')
      expect(useEditStore.getState().removedFrames.size).toBe(0)
      useEditStore.getState().initializeEdit('ds-1', 0, 'author-a')
      expect(useEditStore.getState().principalScopeId).toBe('author-a')
      expect(useEditStore.getState().removedFrames.has(2)).toBe(true)
    })

    it('retains newer edits when a persisted draft resolves after initialization', async () => {
      vi.spyOn(draftStorage, 'loadPersistedEditDraft').mockResolvedValueOnce({
        schemaVersion: 2,
        principalScopeId: 'author-a',
        resource: { kind: 'episode-edit', datasetId: 'ds-1', episodeIndex: 0 },
        baseEtag: null,
        baseline: null,
        draft: { datasetId: 'ds-1', episodeIndex: 0, removedFrames: [2] },
        generation: 1,
        updatedAt: new Date().toISOString(),
      })
      useEditStore.getState().initializeEdit('ds-1', 0, 'author-a')
      useEditStore.getState().toggleFrameRemoval(7)
      await Promise.resolve()
      expect([...useEditStore.getState().removedFrames]).toEqual([7])
      expect(useEditStore.getState().isDirty).toBe(true)
    })

    it('sets up a clean edit session', () => {
      useEditStore.getState().initializeEdit('ds-1', 0)

      const state = useEditStore.getState()
      expect(state.datasetId).toBe('ds-1')
      expect(state.episodeIndex).toBe(0)
      expect(state.isDirty).toBe(false)
      expect(state.removedFrames.size).toBe(0)
      expect(state.insertedFrames.size).toBe(0)
      expect(state.subtasks).toEqual([])
    })
  })

  describe('frame removal', () => {
    beforeEach(() => {
      useEditStore.getState().initializeEdit('ds-1', 0)
    })

    it('toggles a frame as removed', () => {
      useEditStore.getState().toggleFrameRemoval(5)
      expect(useEditStore.getState().removedFrames.has(5)).toBe(true)
      expect(useEditStore.getState().isDirty).toBe(true)
    })

    it('toggles a removed frame back', () => {
      useEditStore.getState().toggleFrameRemoval(5)
      useEditStore.getState().toggleFrameRemoval(5)
      expect(useEditStore.getState().removedFrames.has(5)).toBe(false)
    })

    it('adds a range of frames', () => {
      useEditStore.getState().addFrameRange(3, 7)

      const removed = useEditStore.getState().removedFrames
      expect(removed.size).toBe(5)
      for (let i = 3; i <= 7; i++) {
        expect(removed.has(i)).toBe(true)
      }
    })

    it('adds frames by frequency', () => {
      useEditStore.getState().addFramesByFrequency(0, 10, 3)

      const removed = useEditStore.getState().removedFrames
      expect(removed.has(0)).toBe(true)
      expect(removed.has(3)).toBe(true)
      expect(removed.has(6)).toBe(true)
      expect(removed.has(9)).toBe(true)
      expect(removed.has(1)).toBe(false)
    })

    it('removes a range of frames from removal', () => {
      useEditStore.getState().addFrameRange(0, 10)
      useEditStore.getState().removeFrameRange(3, 7)

      const removed = useEditStore.getState().removedFrames
      expect(removed.has(0)).toBe(true)
      expect(removed.has(3)).toBe(false)
      expect(removed.has(7)).toBe(false)
      expect(removed.has(10)).toBe(true)
    })

    it('clears all removed frames', () => {
      useEditStore.getState().addFrameRange(0, 5)
      useEditStore.getState().clearRemovedFrames()
      expect(useEditStore.getState().removedFrames.size).toBe(0)
    })
  })

  describe('frame insertion', () => {
    beforeEach(() => {
      useEditStore.getState().initializeEdit('ds-1', 0)
    })

    it('inserts a frame with default factor', () => {
      useEditStore.getState().insertFrame(5)

      const ins = useEditStore.getState().insertedFrames.get(5)
      expect(ins).toBeDefined()
      expect(ins!.interpolationFactor).toBe(0.5)
      expect(useEditStore.getState().isDirty).toBe(true)
    })

    it('inserts a frame with custom factor', () => {
      useEditStore.getState().insertFrame(5, 0.3)

      const ins = useEditStore.getState().insertedFrames.get(5)
      expect(ins!.interpolationFactor).toBe(0.3)
    })

    it('removes an inserted frame', () => {
      useEditStore.getState().insertFrame(5)
      useEditStore.getState().removeInsertedFrame(5)
      expect(useEditStore.getState().insertedFrames.has(5)).toBe(false)
    })

    it('clears all inserted frames', () => {
      useEditStore.getState().insertFrame(5)
      useEditStore.getState().insertFrame(10)
      useEditStore.getState().clearInsertedFrames()
      expect(useEditStore.getState().insertedFrames.size).toBe(0)
    })
  })

  describe('transforms', () => {
    beforeEach(() => {
      useEditStore.getState().initializeEdit('ds-1', 0)
    })

    it('sets global transform', () => {
      const transform = { crop: { x: 10, y: 10, width: 200, height: 150 } }
      useEditStore.getState().setGlobalTransform(transform)

      expect(useEditStore.getState().globalTransform).toEqual(transform)
      expect(useEditStore.getState().isDirty).toBe(true)
    })

    it('sets camera-specific transform', () => {
      const transform = { crop: { x: 0, y: 0, width: 100, height: 100 } }
      useEditStore.getState().setCameraTransform('front', transform)

      expect(useEditStore.getState().cameraTransforms['front']).toEqual(transform)
    })

    it('removes camera transform when set to null', () => {
      const transform = { crop: { x: 0, y: 0, width: 100, height: 100 } }
      useEditStore.getState().setCameraTransform('front', transform)
      useEditStore.getState().setCameraTransform('front', null)

      expect(useEditStore.getState().cameraTransforms['front']).toBeUndefined()
    })

    it('clears all transforms', () => {
      useEditStore.getState().setGlobalTransform({ crop: { x: 0, y: 0, width: 1, height: 1 } })
      useEditStore
        .getState()
        .setCameraTransform('cam', { crop: { x: 0, y: 0, width: 1, height: 1 } })
      useEditStore.getState().clearTransforms()

      expect(useEditStore.getState().globalTransform).toBeNull()
      expect(useEditStore.getState().cameraTransforms).toEqual({})
    })

    it('isDirty returns false after clearTransforms when only transforms were changed', () => {
      useEditStore.getState().setGlobalTransform({ resize: { width: 320, height: 240 } })
      expect(useEditStore.getState().isDirty).toBe(true)

      useEditStore.getState().clearTransforms()
      expect(useEditStore.getState().isDirty).toBe(false)
    })

    it('isDirty returns false after setting globalTransform back to null', () => {
      useEditStore.getState().setGlobalTransform({
        colorAdjustment: { brightness: 0.5 },
        colorFilter: 'grayscale',
      })
      expect(useEditStore.getState().isDirty).toBe(true)

      useEditStore.getState().setGlobalTransform(null)
      expect(useEditStore.getState().isDirty).toBe(false)
    })
  })

  describe('markSaved / resetEdits', () => {
    const baseline = {
      sourceId: 'source-a',
      sourceRevision: 'generation-a',
      principalScopeId: 'local',
      etag: 'revision-a',
      operations: { datasetId: 'ds-1', episodeIndex: 0, removedFrames: [2] },
    }

    async function initializeSavedSession() {
      useEditStore.getState().initializeEdit('ds-1', 0)
      await waitFor(() => expect(useEditStore.getState().draftHydrated).toBe(true))
    }

    it('loads server operations as the saved baseline', async () => {
      await initializeSavedSession()
      expect(useEditStore.getState().hydrateSavedEdits(baseline)).toBe(true)
      expect([...useEditStore.getState().removedFrames]).toEqual([2])
      expect(useEditStore.getState().isDirty).toBe(false)
      expect(useEditStore.getState().serverBaseline).toEqual(baseline)
    })

    it('acknowledges only submitted edits and retains changes made during a save', async () => {
      await initializeSavedSession()
      useEditStore.getState().hydrateSavedEdits(baseline)
      useEditStore.getState().toggleFrameRemoval(5)
      const submitted = { ...baseline, operations: useEditStore.getState().getEditOperations()! }
      useEditStore.getState().toggleFrameRemoval(7)
      useEditStore.getState().acknowledgeSave(submitted, { ...submitted, etag: 'revision-b' })
      expect([...useEditStore.getState().removedFrames]).toEqual([2, 5, 7])
      expect(useEditStore.getState().serverBaseline?.etag).toBe('revision-b')
      expect(useEditStore.getState().isDirty).toBe(true)
      useEditStore.getState().resetEdits()
      expect([...useEditStore.getState().removedFrames]).toEqual([2, 5])
      expect(useEditStore.getState().isDirty).toBe(false)
    })

    it('does not hydrate over drafts or acknowledge another source, principal or revision', async () => {
      await initializeSavedSession()
      useEditStore.getState().hydrateSavedEdits(baseline)
      useEditStore.getState().toggleFrameRemoval(5)
      expect(useEditStore.getState().hydrateSavedEdits({ ...baseline, etag: 'newer' })).toBe(false)
      for (const changed of [
        { sourceId: 'source-b' },
        { sourceRevision: 'generation-b' },
        { principalScopeId: 'someone-else' },
        { etag: 'stale' },
      ]) {
        useEditStore.getState().acknowledgeSave(
          { ...baseline, ...changed },
          {
            ...baseline,
            etag: 'revision-b',
          },
        )
        expect(useEditStore.getState().serverBaseline).toEqual(baseline)
        expect(useEditStore.getState().isDirty).toBe(true)
      }
    })

    it('restores empty clearing with its pinned server baseline and revision', async () => {
      await initializeSavedSession()
      useEditStore.getState().hydrateSavedEdits(baseline)
      useEditStore.getState().clearRemovedFrames()
      useEditStore.getState().saveEpisodeDraft()
      expect(useEditStore.getState().isDirty).toBe(true)
      useEditStore.getState().clear()
      useEditStore.getState().initializeEdit('ds-1', 0)
      await waitFor(() => expect(useEditStore.getState().serverBaseline).toEqual(baseline))
      expect(useEditStore.getState().removedFrames.size).toBe(0)
      expect(useEditStore.getState().isDirty).toBe(true)
    })

    it('retains the acknowledged revision when revisiting an episode', async () => {
      await initializeSavedSession()
      useEditStore.getState().hydrateSavedEdits(baseline)
      useEditStore.getState().toggleFrameRemoval(5)
      useEditStore.getState().saveEpisodeDraft()
      const submitted = { ...baseline, operations: useEditStore.getState().getEditOperations()! }
      useEditStore.getState().acknowledgeSave(submitted, { ...submitted, etag: 'revision-b' })
      useEditStore.getState().initializeEdit('ds-1', 1)
      useEditStore.getState().initializeEdit('ds-1', 0)
      expect(useEditStore.getState().serverBaseline?.etag).toBe('revision-b')
      expect([...useEditStore.getState().removedFrames]).toEqual([2, 5])
      expect(useEditStore.getState().isDirty).toBe(false)
    })

    it('defers server hydration until browser draft recovery finishes', async () => {
      await draftStorage.persistEditDraft(
        'ds-1',
        0,
        'local',
        {
          datasetId: 'ds-1',
          episodeIndex: 0,
          removedFrames: [2, 5],
        },
        baseline,
      )
      useEditStore.getState().initializeEdit('ds-1', 0)
      expect(useEditStore.getState().hydrateSavedEdits(baseline)).toBe(false)
      await waitFor(() => expect(useEditStore.getState().draftHydrated).toBe(true))
      expect([...useEditStore.getState().removedFrames]).toEqual([2, 5])
      expect(useEditStore.getState().isDirty).toBe(true)
    })

    it('resetEdits reverts to original state', () => {
      useEditStore.getState().initializeEdit('ds-1', 0)
      useEditStore.getState().toggleFrameRemoval(5)
      useEditStore.getState().resetEdits()

      expect(useEditStore.getState().removedFrames.size).toBe(0)
      expect(useEditStore.getState().isDirty).toBe(false)
    })

    it('reloads saved edits when returning to an episode', () => {
      useEditStore.getState().initializeEdit('ds-1', 0)
      useEditStore.getState().toggleFrameRemoval(5)
      useEditStore.getState().saveEpisodeDraft()

      useEditStore.getState().initializeEdit('ds-1', 1)
      expect(useEditStore.getState().removedFrames.size).toBe(0)

      useEditStore.getState().initializeEdit('ds-1', 0)
      expect(useEditStore.getState().removedFrames.has(5)).toBe(true)
      expect(useEditStore.getState().isDirty).toBe(true)
    })

    it('restores a saved draft after the in-memory store is cleared', async () => {
      useEditStore.getState().initializeEdit('ds-1', 0)
      useEditStore.getState().toggleFrameRemoval(5)
      useEditStore.getState().addSubtaskFromRange(10, 20)
      useEditStore.getState().saveEpisodeDraft()

      useEditStore.getState().clear()
      useEditStore.getState().initializeEdit('ds-1', 0)

      await waitFor(() => {
        expect(useEditStore.getState().removedFrames.has(5)).toBe(true)
        expect(useEditStore.getState().subtasks).toHaveLength(1)
      })
    })

    it('restores in-progress edits after the in-memory store is cleared without an explicit save', async () => {
      useEditStore.getState().initializeEdit('ds-1', 2)
      useEditStore.getState().addSubtaskFromRange(15, 30)

      useEditStore.getState().clear()
      useEditStore.getState().initializeEdit('ds-1', 2)

      await waitFor(() => {
        expect(useEditStore.getState().subtasks).toHaveLength(1)
        expect(useEditStore.getState().subtasks[0]?.frameRange).toEqual([15, 30])
      })
    })

    it('resetEdits clears subtasks, transforms, and trajectory adjustments', () => {
      useEditStore.getState().initializeEdit('ds-1', 0)

      useEditStore.getState().setGlobalTransform({
        colorAdjustment: { brightness: 0.5 },
      })
      useEditStore.getState().addSubtaskFromRange(10, 50)
      useEditStore.getState().insertFrame(3)
      useEditStore.getState().setTrajectoryAdjustment(7, {
        rightArmDelta: [0.1, 0, 0],
      })

      expect(useEditStore.getState().isDirty).toBe(true)
      expect(useEditStore.getState().subtasks).toHaveLength(1)
      expect(useEditStore.getState().globalTransform).not.toBeNull()
      expect(useEditStore.getState().insertedFrames.size).toBe(1)
      expect(useEditStore.getState().trajectoryAdjustments.size).toBe(1)

      useEditStore.getState().resetEdits()

      expect(useEditStore.getState().isDirty).toBe(false)
      expect(useEditStore.getState().subtasks).toHaveLength(0)
      expect(useEditStore.getState().globalTransform).toBeNull()
      expect(useEditStore.getState().insertedFrames.size).toBe(0)
      expect(useEditStore.getState().removedFrames.size).toBe(0)
      expect(useEditStore.getState().trajectoryAdjustments.size).toBe(0)
    })
  })

  describe('getEditOperations', () => {
    it('returns null when not initialized', () => {
      expect(useEditStore.getState().getEditOperations()).toBeNull()
    })

    it('returns operations with active edits', () => {
      useEditStore.getState().initializeEdit('ds-1', 3)
      useEditStore.getState().toggleFrameRemoval(10)
      useEditStore.getState().insertFrame(5)

      const ops = useEditStore.getState().getEditOperations()
      expect(ops).not.toBeNull()
      expect(ops!.datasetId).toBe('ds-1')
      expect(ops!.episodeIndex).toBe(3)
      expect(ops!.removedFrames).toEqual([10])
      expect(ops!.insertedFrames).toHaveLength(1)
    })
  })
})
