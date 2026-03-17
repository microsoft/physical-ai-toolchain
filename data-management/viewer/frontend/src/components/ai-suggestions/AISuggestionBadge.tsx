/**
 * Badge showing AI suggestion confidence level.
 */

import { Loader2, Sparkles } from 'lucide-react'

import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { getAISuggestionTone, getSemanticToneClasses } from '@/lib/semantic-state'
import { cn } from '@/lib/utils'

export interface AISuggestionBadgeProps {
  /** Confidence level 0-1 */
  confidence?: number
  /** Whether the suggestion is loading */
  isLoading?: boolean
  /** Whether there's an error */
  hasError?: boolean
  /** Whether the suggestion was accepted */
  isAccepted?: boolean
  /** Additional class names */
  className?: string
  /** Click handler */
  onClick?: () => void
}

/**
 * Displays AI suggestion status with confidence indicator.
 */
export function AISuggestionBadge({
  confidence,
  isLoading = false,
  hasError = false,
  isAccepted = false,
  className,
  onClick,
}: AISuggestionBadgeProps) {
  const tone = getAISuggestionTone({
    confidence,
    hasError,
    isAccepted,
    isLoading,
  })

  const getConfidenceLabel = () => {
    if (hasError) return 'Error'
    if (isAccepted) return 'Applied'
    if (isLoading) return 'Analyzing...'
    if (confidence === undefined) return 'No data'
    if (confidence >= 0.8) return 'High confidence'
    if (confidence >= 0.5) return 'Medium confidence'
    return 'Low confidence'
  }

  const getTooltipContent = () => {
    if (hasError) return 'Failed to get AI suggestion'
    if (isAccepted) return 'AI suggestion was applied'
    if (isLoading) return 'AI is analyzing the trajectory...'
    if (confidence === undefined) return 'No trajectory data available'
    return `AI confidence: ${Math.round(confidence * 100)}%`
  }

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            onClick={onClick}
            disabled={isLoading || hasError}
            className={cn(
              'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium transition-colors',
              getSemanticToneClasses('badge', tone),
              onClick && !isLoading && !hasError && 'cursor-pointer hover:opacity-80',
              (isLoading || hasError) && 'cursor-default',
              className,
            )}
          >
            {isLoading ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <Sparkles className="h-3 w-3" />
            )}
            <span>{getConfidenceLabel()}</span>
          </button>
        </TooltipTrigger>
        <TooltipContent side="bottom">
          <p>{getTooltipContent()}</p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}
