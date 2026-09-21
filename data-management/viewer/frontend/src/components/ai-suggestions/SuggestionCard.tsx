/**
 * Card displaying AI annotation suggestions with accept/reject actions.
 */

import {
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronUp,
  Flag,
  Info,
  Sparkles,
  Star,
  X,
} from 'lucide-react'
import { useState } from 'react'

import type { AnnotationSuggestion, DetectedAnomaly } from '@/api/ai-analysis'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Label } from '@/components/ui/label'
import { Progress } from '@/components/ui/progress'
import { Separator } from '@/components/ui/separator'
import { getAnomalySeverityTone, getSemanticToneClasses } from '@/lib/semantic-state'
import { cn } from '@/lib/utils'

export interface SuggestionCardProps {
  /** AI suggestion data */
  suggestion: AnnotationSuggestion
  /** Whether the suggestion is being applied */
  isApplying?: boolean
  /** Whether the suggestion was accepted */
  isAccepted?: boolean
  /** Whether the suggestion was rejected */
  isRejected?: boolean
  /** Accept handler */
  onAccept?: () => void
  /** Reject handler */
  onReject?: () => void
  /** Partial accept handler (apply specific fields) */
  onPartialAccept?: (fields: SuggestionField[]) => void
  /** Additional class names */
  className?: string
}

export type SuggestionField = 'task_completion' | 'trajectory_quality' | 'flags' | 'anomalies'

/** Star rating display component */
function StarRatingDisplay({ rating, max = 5 }: { rating: number; max?: number }) {
  return (
    <div className="flex items-center gap-0.5">
      {Array.from({ length: max }, (_, i) => (
        <Star
          key={i}
          className={cn(
            'h-4 w-4',
            i < rating ? 'fill-yellow-400 text-yellow-400' : 'text-gray-300',
          )}
        />
      ))}
    </div>
  )
}

/** Anomaly item display */
function AnomalyItem({ anomaly }: { anomaly: DetectedAnomaly }) {
  const severityTone = getAnomalySeverityTone(anomaly.severity)

  return (
    <div className="flex items-start gap-2 text-sm">
      <AlertTriangle
        className={cn('mt-0.5 h-4 w-4 shrink-0', getSemanticToneClasses('icon', severityTone))}
      />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate font-medium">{anomaly.type.replace(/_/g, ' ')}</span>
          <Badge variant="status" tone={severityTone} className="text-xs">
            {anomaly.severity}
          </Badge>
        </div>
        <p className="text-muted-foreground truncate text-xs">{anomaly.description}</p>
        <p className="text-muted-foreground text-xs">
          Frames {anomaly.frame_start} - {anomaly.frame_end}
        </p>
      </div>
    </div>
  )
}

interface SuggestionFieldToggleProps {
  field: SuggestionField
  checked: boolean
  onCheckedChange: (checked: boolean) => void
  children: React.ReactNode
}

function SuggestionFieldToggle({
  field,
  checked,
  onCheckedChange,
  children,
}: SuggestionFieldToggleProps) {
  const id = `suggestion-field-${field}`

  return (
    <div className="flex items-center gap-2">
      <Checkbox
        id={id}
        checked={checked}
        onCheckedChange={(nextChecked) => onCheckedChange(nextChecked === true)}
      />
      <Label htmlFor={id} className="text-sm font-medium">
        {children}
      </Label>
    </div>
  )
}

/**
 * Displays AI suggestion with accept/reject actions.
 */
export function SuggestionCard({
  suggestion,
  isApplying = false,
  isAccepted = false,
  isRejected = false,
  onAccept,
  onReject,
  onPartialAccept,
  className,
}: SuggestionCardProps) {
  const [isExpanded, setIsExpanded] = useState(false)
  const [selectedFields, setSelectedFields] = useState<Set<SuggestionField>>(
    new Set(['task_completion', 'trajectory_quality', 'flags', 'anomalies']),
  )

  const setFieldSelected = (field: SuggestionField, checked: boolean) => {
    setSelectedFields((currentFields) => {
      const nextFields = new Set(currentFields)
      if (checked) {
        nextFields.add(field)
      } else {
        nextFields.delete(field)
      }

      return nextFields
    })
  }

  const handlePartialAccept = () => {
    if (onPartialAccept) {
      onPartialAccept(Array.from(selectedFields))
    }
  }

  const confidencePercent = Math.round(suggestion.confidence * 100)

  return (
    <Card
      className={cn(
        'overflow-hidden transition-all',
        isAccepted && 'border-green-300 bg-green-50/50',
        isRejected && 'border-red-300 bg-red-50/50 opacity-60',
        className,
      )}
    >
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="flex items-center gap-2 text-base">
            <Sparkles className="h-4 w-4 text-blue-500" />
            AI Suggestion
          </CardTitle>
          <div className="flex items-center gap-2">
            <span className="text-muted-foreground text-xs">{confidencePercent}% confidence</span>
            <Progress value={confidencePercent} className="h-1.5 w-16" />
          </div>
        </div>
      </CardHeader>

      <CardContent className="space-y-3">
        {/* Task Completion */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            {onPartialAccept && (
              <SuggestionFieldToggle
                field="task_completion"
                checked={selectedFields.has('task_completion')}
                onCheckedChange={(checked) => setFieldSelected('task_completion', checked)}
              >
                Task Completion
              </SuggestionFieldToggle>
            )}
            {!onPartialAccept && <span className="text-sm font-medium">Task Completion</span>}
          </div>
          <StarRatingDisplay rating={suggestion.task_completion_rating} />
        </div>

        {/* Trajectory Quality */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            {onPartialAccept && (
              <SuggestionFieldToggle
                field="trajectory_quality"
                checked={selectedFields.has('trajectory_quality')}
                onCheckedChange={(checked) => setFieldSelected('trajectory_quality', checked)}
              >
                Trajectory Quality
              </SuggestionFieldToggle>
            )}
            {!onPartialAccept && <span className="text-sm font-medium">Trajectory Quality</span>}
          </div>
          <StarRatingDisplay rating={suggestion.trajectory_quality_score} />
        </div>

        {/* Flags */}
        {suggestion.suggested_flags.length > 0 && (
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              {onPartialAccept && (
                <SuggestionFieldToggle
                  field="flags"
                  checked={selectedFields.has('flags')}
                  onCheckedChange={(checked) => setFieldSelected('flags', checked)}
                >
                  Suggested Flags
                </SuggestionFieldToggle>
              )}
              {!onPartialAccept && <span className="text-sm font-medium">Suggested Flags</span>}
            </div>
            <div className="ml-6 flex flex-wrap gap-1">
              {suggestion.suggested_flags.map((flag) => (
                <Badge key={flag} variant="outline" className="text-xs">
                  <Flag className="mr-1 h-3 w-3" />
                  {flag.replace(/_/g, ' ')}
                </Badge>
              ))}
            </div>
          </div>
        )}

        {/* Anomalies */}
        {suggestion.detected_anomalies.length > 0 && (
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                {onPartialAccept && (
                  <SuggestionFieldToggle
                    field="anomalies"
                    checked={selectedFields.has('anomalies')}
                    onCheckedChange={(checked) => setFieldSelected('anomalies', checked)}
                  >
                    Detected Anomalies ({suggestion.detected_anomalies.length})
                  </SuggestionFieldToggle>
                )}
                {!onPartialAccept && (
                  <span className="text-sm font-medium">
                    Detected Anomalies ({suggestion.detected_anomalies.length})
                  </span>
                )}
              </div>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setIsExpanded(!isExpanded)}
                className="h-6 px-2"
              >
                {isExpanded ? (
                  <ChevronUp className="h-4 w-4" />
                ) : (
                  <ChevronDown className="h-4 w-4" />
                )}
              </Button>
            </div>
            {isExpanded && (
              <div className="ml-6 space-y-2">
                {suggestion.detected_anomalies.map((anomaly) => (
                  <AnomalyItem key={anomaly.id} anomaly={anomaly} />
                ))}
              </div>
            )}
          </div>
        )}

        {/* Reasoning */}
        <Separator className="my-2" />
        <div className="text-muted-foreground flex items-start gap-2 text-xs">
          <Info className="mt-0.5 h-3 w-3 shrink-0" />
          <p>{suggestion.reasoning}</p>
        </div>
      </CardContent>

      <CardFooter className="gap-2 pt-2">
        {isAccepted ? (
          <div className="flex items-center gap-2 text-sm text-green-600">
            <Check className="h-4 w-4" />
            <span>Applied</span>
          </div>
        ) : isRejected ? (
          <div className="flex items-center gap-2 text-sm text-red-600">
            <X className="h-4 w-4" />
            <span>Rejected</span>
          </div>
        ) : (
          <>
            <Button
              variant="outline"
              size="sm"
              onClick={onReject}
              disabled={isApplying}
              className="text-red-600 hover:bg-red-50 hover:text-red-700"
            >
              <X className="mr-1 h-4 w-4" />
              Reject
            </Button>
            {onPartialAccept && selectedFields.size < 4 ? (
              <Button
                size="sm"
                onClick={handlePartialAccept}
                disabled={isApplying || selectedFields.size === 0}
                className="flex-1"
              >
                <Check className="mr-1 h-4 w-4" />
                Apply Selected ({selectedFields.size})
              </Button>
            ) : (
              <Button size="sm" onClick={onAccept} disabled={isApplying} className="flex-1">
                <Check className="mr-1 h-4 w-4" />
                Apply All
              </Button>
            )}
          </>
        )}
      </CardFooter>
    </Card>
  )
}
