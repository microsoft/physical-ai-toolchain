/**
 * Reusable star rating input component.
 */

import { Star } from 'lucide-react'

import { cn } from '@/lib/utils'

interface StarRatingProps {
  /** Current rating value (1-5) */
  value: number
  /** Callback when rating changes */
  onChange: (value: number) => void
  /** Maximum rating value */
  max?: number
  /** Size of stars */
  size?: 'sm' | 'md' | 'lg'
  /** Whether the rating is read-only */
  readOnly?: boolean
  /** Label to display */
  label?: string
}

/**
 * Interactive star rating component.
 *
 * @example
 * ```tsx
 * <StarRating
 *   value={3}
 *   onChange={setRating}
 *   label="Overall Quality"
 * />
 * ```
 */
export function StarRating({
  value,
  onChange,
  max = 5,
  size = 'md',
  readOnly = false,
  label,
}: StarRatingProps) {
  const sizeClasses = {
    sm: 'h-4 w-4',
    md: 'h-5 w-5',
    lg: 'h-6 w-6',
  }

  const handleClick = (rating: number) => {
    if (!readOnly) {
      onChange(rating)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent, rating: number) => {
    if (!readOnly && (e.key === 'Enter' || e.key === ' ')) {
      e.preventDefault()
      onChange(rating)
    }
  }

  return (
    <div className="space-y-1">
      {label && <label className="text-sm font-medium">{label}</label>}
      <div className="flex gap-1" role="radiogroup" aria-label={label}>
        {Array.from({ length: max }, (_, i) => i + 1).map((rating) => (
          <button
            key={rating}
            type="button"
            onClick={() => handleClick(rating)}
            onKeyDown={(e) => handleKeyDown(e, rating)}
            disabled={readOnly}
            className={cn(
              'focus-visible:ring-primary rounded-sm focus:outline-hidden focus-visible:ring-2',
              !readOnly && 'cursor-pointer transition-transform hover:scale-110',
            )}
            role="radio"
            aria-checked={value === rating}
            aria-label={`${rating} star${rating > 1 ? 's' : ''}`}
          >
            <Star
              className={cn(
                sizeClasses[size],
                rating <= value ? 'fill-yellow-400 text-yellow-400' : 'text-muted-foreground',
              )}
            />
          </button>
        ))}
      </div>
    </div>
  )
}
