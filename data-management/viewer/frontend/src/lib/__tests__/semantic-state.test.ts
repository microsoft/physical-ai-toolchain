import { describe, expect, it } from 'vitest'

import {
  getActivityTypeTone,
  getAISuggestionTone,
  getAnnotationStatusTone,
  getAnomalySeverityTone,
  getIssueSeverityTone,
  getSemanticToneClasses,
  getSyncStatusTone,
} from '@/lib/semantic-state'

describe('semantic state conventions', () => {
  it('maps badge tones to semantic token classes', () => {
    expect(getSemanticToneClasses('badge', 'danger')).toContain('bg-status-danger-subtle')
    expect(getSemanticToneClasses('badge', 'danger')).toContain('text-status-danger-foreground')
    expect(getSemanticToneClasses('badge', 'danger')).toContain('border-status-danger-border')
  })

  it('maps anomaly severities to shared tones', () => {
    expect(getAnomalySeverityTone('low')).toBe('warning')
    expect(getAnomalySeverityTone('medium')).toBe('warning')
    expect(getAnomalySeverityTone('high')).toBe('danger')
  })

  it('maps issue severities to shared tones', () => {
    expect(getIssueSeverityTone('minor')).toBe('warning')
    expect(getIssueSeverityTone('major')).toBe('warning')
    expect(getIssueSeverityTone('critical')).toBe('danger')
  })

  it('maps sync status values to shared tones', () => {
    expect(getSyncStatusTone('pending')).toBe('info')
    expect(getSyncStatusTone('synced')).toBe('success')
    expect(getSyncStatusTone('conflict')).toBe('warning')
  })

  it('maps activity types to shared tones', () => {
    expect(getActivityTypeTone('annotation')).toBe('success')
    expect(getActivityTypeTone('review')).toBe('info')
    expect(getActivityTypeTone('edit')).toBe('warning')
  })

  it('maps annotation status values to shared tones', () => {
    expect(getAnnotationStatusTone('pending')).toBe('neutral')
    expect(getAnnotationStatusTone('in-progress')).toBe('warning')
    expect(getAnnotationStatusTone('complete')).toBe('success')
  })

  it('maps AI suggestion states to shared tones', () => {
    expect(getAISuggestionTone({ hasError: true })).toBe('danger')
    expect(getAISuggestionTone({ isAccepted: true })).toBe('success')
    expect(getAISuggestionTone({ isLoading: true })).toBe('neutral')
    expect(getAISuggestionTone({ confidence: 0.92 })).toBe('info')
    expect(getAISuggestionTone({ confidence: 0.61 })).toBe('warning')
  })
})
