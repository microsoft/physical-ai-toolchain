/**
 * Filter condition builder for curriculum generation.
 */

import { Filter, Plus, X } from 'lucide-react'
import { useCallback } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { cn } from '@/lib/utils'

export type FilterField =
  | 'task_completion_rating'
  | 'trajectory_quality_score'
  | 'has_anomalies'
  | 'has_issues'
  | 'smoothness'
  | 'efficiency'
  | 'cluster_id'

export type FilterOperator =
  | 'equals'
  | 'not_equals'
  | 'greater_than'
  | 'less_than'
  | 'greater_or_equal'
  | 'less_or_equal'
  | 'contains'
  | 'is_true'
  | 'is_false'

export interface FilterCondition {
  id: string
  field: FilterField
  operator: FilterOperator
  value: string | number | boolean
}

export interface FilterBuilderProps {
  /** Current filter conditions */
  conditions: FilterCondition[]
  /** Handler for condition changes */
  onChange: (conditions: FilterCondition[]) => void
  /** Additional class names */
  className?: string
}

const FIELD_OPTIONS: Array<{
  value: FilterField
  label: string
  type: 'number' | 'boolean' | 'string'
}> = [
  { value: 'task_completion_rating', label: 'Task Completion Rating', type: 'number' },
  { value: 'trajectory_quality_score', label: 'Trajectory Quality Score', type: 'number' },
  { value: 'has_anomalies', label: 'Has Anomalies', type: 'boolean' },
  { value: 'has_issues', label: 'Has Issues', type: 'boolean' },
  { value: 'smoothness', label: 'Smoothness', type: 'number' },
  { value: 'efficiency', label: 'Efficiency', type: 'number' },
  { value: 'cluster_id', label: 'Cluster ID', type: 'number' },
]

const NUMERIC_OPERATORS: Array<{ value: FilterOperator; label: string }> = [
  { value: 'equals', label: '=' },
  { value: 'not_equals', label: '≠' },
  { value: 'greater_than', label: '>' },
  { value: 'less_than', label: '<' },
  { value: 'greater_or_equal', label: '≥' },
  { value: 'less_or_equal', label: '≤' },
]

const BOOLEAN_OPERATORS: Array<{ value: FilterOperator; label: string }> = [
  { value: 'is_true', label: 'Is True' },
  { value: 'is_false', label: 'Is False' },
]

/**
 * Filter condition builder component.
 */
export function FilterBuilder({ conditions, onChange, className }: FilterBuilderProps) {
  const generateId = () => `filter-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`

  const addCondition = useCallback(() => {
    const newCondition: FilterCondition = {
      id: generateId(),
      field: 'task_completion_rating',
      operator: 'greater_or_equal',
      value: 3,
    }
    onChange([...conditions, newCondition])
  }, [conditions, onChange])

  const removeCondition = useCallback(
    (id: string) => {
      onChange(conditions.filter((c) => c.id !== id))
    },
    [conditions, onChange],
  )

  const updateCondition = useCallback(
    (id: string, updates: Partial<FilterCondition>) => {
      onChange(conditions.map((c) => (c.id === id ? { ...c, ...updates } : c)))
    },
    [conditions, onChange],
  )

  const getFieldType = (field: FilterField) => {
    return FIELD_OPTIONS.find((f) => f.value === field)?.type || 'string'
  }

  const getOperatorsForField = (field: FilterField) => {
    const fieldType = getFieldType(field)
    return fieldType === 'boolean' ? BOOLEAN_OPERATORS : NUMERIC_OPERATORS
  }

  return (
    <div className={cn('space-y-3', className)}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Filter className="h-4 w-4 text-muted-foreground" />
          <span className="font-medium">Filter Conditions</span>
          <Badge variant="secondary">{conditions.length}</Badge>
        </div>
        <Button variant="outline" size="sm" onClick={addCondition}>
          <Plus className="mr-1 h-4 w-4" />
          Add Condition
        </Button>
      </div>

      {conditions.length === 0 ? (
        <Card className="border-dashed">
          <CardContent className="flex flex-col items-center justify-center py-8">
            <Filter className="mb-2 h-8 w-8 text-muted-foreground/50" />
            <p className="text-sm text-muted-foreground">
              No filters applied. Add conditions to filter episodes.
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-2">
          {conditions.map((condition, index) => {
            const fieldType = getFieldType(condition.field)
            const operators = getOperatorsForField(condition.field)

            return (
              <Card key={condition.id} className="p-3">
                <div className="flex items-center gap-2">
                  {/* AND connector */}
                  {index > 0 && (
                    <Badge variant="outline" className="shrink-0">
                      AND
                    </Badge>
                  )}

                  {/* Field selector */}
                  <Select
                    value={condition.field}
                    onValueChange={(value: string) =>
                      updateCondition(condition.id, {
                        field: value as FilterField,
                        operator: getOperatorsForField(value as FilterField)[0].value,
                        value: getFieldType(value as FilterField) === 'boolean' ? true : 0,
                      })
                    }
                  >
                    <SelectTrigger className="w-48">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {FIELD_OPTIONS.map((field) => (
                        <SelectItem key={field.value} value={field.value}>
                          {field.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>

                  {/* Operator selector */}
                  <Select
                    value={condition.operator}
                    onValueChange={(value: string) =>
                      updateCondition(condition.id, { operator: value as FilterOperator })
                    }
                  >
                    <SelectTrigger className="w-24">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {operators.map((op) => (
                        <SelectItem key={op.value} value={op.value}>
                          {op.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>

                  {/* Value input (not shown for boolean operators) */}
                  {fieldType !== 'boolean' && (
                    <Input
                      type="number"
                      value={condition.value as number}
                      onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                        updateCondition(condition.id, {
                          value: parseFloat(e.target.value) || 0,
                        })
                      }
                      className="w-24"
                      min={0}
                      max={fieldType === 'number' ? 5 : undefined}
                      step={
                        condition.field.includes('score') || condition.field.includes('rating')
                          ? 1
                          : 0.1
                      }
                    />
                  )}

                  {/* Remove button */}
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => removeCondition(condition.id)}
                    className="h-8 w-8 shrink-0"
                  >
                    <X className="h-4 w-4" />
                  </Button>
                </div>
              </Card>
            )
          })}
        </div>
      )}
    </div>
  )
}
