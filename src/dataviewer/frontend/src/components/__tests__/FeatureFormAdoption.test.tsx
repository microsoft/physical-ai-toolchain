import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { SuggestionCard } from '@/components/ai-suggestions/SuggestionCard'
import { AddAnomalyDialog } from '@/components/annotation-panel/AddAnomalyDialog'
import { AddIssueDialog } from '@/components/annotation-panel/AddIssueDialog'

describe('feature form adoption surfaces', () => {
  it('exposes named selection checkboxes for partial AI suggestion acceptance', async () => {
    const user = userEvent.setup()
    const onPartialAccept = vi.fn()

    render(
      <SuggestionCard
        suggestion={{
          task_completion_rating: 4,
          trajectory_quality_score: 3,
          suggested_flags: ['jittery'],
          detected_anomalies: [
            {
              id: 'anomaly-1',
              type: 'unexpected_stop',
              severity: 'medium',
              frame_start: 10,
              frame_end: 20,
              description: 'Stopped early',
              confidence: 0.82,
              auto_detected: true,
            },
          ],
          confidence: 0.91,
          reasoning: 'The trajectory pauses before completing the final step.',
        }}
        onPartialAccept={onPartialAccept}
      />,
    )

    const taskCompletionCheckbox = screen.getByRole('checkbox', {
      name: 'Task Completion',
    })

    await user.click(taskCompletionCheckbox)
    await user.click(screen.getByRole('button', { name: 'Apply Selected (3)' }))

    expect(onPartialAccept).toHaveBeenCalledWith([
      'trajectory_quality',
      'flags',
      'anomalies',
    ])
  })

  it('provides explicit frame labels in the data quality issue dialog', () => {
    render(
      <AddIssueDialog
        open
        onClose={() => undefined}
        onAdd={() => undefined}
        currentFrame={42}
      />,
    )

    expect(screen.getByRole('spinbutton', { name: 'Start frame' })).toHaveValue(42)
    expect(screen.getByRole('spinbutton', { name: 'End frame' })).toHaveValue(52)
  })

  it('provides explicit frame labels in the anomaly dialog', () => {
    render(
      <AddAnomalyDialog
        open
        onClose={() => undefined}
        onAdd={() => undefined}
        currentFrame={24}
      />,
    )

    expect(screen.getByRole('spinbutton', { name: 'Start frame' })).toHaveValue(24)
    expect(screen.getByRole('spinbutton', { name: 'End frame' })).toHaveValue(34)
  })
})