/**
 * Main curriculum generator page component.
 */

import { GraduationCap, RefreshCw, Save } from 'lucide-react'
import { useCallback, useMemo, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useToast } from '@/hooks/use-toast'
import { cn } from '@/lib/utils'

import { CurriculumPreview, type EpisodePreviewItem } from './CurriculumPreview'
import { type ExportOptions, ExportPanel } from './ExportPanel'
import { FilterBuilder, type FilterCondition } from './FilterBuilder'

export interface CurriculumGeneratorProps {
  /** Dataset identifier */
  datasetId: string
  /** Episode data for filtering */
  episodes?: EpisodePreviewItem[]
  /** Whether episodes are loading */
  isLoading?: boolean
  /** Handler for saving curriculum preset */
  onSavePreset?: (name: string, conditions: FilterCondition[]) => void
  /** Handler for exporting curriculum */
  onExport?: (episodeIds: string[], options: ExportOptions) => Promise<void>
  /** Additional class names */
  className?: string
}

/**
 * Main curriculum generator with filtering and export.
 */
export function CurriculumGenerator({
  datasetId: _datasetId,
  episodes = [],
  isLoading = false,
  onSavePreset,
  onExport,
  className,
}: CurriculumGeneratorProps) {
  const { toast } = useToast()
  const [conditions, setConditions] = useState<FilterCondition[]>([])
  const [isExporting, setIsExporting] = useState(false)
  const [activeTab, setActiveTab] = useState('filters')

  // Filter episodes based on conditions
  const filteredEpisodes = useMemo(() => {
    if (conditions.length === 0) return episodes

    return episodes.filter((episode) => {
      return conditions.every((condition) => {
        const getValue = (field: string): number | boolean | undefined => {
          switch (field) {
            case 'task_completion_rating':
              return episode.task_completion_rating
            case 'trajectory_quality_score':
              return episode.trajectory_quality_score
            case 'has_anomalies':
              return episode.has_anomalies
            case 'has_issues':
              return episode.has_issues
            default:
              return undefined
          }
        }

        const value = getValue(condition.field)
        if (value === undefined) return true // Skip if field not available

        switch (condition.operator) {
          case 'equals':
            return value === condition.value
          case 'not_equals':
            return value !== condition.value
          case 'greater_than':
            return typeof value === 'number' && value > (condition.value as number)
          case 'less_than':
            return typeof value === 'number' && value < (condition.value as number)
          case 'greater_or_equal':
            return typeof value === 'number' && value >= (condition.value as number)
          case 'less_or_equal':
            return typeof value === 'number' && value <= (condition.value as number)
          case 'is_true':
            return value === true
          case 'is_false':
            return value === false
          default:
            return true
        }
      })
    })
  }, [episodes, conditions])

  const handleExport = useCallback(
    async (options: ExportOptions) => {
      if (!onExport) return

      setIsExporting(true)
      try {
        const episodeIds = filteredEpisodes.map((e) => e.id)
        await onExport(episodeIds, options)
        toast({
          title: 'Export complete',
          description: `Exported ${episodeIds.length} episodes to ${options.filename}.${options.format}`,
        })
      } catch (error) {
        toast({
          title: 'Export failed',
          description: error instanceof Error ? error.message : 'Unknown error',
          variant: 'destructive',
        })
      } finally {
        setIsExporting(false)
      }
    },
    [filteredEpisodes, onExport, toast],
  )

  const handleSavePreset = useCallback(() => {
    if (!onSavePreset) return

    const name = prompt('Enter preset name:')
    if (name) {
      onSavePreset(name, conditions)
      toast({
        title: 'Preset saved',
        description: `Saved filter preset "${name}"`,
      })
    }
  }, [conditions, onSavePreset, toast])

  const handleClearFilters = useCallback(() => {
    setConditions([])
  }, [])

  return (
    <div className={cn('space-y-6', className)}>
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <GraduationCap className="text-primary h-6 w-6" />
          <h1 className="text-2xl font-bold">Curriculum Generator</h1>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={handleClearFilters}>
            <RefreshCw className="mr-1 h-4 w-4" />
            Clear Filters
          </Button>
          {onSavePreset && (
            <Button
              variant="outline"
              size="sm"
              onClick={handleSavePreset}
              disabled={conditions.length === 0}
            >
              <Save className="mr-1 h-4 w-4" />
              Save Preset
            </Button>
          )}
        </div>
      </div>

      {/* Main content */}
      <div className="grid gap-6 lg:grid-cols-3">
        {/* Left: Filters and Preview */}
        <div className="space-y-6 lg:col-span-2">
          <Tabs value={activeTab} onValueChange={setActiveTab}>
            <TabsList>
              <TabsTrigger value="filters">Filters</TabsTrigger>
              <TabsTrigger value="preview">Preview ({filteredEpisodes.length})</TabsTrigger>
            </TabsList>

            <TabsContent value="filters" className="mt-4">
              <Card>
                <CardContent className="pt-6">
                  <FilterBuilder conditions={conditions} onChange={setConditions} />
                </CardContent>
              </Card>
            </TabsContent>

            <TabsContent value="preview" className="mt-4">
              <CurriculumPreview
                episodes={filteredEpisodes}
                isLoading={isLoading}
                totalCount={filteredEpisodes.length}
                previewLimit={50}
              />
            </TabsContent>
          </Tabs>
        </div>

        {/* Right: Export panel */}
        <div>
          <ExportPanel
            episodeCount={filteredEpisodes.length}
            onExport={handleExport}
            isExporting={isExporting}
            disabled={filteredEpisodes.length === 0}
          />

          {/* Quick stats */}
          <Card className="mt-4">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">Selection Summary</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              <div className="flex justify-between">
                <span className="text-muted-foreground">Total episodes:</span>
                <span className="font-medium">{episodes.length.toLocaleString()}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">Filtered:</span>
                <span className="font-medium">{filteredEpisodes.length.toLocaleString()}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">Active filters:</span>
                <span className="font-medium">{conditions.length}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">Selection rate:</span>
                <span className="font-medium">
                  {episodes.length > 0
                    ? Math.round((filteredEpisodes.length / episodes.length) * 100)
                    : 0}
                  %
                </span>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}
