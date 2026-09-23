import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { ReviewQualityPanel } from '@/components/annotation-workspace/ReviewQualityPanel'
import type { QualityReport } from '@/types'

const report: QualityReport = {
  schemaVersion: '1.0.0',
  runId: 'quality-run-42',
  checkSetVersion: '1.0.0',
  source: {
    schemaVersion: '1.0.0',
    datasetId: 'dataset-1',
    episodeIndex: 2,
    sourceFormat: 'lerobot',
    formatVersion: '3.0',
    sourceDigest: 'a'.repeat(64),
    files: [{ relativePath: 'meta/info.json', sizeBytes: 2, sha256: 'b'.repeat(64) }],
  },
  actorId: 'reviewer',
  createdAt: '2026-09-22T12:00:00Z',
  episodeChecks: [
    {
      checkId: 'timestamps.contiguous',
      required: true,
      outcome: 'fail',
      measurements: {},
      thresholds: {},
      reasonCodes: ['timestamp-gap'],
    },
    {
      checkId: 'streams.complete',
      required: true,
      outcome: 'fail',
      measurements: {},
      thresholds: {},
      reasonCodes: ['missing-camera'],
    },
    {
      checkId: 'optional.preview',
      required: false,
      outcome: 'fail',
      measurements: {},
      thresholds: {},
      reasonCodes: [],
    },
  ],
  packageChecks: [],
}

describe('ReviewQualityPanel', () => {
  it('shows the referenced run and every blocking failure with text and icon semantics', () => {
    render(
      <ReviewQualityPanel
        qualityReport={report}
        qualityError={null}
        isRunningQuality={false}
        isSubmittingDecision={false}
        onRunQuality={vi.fn()}
        onDecision={vi.fn()}
      />,
    )

    expect(screen.getByText('quality-run-42')).toBeInTheDocument()
    expect(screen.getByText('timestamps.contiguous')).toBeInTheDocument()
    expect(screen.getByText('streams.complete')).toBeInTheDocument()
    expect(screen.queryByText('optional.preview')).not.toBeInTheDocument()
    expect(screen.getAllByLabelText('Blocking required failure')).toHaveLength(2)
    expect(screen.getAllByText('Blocking')).toHaveLength(2)
  })

  it('requires a reason code before explicit Accept or Reject actions', async () => {
    const user = userEvent.setup()
    const onDecision = vi.fn()
    render(
      <ReviewQualityPanel
        qualityReport={report}
        qualityError={null}
        isRunningQuality={false}
        isSubmittingDecision={false}
        onRunQuality={vi.fn()}
        onDecision={onDecision}
      />,
    )

    expect(screen.getByRole('button', { name: 'Accept episode' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Reject episode' })).toBeDisabled()

    await user.click(screen.getByRole('checkbox', { name: 'Needs recapture' }))
    await user.click(screen.getByRole('button', { name: 'Reject episode' }))

    expect(onDecision).toHaveBeenCalledWith('reject', ['needs-recapture'])
  })

  it('explains locked decision controls and exposes quality-run failures', () => {
    render(
      <ReviewQualityPanel
        qualityReport={null}
        qualityError="Quality request was rejected"
        isRunningQuality={false}
        isSubmittingDecision={false}
        onRunQuality={vi.fn()}
        onDecision={vi.fn()}
      />,
    )

    expect(screen.getByRole('alert')).toHaveTextContent('Quality request was rejected')
    expect(
      screen.getByText('Run quality to enable reason codes and review decisions.'),
    ).toBeVisible()
    expect(screen.getByRole('checkbox', { name: 'Needs recapture' })).toBeDisabled()
  })
})
