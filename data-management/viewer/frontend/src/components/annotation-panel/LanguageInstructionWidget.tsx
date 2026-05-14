/**
 * Language instruction annotation widget for VLA training.
 *
 * Captures natural language task descriptions, paraphrases for data
 * augmentation, and subtask decompositions for hierarchical policy
 * conditioning.
 */

import { Plus, Trash2 } from 'lucide-react'
import { useCallback, useMemo, useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { useEpisodeAnnotations } from '@/hooks/use-annotations'
import { cn } from '@/lib/utils'
import { useAnnotationStore } from '@/stores'
import { useDatasetStore } from '@/stores/dataset-store'
import { useEpisodeStore } from '@/stores/episode-store'
import type { InstructionSource } from '@/types'

import { FormSection } from './FormSection'

const SOURCE_OPTIONS: { value: InstructionSource; label: string }[] = [
  { value: 'human', label: 'Human' },
  { value: 'template', label: 'Template' },
  { value: 'llm-generated', label: 'LLM Generated' },
  { value: 'retroactive', label: 'Retroactive' },
]

export function LanguageInstructionWidget() {
  useEpisodeAnnotations('default')

  const currentAnnotation = useAnnotationStore((state) => state.currentAnnotation)
  const updateLanguageInstruction = useAnnotationStore((state) => state.updateLanguageInstruction)
  const clearLanguageInstruction = useAnnotationStore((state) => state.clearLanguageInstruction)

  const currentDataset = useDatasetStore((state) => state.currentDataset)
  const currentEpisode = useEpisodeStore((state) => state.currentEpisode)

  const datasetTaskDescription = useMemo(() => {
    const taskIndex = currentEpisode?.meta.taskIndex
    if (taskIndex == null || !currentDataset?.tasks.length) return undefined
    return currentDataset.tasks.find((t) => t.taskIndex === taskIndex)?.description
  }, [currentDataset?.tasks, currentEpisode?.meta.taskIndex])

  const langInst = currentAnnotation?.languageInstruction

  const [paraphraseInput, setParaphraseInput] = useState('')
  const [subtaskInput, setSubtaskInput] = useState('')

  const handleAddParaphrase = useCallback(() => {
    const trimmed = paraphraseInput.trim()
    if (!trimmed || !langInst) return
    updateLanguageInstruction({
      paraphrases: [...langInst.paraphrases, trimmed],
    })
    setParaphraseInput('')
  }, [paraphraseInput, langInst, updateLanguageInstruction])

  const handleRemoveParaphrase = useCallback(
    (index: number) => {
      if (!langInst) return
      updateLanguageInstruction({
        paraphrases: langInst.paraphrases.filter((_, i) => i !== index),
      })
    },
    [langInst, updateLanguageInstruction],
  )

  const handleAddSubtask = useCallback(() => {
    const trimmed = subtaskInput.trim()
    if (!trimmed || !langInst) return
    updateLanguageInstruction({
      subtaskInstructions: [...langInst.subtaskInstructions, trimmed],
    })
    setSubtaskInput('')
  }, [subtaskInput, langInst, updateLanguageInstruction])

  const handleRemoveSubtask = useCallback(
    (index: number) => {
      if (!langInst) return
      updateLanguageInstruction({
        subtaskInstructions: langInst.subtaskInstructions.filter((_, i) => i !== index),
      })
    },
    [langInst, updateLanguageInstruction],
  )

  if (!currentAnnotation) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Language Instruction</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-muted-foreground text-sm">No episode selected</p>
        </CardContent>
      </Card>
    )
  }

  if (!langInst) {
    return (
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm">Language Instruction</CardTitle>
        </CardHeader>
        <CardContent>
          {datasetTaskDescription ? (
            <>
              <p className="text-muted-foreground mb-2 text-xs">Dataset task description:</p>
              <p className="mb-3 text-sm font-medium">{datasetTaskDescription}</p>
              <Button
                variant="outline"
                size="sm"
                className="w-full"
                onClick={() =>
                  updateLanguageInstruction({
                    instruction: datasetTaskDescription,
                    source: 'template' as InstructionSource,
                  })
                }
              >
                <Plus className="mr-2 h-3 w-3" />
                Use as Instruction
              </Button>
            </>
          ) : (
            <>
              <p className="text-muted-foreground mb-3 text-xs">
                Add a natural language task description for VLA training.
              </p>
              <Button
                variant="outline"
                size="sm"
                className="w-full"
                onClick={() => updateLanguageInstruction({ instruction: '' })}
              >
                <Plus className="mr-2 h-3 w-3" />
                Add Instruction
              </Button>
            </>
          )}
        </CardContent>
      </Card>
    )
  }

  const hasInstruction = langInst.instruction.trim().length > 0

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center justify-between text-sm">
          Language Instruction
          {hasInstruction && (
            <Badge variant="secondary" className="text-xs font-normal">
              {langInst.source}
            </Badge>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <FormSection label="Task Instruction" htmlFor="lang-instruction">
          <Textarea
            id="lang-instruction"
            value={langInst.instruction}
            onChange={(e) => updateLanguageInstruction({ instruction: e.target.value })}
            placeholder="Describe the task, e.g. 'Hand the box from left arm to right arm'"
            className="min-h-[60px] resize-none"
          />
        </FormSection>

        <div className="grid grid-cols-2 gap-3">
          <FormSection label="Source" htmlFor="lang-source">
            <Select
              value={langInst.source}
              onValueChange={(value) =>
                updateLanguageInstruction({ source: value as InstructionSource })
              }
            >
              <SelectTrigger id="lang-source">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {SOURCE_OPTIONS.map((opt) => (
                  <SelectItem key={opt.value} value={opt.value}>
                    {opt.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </FormSection>

          <FormSection label="Language" htmlFor="lang-language">
            <Input
              id="lang-language"
              value={langInst.language}
              onChange={(e) => updateLanguageInstruction({ language: e.target.value })}
              maxLength={10}
              className="font-mono"
            />
          </FormSection>
        </div>

        <FormSection label={`Paraphrases (${langInst.paraphrases.length})`}>
          <div className="space-y-2">
            {langInst.paraphrases.map((p, i) => (
              <div
                key={i}
                className={cn('bg-muted/30 flex items-start gap-2 rounded-md border px-2 py-1.5')}
              >
                <span className="flex-1 text-xs">{p}</span>
                <button
                  type="button"
                  onClick={() => handleRemoveParaphrase(i)}
                  className="text-muted-foreground hover:text-destructive shrink-0"
                  aria-label={`Remove paraphrase ${i + 1}`}
                >
                  <Trash2 className="h-3 w-3" />
                </button>
              </div>
            ))}
            <div className="flex gap-2">
              <Input
                value={paraphraseInput}
                onChange={(e) => setParaphraseInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault()
                    handleAddParaphrase()
                  }
                }}
                placeholder="Add alternative phrasing..."
                className="text-xs"
              />
              <Button
                variant="outline"
                size="icon"
                className="shrink-0"
                onClick={handleAddParaphrase}
                disabled={!paraphraseInput.trim()}
              >
                <Plus className="h-3 w-3" />
              </Button>
            </div>
          </div>
        </FormSection>

        <FormSection label={`Subtask Instructions (${langInst.subtaskInstructions.length})`}>
          <div className="space-y-2">
            {langInst.subtaskInstructions.map((s, i) => (
              <div
                key={i}
                className="bg-muted/30 flex items-start gap-2 rounded-md border px-2 py-1.5"
              >
                <span className="text-muted-foreground shrink-0 text-xs font-medium">{i + 1}.</span>
                <span className="flex-1 text-xs">{s}</span>
                <button
                  type="button"
                  onClick={() => handleRemoveSubtask(i)}
                  className="text-muted-foreground hover:text-destructive shrink-0"
                  aria-label={`Remove subtask ${i + 1}`}
                >
                  <Trash2 className="h-3 w-3" />
                </button>
              </div>
            ))}
            <div className="flex gap-2">
              <Input
                value={subtaskInput}
                onChange={(e) => setSubtaskInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault()
                    handleAddSubtask()
                  }
                }}
                placeholder="Add subtask step..."
                className="text-xs"
              />
              <Button
                variant="outline"
                size="icon"
                className="shrink-0"
                onClick={handleAddSubtask}
                disabled={!subtaskInput.trim()}
              >
                <Plus className="h-3 w-3" />
              </Button>
            </div>
          </div>
        </FormSection>

        <Button
          variant="ghost"
          size="sm"
          className="text-destructive hover:text-destructive w-full text-xs"
          onClick={clearLanguageInstruction}
        >
          <Trash2 className="mr-2 h-3 w-3" />
          Remove Instruction
        </Button>
      </CardContent>
    </Card>
  )
}
