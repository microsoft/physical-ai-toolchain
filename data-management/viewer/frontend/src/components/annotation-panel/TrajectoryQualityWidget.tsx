/**
 * Trajectory quality annotation widget.
 *
 * Provides star ratings for overall and individual metrics,
 * plus flag toggles for trajectory issues.
 */

import { useCallback, useEffect } from 'react'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { useAnnotationStore } from '@/stores'
import type { TrajectoryFlag } from '@/types'

import { FlagToggle } from './FlagToggle'
import { StarRating } from './StarRating'

/**
 * Widget for annotating trajectory quality with star ratings and flags.
 *
 * Keyboard shortcuts:
 * - 1-5: Set overall quality rating
 * - J: Toggle jittery flag
 *
 * @example
 * ```tsx
 * <TrajectoryQualityWidget />
 * ```
 */
export function TrajectoryQualityWidget() {
  const currentAnnotation = useAnnotationStore((state) => state.currentAnnotation)
  const updateTrajectoryQuality = useAnnotationStore((state) => state.updateTrajectoryQuality)

  const trajectoryQuality = currentAnnotation?.trajectoryQuality

  const toggleFlag = useCallback(
    (flag: TrajectoryFlag) => {
      const currentFlags = trajectoryQuality?.flags ?? []
      const hasFlag = currentFlags.includes(flag)
      updateTrajectoryQuality({
        flags: hasFlag ? currentFlags.filter((f) => f !== flag) : [...currentFlags, flag],
      })
    },
    [trajectoryQuality?.flags, updateTrajectoryQuality],
  )

  // Keyboard shortcuts for overall rating
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) {
        return
      }

      // Number keys 1-5 for overall rating
      if (/^[1-5]$/.test(e.key) && !e.ctrlKey && !e.metaKey && !e.altKey) {
        updateTrajectoryQuality({ overallScore: parseInt(e.key) as 1 | 2 | 3 | 4 | 5 })
      }

      // J for jittery flag
      if (e.key.toLowerCase() === 'j' && !e.ctrlKey && !e.metaKey) {
        toggleFlag('jittery')
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [trajectoryQuality, updateTrajectoryQuality, toggleFlag])

  const metricLabels: {
    key: keyof NonNullable<typeof trajectoryQuality>['metrics']
    label: string
  }[] = [
    { key: 'smoothness', label: 'Smoothness' },
    { key: 'efficiency', label: 'Efficiency' },
    { key: 'safety', label: 'Safety' },
    { key: 'precision', label: 'Precision' },
  ]

  const flagLabels: { flag: TrajectoryFlag; label: string; shortcut?: string }[] = [
    { flag: 'jittery', label: 'Jittery', shortcut: 'J' },
    { flag: 'hesitation', label: 'Hesitant' },
    { flag: 'near-collision', label: 'Collision Risk' },
    { flag: 'over-extension', label: 'Over Extension' },
    { flag: 'inefficient-path', label: 'Inefficient' },
    { flag: 'correction-heavy', label: 'Correction Heavy' },
  ]

  if (!currentAnnotation) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Trajectory Quality</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-muted-foreground text-sm">No episode selected</p>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center justify-between text-sm">
          Trajectory Quality
          {trajectoryQuality?.overallScore && (
            <span className="text-xs text-yellow-500">
              {'★'.repeat(trajectoryQuality.overallScore)}
              {'☆'.repeat(5 - trajectoryQuality.overallScore)}
            </span>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Overall score */}
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium">Overall (1-5)</span>
            <span className="text-muted-foreground text-xs">Press 1-5</span>
          </div>
          <StarRating
            value={trajectoryQuality?.overallScore ?? 0}
            onChange={(value) =>
              updateTrajectoryQuality({ overallScore: value as 1 | 2 | 3 | 4 | 5 })
            }
            size="lg"
          />
        </div>

        {/* Individual metrics */}
        <div className="space-y-3">
          <span className="text-sm font-medium">Metrics</span>
          <div className="grid grid-cols-2 gap-3">
            {metricLabels.map(({ key, label }) => (
              <StarRating
                key={key}
                label={label}
                value={trajectoryQuality?.metrics?.[key] ?? 0}
                onChange={(value) =>
                  updateTrajectoryQuality({
                    metrics: {
                      ...trajectoryQuality?.metrics,
                      smoothness: trajectoryQuality?.metrics?.smoothness ?? 3,
                      efficiency: trajectoryQuality?.metrics?.efficiency ?? 3,
                      safety: trajectoryQuality?.metrics?.safety ?? 3,
                      precision: trajectoryQuality?.metrics?.precision ?? 3,
                      [key]: value,
                    },
                  })
                }
                size="sm"
              />
            ))}
          </div>
        </div>

        {/* Flags */}
        <div className="space-y-2">
          <span className="text-sm font-medium">Flags</span>
          <div className="flex flex-wrap gap-2">
            {flagLabels.map(({ flag, label, shortcut }) => (
              <FlagToggle
                key={flag}
                label={label}
                active={trajectoryQuality?.flags?.includes(flag) ?? false}
                onToggle={() => toggleFlag(flag)}
                shortcut={shortcut}
              />
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
