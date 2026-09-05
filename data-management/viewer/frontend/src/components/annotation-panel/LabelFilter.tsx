/**
 * LabelFilter - filter episodes by label in the sidebar.
 */

import { Search, X } from 'lucide-react'
import { useMemo, useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { useLabelStore } from '@/stores/label-store'

/** Show the search box only once the label list gets long enough to warrant it. */
const SEARCH_THRESHOLD = 8

export function LabelFilter({ compact = false }: { compact?: boolean }) {
  const availableLabels = useLabelStore((state) => state.availableLabels)
  const filterLabels = useLabelStore((state) => state.filterLabels)
  const toggleFilterLabel = useLabelStore((state) => state.toggleFilterLabel)
  const isLoaded = useLabelStore((state) => state.isLoaded)
  const [search, setSearch] = useState('')

  const visibleLabels = useMemo(() => {
    const query = search.trim().toLowerCase()
    if (!query) return availableLabels
    return availableLabels.filter((label) => label.toLowerCase().includes(query))
  }, [availableLabels, search])

  if (!isLoaded || availableLabels.length === 0) return null

  const showSearch = availableLabels.length > SEARCH_THRESHOLD

  return (
    <div className={compact ? 'space-y-1' : 'space-y-1 border-b px-2 py-2'}>
      <div className="text-muted-foreground text-xs font-medium">Filter by label</div>
      {showSearch && (
        <div className="relative">
          <Search className="text-muted-foreground pointer-events-none absolute top-1/2 left-2 h-3 w-3 -translate-y-1/2" />
          <Input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search labels..."
            aria-label="Search labels"
            className="h-6 pl-6 text-[11px]"
          />
        </div>
      )}
      <div className="flex max-h-48 flex-wrap gap-1 overflow-y-auto">
        {visibleLabels.map((label) => {
          const isActive = filterLabels.includes(label)
          return (
            <button
              key={label}
              onClick={() => toggleFilterLabel(label)}
              className="focus:outline-hidden"
            >
              <Badge
                variant={isActive ? 'default' : 'outline'}
                className={`h-5 cursor-pointer px-1.5 py-0 text-[10px] select-none ${
                  isActive ? 'shadow-xs' : 'opacity-60 hover:opacity-100'
                }`}
              >
                {label}
                {isActive && <X className="ml-0.5 h-2.5 w-2.5" />}
              </Badge>
            </button>
          )
        })}
        {visibleLabels.length === 0 && (
          <span className="text-muted-foreground text-[10px]">No labels match your search.</span>
        )}
      </div>
    </div>
  )
}
