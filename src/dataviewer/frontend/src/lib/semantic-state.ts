import type { AnomalySeverity, EpisodeMeta, IssueSeverity } from '@/types'

export type SemanticTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger'
export type SemanticToneTarget = 'badge' | 'surface' | 'text' | 'icon'
export type SyncStatusTone = 'pending' | 'synced' | 'conflict'
export type ActivityTone = 'annotation' | 'review' | 'edit'
export type AnnotationStatusTone = NonNullable<EpisodeMeta['annotationStatus']>

interface AISuggestionToneOptions {
  confidence?: number
  hasError?: boolean
  isAccepted?: boolean
  isLoading?: boolean
}

const semanticToneClasses: Record<SemanticToneTarget, Record<SemanticTone, string>> = {
  badge: {
    neutral: 'border-status-neutral-border bg-status-neutral-subtle text-status-neutral-foreground',
    info: 'border-status-info-border bg-status-info-subtle text-status-info-foreground',
    success: 'border-status-success-border bg-status-success-subtle text-status-success-foreground',
    warning: 'border-status-warning-border bg-status-warning-subtle text-status-warning-foreground',
    danger: 'border-status-danger-border bg-status-danger-subtle text-status-danger-foreground',
  },
  surface: {
    neutral: 'border-status-neutral-border bg-status-neutral-subtle text-status-neutral-foreground',
    info: 'border-status-info-border bg-status-info-subtle text-status-info-foreground',
    success: 'border-status-success-border bg-status-success-subtle text-status-success-foreground',
    warning: 'border-status-warning-border bg-status-warning-subtle text-status-warning-foreground',
    danger: 'border-status-danger-border bg-status-danger-subtle text-status-danger-foreground',
  },
  text: {
    neutral: 'text-status-neutral-foreground',
    info: 'text-status-info-foreground',
    success: 'text-status-success-foreground',
    warning: 'text-status-warning-foreground',
    danger: 'text-status-danger-foreground',
  },
  icon: {
    neutral: 'text-status-neutral',
    info: 'text-status-info',
    success: 'text-status-success',
    warning: 'text-status-warning',
    danger: 'text-status-danger',
  },
}

export function getSemanticToneClasses(target: SemanticToneTarget, tone: SemanticTone) {
  return semanticToneClasses[target][tone]
}

export function getAnomalySeverityTone(severity: AnomalySeverity): SemanticTone {
  if (severity === 'high') {
    return 'danger'
  }

  return 'warning'
}

export function getIssueSeverityTone(severity: IssueSeverity): SemanticTone {
  if (severity === 'critical') {
    return 'danger'
  }

  return 'warning'
}

export function getSyncStatusTone(status: SyncStatusTone): SemanticTone {
  switch (status) {
    case 'pending':
      return 'info'
    case 'synced':
      return 'success'
    case 'conflict':
      return 'warning'
  }
}

export function getActivityTypeTone(activityType: ActivityTone): SemanticTone {
  switch (activityType) {
    case 'annotation':
      return 'success'
    case 'review':
      return 'info'
    case 'edit':
      return 'warning'
  }
}

export function getAnnotationStatusTone(status: AnnotationStatusTone): SemanticTone {
  switch (status) {
    case 'complete':
      return 'success'
    case 'in-progress':
      return 'warning'
    case 'pending':
      return 'neutral'
  }
}

export function getAISuggestionTone({
  confidence,
  hasError = false,
  isAccepted = false,
  isLoading = false,
}: AISuggestionToneOptions): SemanticTone {
  if (hasError) {
    return 'danger'
  }

  if (isAccepted) {
    return 'success'
  }

  if (isLoading || confidence === undefined) {
    return 'neutral'
  }

  if (confidence >= 0.8) {
    return 'info'
  }

  return 'warning'
}