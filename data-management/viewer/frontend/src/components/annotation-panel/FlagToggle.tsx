/**
 * Flag toggle chip component for trajectory quality flags.
 */

import { cn } from '@/lib/utils'

interface FlagToggleProps {
  /** Flag label */
  label: string
  /** Whether the flag is active */
  active: boolean
  /** Callback when toggled */
  onToggle: () => void
  /** Optional keyboard shortcut hint */
  shortcut?: string
}

/**
 * Toggleable chip for trajectory quality flags.
 *
 * @example
 * ```tsx
 * <FlagToggle
 *   label="Jittery"
 *   active={flags.jittery}
 *   onToggle={() => toggleFlag('jittery')}
 *   shortcut="J"
 * />
 * ```
 */
export function FlagToggle({ label, active, onToggle, shortcut }: FlagToggleProps) {
  return (
    <button
      type="button"
      onClick={onToggle}
      className={cn(
        'relative rounded-full px-3 py-1.5 text-xs font-medium transition-colors',
        'focus-visible:ring-primary focus:outline-hidden focus-visible:ring-2',
        active
          ? 'bg-red-100 text-red-700 hover:bg-red-200'
          : 'bg-muted text-muted-foreground hover:bg-muted/80',
      )}
    >
      {label}
      {shortcut && (
        <span className="bg-background absolute -top-1 -right-1 rounded-sm border px-1 text-[9px]">
          {shortcut}
        </span>
      )}
    </button>
  )
}
