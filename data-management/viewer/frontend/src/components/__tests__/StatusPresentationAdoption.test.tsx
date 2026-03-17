import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { AISuggestionBadge } from '@/components/ai-suggestions/AISuggestionBadge'
import { SuggestionCard } from '@/components/ai-suggestions/SuggestionCard'
import { AnomalyList } from '@/components/annotation-panel/AnomalyList'
import { ActivityFeed } from '@/components/dashboard/ActivityFeed'
import { OfflineIndicator } from '@/components/offline/OfflineIndicator'
import type { Anomaly } from '@/types'

vi.mock('@/hooks/use-offline-annotations', () => ({
  useOfflineAnnotations: vi.fn(() => ({
    isOnline: true,
    pendingCount: 3,
    isSyncing: false,
    lastSyncResult: null,
    sync: vi.fn(async () => ({ syncedCount: 0, failedCount: 0, errors: [] })),
    saveLocal: vi.fn(),
    getLocal: vi.fn(),
    getPending: vi.fn(),
    deleteLocal: vi.fn(),
    startSync: vi.fn(),
    stopSync: vi.fn(),
  })),
}))

describe('status presentation adoption surfaces', () => {
  it('renders AI suggestion badge states with semantic status classes', () => {
    const { rerender } = render(<AISuggestionBadge hasError />)

    expect(screen.getByRole('button', { name: 'Error' })).toHaveClass(
      'bg-status-danger-subtle',
      'text-status-danger-foreground',
      'border-status-danger-border',
    )

    rerender(<AISuggestionBadge isAccepted />)

    expect(screen.getByRole('button', { name: 'Applied' })).toHaveClass(
      'bg-status-success-subtle',
      'text-status-success-foreground',
      'border-status-success-border',
    )
  })

  it('uses semantic status classes in the offline indicator summary', async () => {
    const user = userEvent.setup()

    render(<OfflineIndicator />)

    expect(screen.getByText('3')).toHaveClass(
      'bg-status-info-subtle',
      'text-status-info-foreground',
      'border-status-info-border',
    )

    await user.click(screen.getByRole('button'))

    expect(screen.getByText('Online')).toHaveClass(
      'bg-status-success-subtle',
      'text-status-success-foreground',
      'border-status-success-border',
    )
  })

  it('uses semantic icon and badge tones in the activity feed', () => {
    const { container } = render(
      <ActivityFeed
        activities={[
          {
            id: 'activity-1',
            type: 'review',
            episode_id: 'episode-12',
            annotator_name: 'Allen',
            timestamp: '2026-03-06T12:00:00.000Z',
            summary: 'Reviewed the grasp trajectory',
          },
        ]}
      />,
    )

    expect(screen.getByText('Reviewed')).toHaveClass(
      'bg-status-info-subtle',
      'text-status-info-foreground',
      'border-status-info-border',
    )

    const iconSurface = container.querySelector('.bg-status-info-subtle')
    expect(iconSurface).not.toBeNull()
    expect(iconSurface).toHaveClass('text-status-info-foreground', 'border-status-info-border')
  })

  it('uses semantic severity badges inside AI suggestion details', async () => {
    const user = userEvent.setup()

    render(
      <SuggestionCard
        suggestion={{
          task_completion_rating: 4,
          trajectory_quality_score: 3,
          suggested_flags: ['jittery'],
          detected_anomalies: [
            {
              id: 'detected-1',
              type: 'unexpected_stop',
              severity: 'high',
              frame_start: 10,
              frame_end: 16,
              description: 'The robot pauses before the final motion.',
              confidence: 0.88,
              auto_detected: true,
            },
          ],
          confidence: 0.91,
          reasoning: 'The end of the trajectory contains a sudden stop.',
        }}
      />,
    )

    await user.click(screen.getAllByRole('button')[0])

    expect(screen.getByText('high')).toHaveClass(
      'bg-status-danger-subtle',
      'text-status-danger-foreground',
      'border-status-danger-border',
    )
  })

  it('uses semantic status badges in the anomaly list', () => {
    const anomalies: Anomaly[] = [
      {
        id: 'anomaly-1',
        type: 'unexpected-stop',
        severity: 'high',
        frameRange: [12, 18],
        timestamp: [0.4, 0.6],
        description: 'Robot stopped before completing the grasp',
        autoDetected: true,
        verified: true,
      },
    ]

    render(
      <AnomalyList
        anomalies={anomalies}
        onRemove={() => undefined}
        onToggleVerified={() => undefined}
      />,
    )

    expect(screen.getByText('high')).toHaveClass(
      'bg-status-danger-subtle',
      'text-status-danger-foreground',
      'border-status-danger-border',
    )

    expect(screen.getByText('auto')).toHaveClass(
      'bg-status-info-subtle',
      'text-status-info-foreground',
      'border-status-info-border',
    )

    expect(screen.getByText('verified')).toHaveClass(
      'bg-status-success-subtle',
      'text-status-success-foreground',
      'border-status-success-border',
    )
  })
})
