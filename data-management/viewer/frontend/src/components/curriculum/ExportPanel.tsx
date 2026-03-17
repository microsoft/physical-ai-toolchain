/**
 * Export options for curriculum data.
 */

import { Database, Download, FileJson, Loader2 } from 'lucide-react'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { cn } from '@/lib/utils'

export type ExportFormat = 'json' | 'parquet' | 'csv'

export interface ExportOptions {
  format: ExportFormat
  filename: string
  includeMetadata: boolean
  includeAnnotations: boolean
  includeTrajectoryMetrics: boolean
  includeAnomalies: boolean
}

export interface ExportPanelProps {
  /** Number of episodes to export */
  episodeCount: number
  /** Export handler */
  onExport: (options: ExportOptions) => Promise<void>
  /** Whether export is in progress */
  isExporting?: boolean
  /** Whether export is disabled */
  disabled?: boolean
  /** Additional class names */
  className?: string
}

const FORMAT_OPTIONS: Array<{
  value: ExportFormat
  label: string
  description: string
  icon: typeof FileJson
}> = [
  {
    value: 'json',
    label: 'JSON',
    description: 'Human-readable, good for inspection',
    icon: FileJson,
  },
  {
    value: 'parquet',
    label: 'Parquet',
    description: 'Efficient columnar format for training',
    icon: Database,
  },
  {
    value: 'csv',
    label: 'CSV',
    description: 'Compatible with spreadsheets',
    icon: FileJson,
  },
]

/**
 * Export options panel for curriculum data.
 */
export function ExportPanel({
  episodeCount,
  onExport,
  isExporting = false,
  disabled = false,
  className,
}: ExportPanelProps) {
  const [options, setOptions] = useState<ExportOptions>({
    format: 'parquet',
    filename: `curriculum-${new Date().toISOString().slice(0, 10)}`,
    includeMetadata: true,
    includeAnnotations: true,
    includeTrajectoryMetrics: true,
    includeAnomalies: false,
  })

  const handleExport = () => {
    onExport(options)
  }

  const updateOption = <K extends keyof ExportOptions>(key: K, value: ExportOptions[K]) => {
    setOptions((prev) => ({ ...prev, [key]: value }))
  }

  const selectedFormat = FORMAT_OPTIONS.find((f) => f.value === options.format)

  return (
    <Card className={cn('', className)}>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          <Download className="h-4 w-4" />
          Export Curriculum
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Format selection */}
        <div className="space-y-2">
          <Label>Export Format</Label>
          <Select
            value={options.format}
            onValueChange={(value: string) => updateOption('format', value as ExportFormat)}
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {FORMAT_OPTIONS.map((format) => (
                <SelectItem key={format.value} value={format.value}>
                  <div className="flex items-center gap-2">
                    <format.icon className="h-4 w-4" />
                    <span>{format.label}</span>
                  </div>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {selectedFormat && (
            <p className="text-xs text-muted-foreground">{selectedFormat.description}</p>
          )}
        </div>

        {/* Filename */}
        <div className="space-y-2">
          <Label htmlFor="filename">Filename</Label>
          <Input
            id="filename"
            value={options.filename}
            onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
              updateOption('filename', e.target.value)
            }
            placeholder="curriculum-export"
          />
        </div>

        {/* Include options */}
        <div className="space-y-3">
          <Label>Include</Label>

          <div className="space-y-2">
            <div className="flex items-center space-x-2">
              <Checkbox
                id="include-metadata"
                checked={options.includeMetadata}
                onCheckedChange={(checked: boolean) =>
                  updateOption('includeMetadata', checked === true)
                }
              />
              <label
                htmlFor="include-metadata"
                className="text-sm leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70"
              >
                Episode metadata
              </label>
            </div>

            <div className="flex items-center space-x-2">
              <Checkbox
                id="include-annotations"
                checked={options.includeAnnotations}
                onCheckedChange={(checked: boolean) =>
                  updateOption('includeAnnotations', checked === true)
                }
              />
              <label
                htmlFor="include-annotations"
                className="text-sm leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70"
              >
                Annotation data
              </label>
            </div>

            <div className="flex items-center space-x-2">
              <Checkbox
                id="include-metrics"
                checked={options.includeTrajectoryMetrics}
                onCheckedChange={(checked: boolean) =>
                  updateOption('includeTrajectoryMetrics', checked === true)
                }
              />
              <label
                htmlFor="include-metrics"
                className="text-sm leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70"
              >
                Trajectory metrics
              </label>
            </div>

            <div className="flex items-center space-x-2">
              <Checkbox
                id="include-anomalies"
                checked={options.includeAnomalies}
                onCheckedChange={(checked: boolean) =>
                  updateOption('includeAnomalies', checked === true)
                }
              />
              <label
                htmlFor="include-anomalies"
                className="text-sm leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70"
              >
                Detected anomalies
              </label>
            </div>
          </div>
        </div>

        {/* Export button */}
        <Button
          onClick={handleExport}
          disabled={disabled || isExporting || episodeCount === 0}
          className="w-full"
        >
          {isExporting ? (
            <>
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              Exporting...
            </>
          ) : (
            <>
              <Download className="mr-2 h-4 w-4" />
              Export {episodeCount.toLocaleString()} Episodes
            </>
          )}
        </Button>
      </CardContent>
    </Card>
  )
}
