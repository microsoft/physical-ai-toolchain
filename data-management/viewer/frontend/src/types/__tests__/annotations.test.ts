import { describe, expect, it } from 'vitest'

import {
  createDefaultAnomalyAnnotation,
  createDefaultDataQuality,
  createDefaultEpisodeAnnotation,
  createDefaultTaskCompleteness,
  createDefaultTrajectoryQuality,
} from '../annotations'

describe('annotation default factories', () => {
  describe('createDefaultTaskCompleteness', () => {
    it('returns unknown rating with confidence 3', () => {
      const tc = createDefaultTaskCompleteness()
      expect(tc.rating).toBe('unknown')
      expect(tc.confidence).toBe(3)
    })
  })

  describe('createDefaultTrajectoryQuality', () => {
    it('returns score 3 with all metrics at 3 and empty flags', () => {
      const tq = createDefaultTrajectoryQuality()
      expect(tq.overallScore).toBe(3)
      expect(tq.metrics).toEqual({
        smoothness: 3,
        efficiency: 3,
        safety: 3,
        precision: 3,
      })
      expect(tq.flags).toEqual([])
    })
  })

  describe('createDefaultDataQuality', () => {
    it('returns good quality with no issues', () => {
      const dq = createDefaultDataQuality()
      expect(dq.overallQuality).toBe('good')
      expect(dq.issues).toEqual([])
    })
  })

  describe('createDefaultAnomalyAnnotation', () => {
    it('returns empty anomalies list', () => {
      const aa = createDefaultAnomalyAnnotation()
      expect(aa.anomalies).toEqual([])
    })
  })

  describe('createDefaultEpisodeAnnotation', () => {
    it('creates a complete default annotation with annotator ID', () => {
      const ann = createDefaultEpisodeAnnotation('annotator-1')
      expect(ann.annotatorId).toBe('annotator-1')
      expect(ann.timestamp).toBeTruthy()
      expect(ann.taskCompleteness.rating).toBe('unknown')
      expect(ann.trajectoryQuality.overallScore).toBe(3)
      expect(ann.dataQuality.overallQuality).toBe('good')
      expect(ann.anomalies.anomalies).toEqual([])
    })
  })
})
