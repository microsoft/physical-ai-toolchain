import { beforeEach, describe, expect, it } from 'vitest'

import type { Anomaly, EpisodeAnnotation } from '@/types'

import { useAnnotationStore } from '../annotation-store'

const mockAnnotation: EpisodeAnnotation = {
  annotatorId: 'user-1',
  timestamp: '2025-01-01T00:00:00.000Z',
  taskCompleteness: { rating: 'success', confidence: 4 },
  trajectoryQuality: {
    overallScore: 4,
    metrics: { smoothness: 4, efficiency: 3, safety: 5, precision: 4 },
    flags: ['hesitation'],
  },
  dataQuality: { overallQuality: 'good', issues: [] },
  anomalies: { anomalies: [] },
  notes: 'Looks good',
}

const mockAnomaly: Anomaly = {
  id: 'anom-1',
  type: 'unexpected-stop',
  severity: 'medium',
  frameRange: [10, 20],
  timestamp: [0.33, 0.67],
  description: 'Robot stopped unexpectedly',
  autoDetected: false,
  verified: false,
}

describe('useAnnotationStore', () => {
  beforeEach(() => {
    useAnnotationStore.getState().clear()
  })

  it('starts with initial state', () => {
    const state = useAnnotationStore.getState()
    expect(state.currentAnnotation).toBeNull()
    expect(state.originalAnnotation).toBeNull()
    expect(state.isDirty).toBe(false)
    expect(state.isSaving).toBe(false)
    expect(state.error).toBeNull()
  })

  describe('initializeAnnotation', () => {
    it('creates a default annotation for the given annotator', () => {
      useAnnotationStore.getState().initializeAnnotation('user-42')

      const state = useAnnotationStore.getState()
      expect(state.currentAnnotation).not.toBeNull()
      expect(state.currentAnnotation!.annotatorId).toBe('user-42')
      expect(state.currentAnnotation!.taskCompleteness.rating).toBe('unknown')
      expect(state.currentAnnotation!.trajectoryQuality.overallScore).toBe(3)
      expect(state.isDirty).toBe(false)
      expect(state.annotatorId).toBe('user-42')
    })

    it('stores a deep copy as originalAnnotation', () => {
      useAnnotationStore.getState().initializeAnnotation('user-42')

      const state = useAnnotationStore.getState()
      expect(state.originalAnnotation).toEqual(state.currentAnnotation)
      expect(state.originalAnnotation).not.toBe(state.currentAnnotation)
    })
  })

  describe('loadAnnotation', () => {
    it('loads an existing annotation as deep copy', () => {
      useAnnotationStore.getState().loadAnnotation(mockAnnotation)

      const state = useAnnotationStore.getState()
      expect(state.currentAnnotation).toEqual(mockAnnotation)
      expect(state.currentAnnotation).not.toBe(mockAnnotation)
      expect(state.isDirty).toBe(false)
      expect(state.annotatorId).toBe('user-1')
    })
  })

  describe('updateTaskCompleteness', () => {
    beforeEach(() => {
      useAnnotationStore.getState().loadAnnotation(mockAnnotation)
    })

    it('partially updates task completeness and marks dirty', () => {
      useAnnotationStore.getState().updateTaskCompleteness({ rating: 'failure' })

      const state = useAnnotationStore.getState()
      expect(state.currentAnnotation!.taskCompleteness.rating).toBe('failure')
      expect(state.currentAnnotation!.taskCompleteness.confidence).toBe(4)
      expect(state.isDirty).toBe(true)
    })

    it('is a no-op when there is no current annotation', () => {
      useAnnotationStore.getState().clear()
      useAnnotationStore.getState().updateTaskCompleteness({ rating: 'failure' })
      expect(useAnnotationStore.getState().currentAnnotation).toBeNull()
    })
  })

  describe('updateTrajectoryQuality', () => {
    beforeEach(() => {
      useAnnotationStore.getState().loadAnnotation(mockAnnotation)
    })

    it('updates overall score and preserves existing metrics', () => {
      useAnnotationStore.getState().updateTrajectoryQuality({ overallScore: 2 })

      const tq = useAnnotationStore.getState().currentAnnotation!.trajectoryQuality
      expect(tq.overallScore).toBe(2)
      expect(tq.metrics.smoothness).toBe(4)
    })

    it('merges individual metric updates', () => {
      useAnnotationStore.getState().updateTrajectoryQuality({ metrics: { smoothness: 1 } } as never)

      const metrics = useAnnotationStore.getState().currentAnnotation!.trajectoryQuality.metrics
      expect(metrics.smoothness).toBe(1)
      expect(metrics.efficiency).toBe(3)
    })
  })

  describe('updateDataQuality', () => {
    beforeEach(() => {
      useAnnotationStore.getState().loadAnnotation(mockAnnotation)
    })

    it('updates data quality and marks dirty', () => {
      useAnnotationStore.getState().updateDataQuality({ overallQuality: 'poor' })

      const state = useAnnotationStore.getState()
      expect(state.currentAnnotation!.dataQuality.overallQuality).toBe('poor')
      expect(state.isDirty).toBe(true)
    })
  })

  describe('anomaly CRUD', () => {
    beforeEach(() => {
      useAnnotationStore.getState().loadAnnotation(mockAnnotation)
    })

    it('adds an anomaly', () => {
      useAnnotationStore.getState().addAnomaly(mockAnomaly)

      const anomalies = useAnnotationStore.getState().currentAnnotation!.anomalies.anomalies
      expect(anomalies).toHaveLength(1)
      expect(anomalies[0]).toEqual(mockAnomaly)
    })

    it('updates an anomaly by id', () => {
      useAnnotationStore.getState().addAnomaly(mockAnomaly)
      useAnnotationStore.getState().updateAnomaly('anom-1', { severity: 'high' })

      const anomalies = useAnnotationStore.getState().currentAnnotation!.anomalies.anomalies
      expect(anomalies[0].severity).toBe('high')
      expect(anomalies[0].type).toBe('unexpected-stop')
    })

    it('removes an anomaly by id', () => {
      useAnnotationStore.getState().addAnomaly(mockAnomaly)
      useAnnotationStore.getState().removeAnomaly('anom-1')

      expect(useAnnotationStore.getState().currentAnnotation!.anomalies.anomalies).toHaveLength(0)
    })

    it('toggles anomaly verified status', () => {
      useAnnotationStore.getState().addAnomaly(mockAnomaly)

      useAnnotationStore.getState().toggleAnomalyVerified('anom-1')
      expect(useAnnotationStore.getState().currentAnnotation!.anomalies.anomalies[0].verified).toBe(
        true,
      )

      useAnnotationStore.getState().toggleAnomalyVerified('anom-1')
      expect(useAnnotationStore.getState().currentAnnotation!.anomalies.anomalies[0].verified).toBe(
        false,
      )
    })
  })

  describe('updateNotes', () => {
    it('updates free-form notes', () => {
      useAnnotationStore.getState().loadAnnotation(mockAnnotation)
      useAnnotationStore.getState().updateNotes('Updated note')

      expect(useAnnotationStore.getState().currentAnnotation!.notes).toBe('Updated note')
      expect(useAnnotationStore.getState().isDirty).toBe(true)
    })
  })

  describe('markSaved', () => {
    it('resets dirty flag and updates original snapshot', () => {
      useAnnotationStore.getState().loadAnnotation(mockAnnotation)
      useAnnotationStore.getState().updateTaskCompleteness({ rating: 'failure' })
      expect(useAnnotationStore.getState().isDirty).toBe(true)

      useAnnotationStore.getState().markSaved()

      const state = useAnnotationStore.getState()
      expect(state.isDirty).toBe(false)
      expect(state.isSaving).toBe(false)
      expect(state.originalAnnotation!.taskCompleteness.rating).toBe('failure')
    })
  })

  describe('resetAnnotation', () => {
    it('reverts to original annotation', () => {
      useAnnotationStore.getState().loadAnnotation(mockAnnotation)
      useAnnotationStore.getState().updateTaskCompleteness({ rating: 'failure' })
      useAnnotationStore.getState().resetAnnotation()

      const state = useAnnotationStore.getState()
      expect(state.currentAnnotation!.taskCompleteness.rating).toBe('success')
      expect(state.isDirty).toBe(false)
    })
  })

  describe('setSaving / setError', () => {
    it('sets saving state', () => {
      useAnnotationStore.getState().setSaving(true)
      expect(useAnnotationStore.getState().isSaving).toBe(true)
    })

    it('sets error and clears saving', () => {
      useAnnotationStore.getState().setSaving(true)
      useAnnotationStore.getState().setError('Save failed')

      const state = useAnnotationStore.getState()
      expect(state.error).toBe('Save failed')
      expect(state.isSaving).toBe(false)
    })
  })
})
