/**
 * LabelPanel - multi-select label tagging for episodes.
 *
 * Displays available labels as toggleable chips and allows adding custom labels.
 */

import { Check, Download, Plus, X } from 'lucide-react'
import { useCallback, useMemo, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import type { ImportableAnalysisField } from '@/hooks/use-labels'
import {
  IMPORTABLE_ANALYSIS_FIELDS,
  useAddLabelOption,
  useCurrentEpisodeLabels,
  useImportAnalysisLabels,
  useRemoveLabelOption,
} from '@/hooks/use-labels'
import { cn } from '@/lib/utils'
import { DEFAULT_LABELS, useLabelStore } from '@/stores/label-store'

interface LabelPanelProps {
  episodeIndex: number
}

const ANALYSIS_FIELD_LABELS: Record<ImportableAnalysisField, string> = {
  object: 'Object',
  pick_from: 'Pick location',
  grasp_success: 'Grasp',
  place_success: 'Place',
  motion_score: 'Motion score',
  motion_flags: 'Motion flags',
  source: 'Source',
}

export function LabelPanel({ episodeIndex }: LabelPanelProps) {
  const [newLabel, setNewLabel] = useState('')
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [importMessage, setImportMessage] = useState<string | null>(null)
  const [importPrefix, setImportPrefix] = useState('')
  const [shouldOverwrite, setShouldOverwrite] = useState(false)
  const [pendingDeleteLabel, setPendingDeleteLabel] = useState<string | null>(null)
  const availableLabels = useLabelStore((state) => state.availableLabels)
  const episodeAnalysis = useLabelStore((state) => state.episodeAnalysis)
  const { currentLabels, toggle } = useCurrentEpisodeLabels(episodeIndex)
  const addOption = useAddLabelOption()
  const removeOption = useRemoveLabelOption()
  const importLabels = useImportAnalysisLabels()

  const importableFields = useMemo(() => {
    const present = new Set<ImportableAnalysisField>()
    for (const record of Object.values(episodeAnalysis)) {
      for (const field of IMPORTABLE_ANALYSIS_FIELDS) {
        const value = record[field]
        if (value === null || value === undefined) continue
        if (Array.isArray(value) && value.length === 0) continue
        present.add(field)
      }
    }
    return IMPORTABLE_ANALYSIS_FIELDS.filter((field) => present.has(field))
  }, [episodeAnalysis])

  const handleAddLabel = useCallback(async () => {
    const normalized = newLabel.trim().toUpperCase()
    if (!normalized) return
    try {
      await addOption.mutateAsync(normalized)
      setNewLabel('')
      setErrorMessage(null)
    } catch (error) {
      const detail = error instanceof Error ? error.message : 'Unknown error'
      setErrorMessage(`Failed to add label: ${detail}`)
    }
  }, [newLabel, addOption])

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter') {
        e.preventDefault()
        handleAddLabel()
      }
    },
    [handleAddLabel],
  )

  const handleDeleteLabel = useCallback((label: string) => {
    setPendingDeleteLabel(label)
  }, [])

  const handleToggleLabel = useCallback(
    async (label: string) => {
      try {
        await toggle(label)
        setErrorMessage(null)
      } catch (error) {
        const detail = error instanceof Error ? error.message : 'Unknown error'
        setErrorMessage(`Failed to update labels: ${detail}`)
      }
    },
    [toggle],
  )

  const confirmDeleteLabel = useCallback(async () => {
    if (!pendingDeleteLabel) return

    try {
      await removeOption.mutateAsync(pendingDeleteLabel)
      setErrorMessage(null)
      setPendingDeleteLabel(null)
    } catch (error) {
      const detail = error instanceof Error ? error.message : 'Unknown error'
      setErrorMessage(`Failed to delete label: ${detail}`)
    }
  }, [pendingDeleteLabel, removeOption])

  const handleImport = useCallback(
    async (field: ImportableAnalysisField) => {
      setImportMessage(null)
      try {
        const result = await importLabels.mutateAsync({
          field,
          prefix: importPrefix.trim() || undefined,
          overwrite: shouldOverwrite,
        })
        const added = result.labels_added.length
        setImportMessage(
          `Imported ${added} ${ANALYSIS_FIELD_LABELS[field]} label${added === 1 ? '' : 's'} ` +
            `across ${result.episodes_updated} episode${result.episodes_updated === 1 ? '' : 's'}.`,
        )
        setErrorMessage(null)
      } catch (error) {
        const detail = error instanceof Error ? error.message : 'Unknown error'
        setErrorMessage(`Failed to import labels: ${detail}`)
      }
    },
    [importLabels, importPrefix, shouldOverwrite],
  )

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-medium">Episode Labels</h3>
      </div>

      {errorMessage && <p className="text-destructive text-xs">{errorMessage}</p>}

      {/* Label toggles */}
      <div className="flex flex-wrap gap-2">
        {availableLabels.map((label) => {
          const isSelected = currentLabels.includes(label)
          const isProtected = DEFAULT_LABELS.includes(label)
          return (
            <div
              key={label}
              className={cn(
                'inline-flex items-center rounded-full border transition-all',
                isSelected
                  ? 'bg-primary text-primary-foreground border-transparent shadow-xs'
                  : 'text-foreground hover:bg-accent',
              )}
            >
              <button
                type="button"
                onClick={() => void handleToggleLabel(label)}
                className="focus-visible:ring-ring inline-flex items-center gap-1 rounded-l-full px-2.5 py-0.5 text-xs font-semibold focus:outline-hidden focus-visible:ring-2 focus-visible:ring-offset-2"
              >
                {isSelected && <Check className="mr-1 h-3 w-3" />}
                {label}
              </button>
              {!isProtected && (
                <button
                  type="button"
                  onClick={() => handleDeleteLabel(label)}
                  aria-label={`Delete label ${label}`}
                  title={`Delete label ${label}`}
                  disabled={removeOption.isPending}
                  className={cn(
                    'focus-visible:ring-ring mr-1 inline-flex h-5 w-5 items-center justify-center rounded-full focus:outline-hidden focus-visible:ring-2 focus-visible:ring-offset-2',
                    isSelected ? 'hover:bg-primary-foreground/15' : 'hover:bg-accent-foreground/10',
                  )}
                >
                  <X className="h-3 w-3" />
                </button>
              )}
            </div>
          )
        })}
      </div>

      <p className="text-muted-foreground text-xs">
        Built-in labels stay available. Custom labels require confirmation before deletion.
      </p>

      {/* Add custom label */}
      <div className="flex gap-2">
        <Input
          value={newLabel}
          onChange={(e) => setNewLabel(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Add custom label..."
          className="h-8 text-sm"
        />
        <Button
          size="sm"
          variant="outline"
          onClick={() => void handleAddLabel()}
          disabled={!newLabel.trim() || addOption.isPending}
          className="h-8 gap-1 px-3"
        >
          <Plus className="h-3 w-3" />
          Add
        </Button>
      </div>

      {/* Current labels summary */}
      {currentLabels.length > 0 && (
        <div className="text-muted-foreground text-xs">Applied: {currentLabels.join(', ')}</div>
      )}

      {/* Import analysis fields as labels */}
      {importableFields.length > 0 && (
        <div className="space-y-1.5 border-t pt-3">
          <p className="text-muted-foreground text-xs font-medium">Import from analysis</p>
          <div className="grid gap-2 sm:grid-cols-2">
            <div className="space-y-1">
              <Label htmlFor="analysis-label-prefix" className="text-xs">
                Label prefix
              </Label>
              <Input
                id="analysis-label-prefix"
                value={importPrefix}
                onChange={(event) => setImportPrefix(event.target.value)}
                placeholder="Use field default"
                maxLength={32}
                className="h-7 text-xs"
              />
            </div>
            <div className="flex items-end gap-2 pb-1">
              <Checkbox
                id="overwrite-analysis-labels"
                checked={shouldOverwrite}
                onCheckedChange={(checked) => setShouldOverwrite(checked === true)}
              />
              <Label htmlFor="overwrite-analysis-labels" className="text-xs">
                Replace existing imported labels
              </Label>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            {importableFields.map((field) => (
              <Button
                key={field}
                size="sm"
                variant="outline"
                onClick={() => void handleImport(field)}
                disabled={importLabels.isPending}
                className="h-7 gap-1 px-2 text-xs"
              >
                <Download className="h-3 w-3" />
                {ANALYSIS_FIELD_LABELS[field]}
              </Button>
            ))}
          </div>
          {importMessage && <p className="text-muted-foreground text-xs">{importMessage}</p>}
        </div>
      )}

      <Dialog
        open={pendingDeleteLabel !== null}
        onOpenChange={(open) => !open && setPendingDeleteLabel(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete Label</DialogTitle>
            <DialogDescription>
              {pendingDeleteLabel
                ? `Delete ${pendingDeleteLabel} from the dataset label list and remove it from every episode assignment?`
                : 'Delete this label from the dataset.'}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setPendingDeleteLabel(null)}>
              Cancel
            </Button>
            <Button type="button" variant="destructive" onClick={() => void confirmDeleteLabel()}>
              Delete Label
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
