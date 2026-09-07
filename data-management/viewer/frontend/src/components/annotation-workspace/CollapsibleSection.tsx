/**
 * Foldable wrapper for a right-pane annotation panel.
 *
 * When expanded it renders a subtle collapse chevron above the panel (the
 * panel keeps its own header, so no title is duplicated). When collapsed it
 * shows a compact titled bar so the section can be identified and reopened,
 * letting the annotator hide panels to focus on a single one.
 */

import { ChevronDown, ChevronRight } from 'lucide-react'
import { type ReactNode } from 'react'

import { useSectionOpen } from '@/stores/viewer-settings-store'

interface CollapsibleSectionProps {
  /** Label shown when collapsed and used as the toggle's accessible name. */
  title: string
  children: ReactNode
  /** Whether the section starts expanded. Defaults to true. */
  defaultOpen?: boolean
  /** Extra classes for the section wrapper (e.g. separator borders). */
  className?: string
  /**
   * Stable id for persisting the fold state across episode switches. Defaults
   * to the title.
   */
  sectionId?: string
}

export function CollapsibleSection({
  title,
  children,
  defaultOpen = true,
  className,
  sectionId,
}: CollapsibleSectionProps) {
  const { open, toggle } = useSectionOpen(sectionId ?? title, defaultOpen)

  if (!open) {
    return (
      <div className={className} data-testid="collapsible-section">
        <button
          type="button"
          onClick={toggle}
          aria-expanded={false}
          aria-label={`Expand ${title}`}
          className="text-muted-foreground hover:text-foreground flex w-full items-center gap-1.5 text-sm font-medium"
        >
          <ChevronRight className="size-4 shrink-0" />
          <span>{title}</span>
        </button>
      </div>
    )
  }

  return (
    <div className={className} data-testid="collapsible-section">
      <div className="mb-1 flex justify-end">
        <button
          type="button"
          onClick={toggle}
          aria-expanded
          aria-label={`Collapse ${title}`}
          title={`Collapse ${title}`}
          className="text-muted-foreground hover:text-foreground hover:bg-muted rounded-md p-1"
        >
          <ChevronDown className="size-4" />
        </button>
      </div>
      {children}
    </div>
  )
}
