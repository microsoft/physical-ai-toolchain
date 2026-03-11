/**
 * Task completeness annotation widget.
 *
 * Provides controls for rating episode task completion with
 * success/partial/failure/unknown options and conditional fields.
 */

import { useEffect } from 'react';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { cn } from '@/lib/utils';
import { useAnnotationStore } from '@/stores';
import type { TaskCompletenessRating } from '@/types';

import { FormSection } from './FormSection';

/**
 * Widget for annotating task completeness with keyboard shortcuts.
 *
 * Keyboard shortcuts:
 * - S: Mark as Success
 * - P: Mark as Partial
 * - F: Mark as Failure
 *
 * @example
 * ```tsx
 * <TaskCompletenessWidget />
 * ```
 */
export function TaskCompletenessWidget() {
  const currentAnnotation = useAnnotationStore((state) => state.currentAnnotation);
  const updateTaskCompleteness = useAnnotationStore(
    (state) => state.updateTaskCompleteness
  );

  const taskCompleteness = currentAnnotation?.taskCompleteness;

  // Keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (
        e.target instanceof HTMLInputElement ||
        e.target instanceof HTMLTextAreaElement
      ) {
        return;
      }

      switch (e.key.toLowerCase()) {
        case 's':
          if (!e.ctrlKey && !e.metaKey) {
            updateTaskCompleteness({ rating: 'success' });
          }
          break;
        case 'p':
          updateTaskCompleteness({ rating: 'partial' });
          break;
        case 'f':
          updateTaskCompleteness({ rating: 'failure' });
          break;
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [updateTaskCompleteness]);

  const ratingOptions: { value: TaskCompletenessRating; label: string; key: string }[] = [
    { value: 'success', label: 'Success', key: 'S' },
    { value: 'partial', label: 'Partial', key: 'P' },
    { value: 'failure', label: 'Failure', key: 'F' },
    { value: 'unknown', label: 'Unknown', key: '-' },
  ];

  const subtaskOptions = [
    'approach',
    'grasp',
    'lift',
    'transport',
    'place',
    'release',
    'retreat',
  ];

  if (!currentAnnotation) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Task Completeness</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">No episode selected</p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm flex items-center justify-between">
          Task Completeness
          {taskCompleteness?.rating && (
            <span
              className={cn(
                'px-2 py-0.5 rounded text-xs font-medium',
                taskCompleteness.rating === 'success' &&
                  'bg-green-100 text-green-700',
                taskCompleteness.rating === 'partial' &&
                  'bg-yellow-100 text-yellow-700',
                taskCompleteness.rating === 'failure' &&
                  'bg-red-100 text-red-700',
                taskCompleteness.rating === 'unknown' &&
                  'bg-gray-100 text-gray-700'
              )}
            >
              {taskCompleteness.rating}
            </span>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Rating buttons */}
        <div className="grid grid-cols-4 gap-2">
          {ratingOptions.map((option) => (
            <Button
              key={option.value}
              variant={taskCompleteness?.rating === option.value ? 'default' : 'outline'}
              size="sm"
              onClick={() => updateTaskCompleteness({ rating: option.value })}
              className="relative"
            >
              {option.label}
              <span className="absolute -top-1 -right-1 text-[10px] text-muted-foreground bg-muted px-1 rounded">
                {option.key}
              </span>
            </Button>
          ))}
        </div>

        {/* Confidence level */}
        <FormSection label={`Confidence Level: ${taskCompleteness?.confidence ?? 3}`}>
          <input
            type="range"
            min={1}
            max={5}
            value={taskCompleteness?.confidence ?? 3}
            onChange={(e) =>
              updateTaskCompleteness({ confidence: parseInt(e.target.value) as 1 | 2 | 3 | 4 | 5 })
            }
            className="w-full h-2 bg-muted rounded-lg appearance-none cursor-pointer"
          />
          <div className="flex justify-between text-xs text-muted-foreground">
            <span>Low</span>
            <span>High</span>
          </div>
        </FormSection>

        {/* Conditional: Partial - Completion percentage and subtask */}
        {taskCompleteness?.rating === 'partial' && (
          <>
            <FormSection label={`Completion: ${taskCompleteness.completionPercentage ?? 50}%`}>
              <input
                type="range"
                min={0}
                max={100}
                step={5}
                value={taskCompleteness.completionPercentage ?? 50}
                onChange={(e) =>
                  updateTaskCompleteness({
                    completionPercentage: parseInt(e.target.value),
                  })
                }
                className="w-full h-2 bg-muted rounded-lg appearance-none cursor-pointer"
              />
            </FormSection>

            <FormSection label="Last Subtask Reached" htmlFor="subtask-reached">
              <Select
                value={taskCompleteness.subtaskReached ?? ''}
                onValueChange={(value) => updateTaskCompleteness({ subtaskReached: value })}
              >
                <SelectTrigger id="subtask-reached">
                  <SelectValue placeholder="Select subtask..." />
                </SelectTrigger>
                <SelectContent>
                  {subtaskOptions.map((subtask) => (
                    <SelectItem key={subtask} value={subtask}>
                      {subtask.charAt(0).toUpperCase() + subtask.slice(1)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </FormSection>
          </>
        )}

        {/* Conditional: Failure - Reason */}
        {taskCompleteness?.rating === 'failure' && (
          <FormSection label="Failure Reason" htmlFor="failure-reason">
            <Textarea
              id="failure-reason"
              value={taskCompleteness.failureReason ?? ''}
              onChange={(e) =>
                updateTaskCompleteness({ failureReason: e.target.value })
              }
              placeholder="Describe why the task failed..."
              className="min-h-[60px] resize-none"
            />
          </FormSection>
        )}
      </CardContent>
    </Card>
  );
}
