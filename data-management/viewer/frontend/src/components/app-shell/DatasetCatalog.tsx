import { ArrowLeft, ArrowRight, RefreshCw } from 'lucide-react'
import { useDeferredValue, useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Command, CommandInput, CommandItem, CommandList } from '@/components/ui/command'
import { useDatasetCatalog } from '@/hooks/use-datasets'
import type { DatasetCatalogOptions } from '@/lib/api-client'
import { recordDiagnosticEvent } from '@/lib/playback-diagnostics'

interface DatasetCatalogProps {
  visible: boolean
  selectedId: string
  onSelect: (id: string) => void
  onReturn: () => void
}

export function DatasetCatalog({ visible, selectedId, onSelect, onReturn }: DatasetCatalogProps) {
  const [text, setText] = useState('')
  const query = useDeferredValue(text)
  const [group, setGroup] = useState('')
  const [sort, setSort] = useState<DatasetCatalogOptions['sort']>('name')
  const [offset, setOffset] = useState(0)
  const [snapshotId, setSnapshotId] = useState<string>()
  const [refreshing, setRefreshing] = useState(false)
  const [refreshError, setRefreshError] = useState(false)
  const root = useRef<HTMLElement>(null)
  const input = useRef<HTMLInputElement>(null)
  const lastSelection = useRef('')
  const catalog = useDatasetCatalog(
    { query, group: group || undefined, sort, offset, limit: 25, snapshotId },
    visible,
  )
  const page = catalog.data

  useEffect(() => {
    if (catalog.error)
      recordDiagnosticEvent('navigation', 'catalog-fetch-error', { retained: !!page })
  }, [catalog.error, page])

  useEffect(() => {
    if (!visible) return
    const selected = lastSelection.current
      ? root.current?.querySelector<HTMLElement>(
          `[data-dataset="${CSS.escape(lastSelection.current)}"]`,
        )
      : null
    if (selected) selected.focus()
    else input.current?.focus()
  }, [visible, selectedId])

  useEffect(() => {
    if (!visible || !selectedId) return
    const escapeCatalog = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        onReturn()
      }
    }
    document.addEventListener('keydown', escapeCatalog)
    return () => document.removeEventListener('keydown', escapeCatalog)
  }, [visible, selectedId, onReturn])

  const resetPage = () => {
    setOffset(0)
    setSnapshotId(page?.snapshotId)
  }
  const refresh = async () => {
    recordDiagnosticEvent('navigation', 'catalog-refresh')
    setRefreshing(true)
    setRefreshError(false)
    try {
      const updated = await catalog.refreshCatalog()
      recordDiagnosticEvent('navigation', 'catalog-refresh-result', {
        failed: updated.refreshFailed,
        count: updated.total,
      })
      setSnapshotId(updated.snapshotId)
      setOffset(0)
    } catch {
      recordDiagnosticEvent('navigation', 'catalog-refresh-error')
      setRefreshError(true)
    } finally {
      setRefreshing(false)
    }
  }

  return (
    <main
      id="dataset-catalog"
      ref={root}
      hidden={!visible}
      className="min-h-0 flex-1 overflow-auto p-4"
      aria-label="Dataset catalog"
    >
      <div className="mx-auto max-w-5xl space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-xl font-semibold">Datasets</h2>
          <div className="flex gap-2">
            {selectedId && (
              <Button variant="outline" onClick={onReturn}>
                <ArrowLeft className="mr-2 size-4" />
                Return to episode
              </Button>
            )}
            <Button
              variant="outline"
              size="icon"
              aria-label="Refresh catalog"
              title="Refresh catalog"
              disabled={refreshing}
              onClick={() => void refresh()}
            >
              <RefreshCw className={refreshing ? 'size-4 animate-spin' : 'size-4'} />
            </Button>
          </div>
        </div>
        <div className="flex flex-wrap gap-4">
          <label className="flex items-center gap-2 text-sm">
            Group
            <select
              className="bg-background min-w-0 rounded-md border p-2"
              value={group}
              onChange={(event) => {
                setGroup(event.target.value)
                resetPage()
              }}
            >
              <option value="">All groups</option>
              {page?.groups.map((name) => (
                <option key={name} value={name}>
                  {name.split('--').join('/')}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-2 text-sm">
            Sort
            <select
              className="bg-background rounded-md border p-2"
              value={sort}
              onChange={(event) => {
                setSort(event.target.value as DatasetCatalogOptions['sort'])
                resetPage()
              }}
            >
              <option value="name">Name</option>
              <option value="episodes-desc">Most episodes</option>
              <option value="episodes">Fewest episodes</option>
            </select>
          </label>
        </div>
        {(refreshError || page?.refreshFailed) && (
          <p role="alert">
            Catalog refresh failed. Previous discovery is retained. Retry with Refresh catalog.
          </p>
        )}
        {catalog.error && (
          <p role="alert">Catalog unavailable or snapshot expired. Refresh the catalog to retry.</p>
        )}
        {page?.stale && !page.refreshFailed && <p role="status">Catalog snapshot is stale.</p>}
        <Command label="Filter datasets" shouldFilter={false} className="bg-transparent">
          <CommandInput
            ref={input}
            value={text}
            onValueChange={(value) => {
              setText(value)
              resetPage()
            }}
            placeholder="Filter datasets"
            role="combobox"
            aria-expanded={visible}
            aria-controls="catalog-results"
            aria-autocomplete="list"
          />
          <CommandList
            id="catalog-results"
            role="listbox"
            aria-label="Available datasets"
            className="max-h-none"
            aria-busy={catalog.isFetching}
          >
            {page?.items.map((dataset) => (
              <CommandItem
                key={dataset.id}
                value={dataset.id}
                data-dataset={dataset.id}
                tabIndex={0}
                role="option"
                aria-selected={dataset.id === selectedId}
                onSelect={() => {
                  lastSelection.current = dataset.id
                  onSelect(dataset.id)
                }}
                className="flex flex-wrap items-center justify-between gap-2 border-b py-3"
              >
                <span className="min-w-0 flex-1 break-all">
                  <span className="block font-medium">{dataset.id}</span>
                  <span className="text-muted-foreground text-sm">{dataset.name}</span>
                </span>
                <span className="text-muted-foreground text-sm">
                  {dataset.totalEpisodes} episodes{dataset.format ? ` · ${dataset.format}` : ''}
                </span>
              </CommandItem>
            ))}
          </CommandList>
        </Command>
        <p role="status" aria-live="polite" className="text-muted-foreground text-sm">
          {catalog.isLoading
            ? 'Loading datasets...'
            : page?.total === 0
              ? page.catalogTotal === 0
                ? 'No datasets available.'
                : 'No datasets match the current filters.'
              : page
                ? `${offset + 1}-${Math.min(offset + 25, page.total)} of ${page.total} datasets`
                : ''}
        </p>
        <nav aria-label="Catalog pages" className="flex gap-2">
          <Button
            variant="outline"
            size="icon"
            aria-label="Previous catalog page"
            title="Previous catalog page"
            disabled={!page || offset === 0 || catalog.isFetching}
            onClick={() => {
              setSnapshotId(page?.snapshotId)
              setOffset(Math.max(0, offset - 25))
            }}
          >
            <ArrowLeft className="size-4" />
          </Button>
          <Button
            variant="outline"
            size="icon"
            aria-label="Next catalog page"
            title="Next catalog page"
            disabled={!page || offset + 25 >= page.total || catalog.isFetching}
            onClick={() => {
              setSnapshotId(page?.snapshotId)
              setOffset(offset + 25)
            }}
          >
            <ArrowRight className="size-4" />
          </Button>
        </nav>
      </div>
    </main>
  )
}
