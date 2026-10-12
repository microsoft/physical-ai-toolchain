import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { ExportProgress } from '@/components/export/ExportProgress'
import type { ExportResult } from '@/types'

function exportResult(overrides: Partial<ExportResult> = {}): ExportResult {
  return {
    success: true,
    outputFiles: [],
    error: null,
    stats: { totalEpisodes: 2, totalFrames: 40, removedFrames: 0, durationMs: 120 },
    ...overrides,
  }
}

describe('ExportProgress', () => {
  it('names the output path when an export writes a single dataset', () => {
    render(
      <ExportProgress
        progress={null}
        error={null}
        result={exportResult({ outputFiles: ['/data/exports/pick-place'] })}
      />,
    )

    expect(screen.getByRole('status')).toHaveTextContent(
      'Successfully exported 2 episode(s) to /data/exports/pick-place.',
    )
  })

  it('counts the output files when an export writes several', () => {
    render(
      <ExportProgress
        progress={null}
        error={null}
        result={exportResult({ outputFiles: ['episode_000000.hdf5', 'episode_000001.hdf5'] })}
      />,
    )

    expect(screen.getByRole('status')).toHaveTextContent(
      'Successfully exported 2 episode(s) to 2 files.',
    )
  })

  it('shows the message from an export that failed', () => {
    render(
      <ExportProgress
        progress={null}
        error={null}
        result={exportResult({ success: false, error: 'Output directory is in use' })}
      />,
    )

    expect(screen.getByText('Export Failed')).toBeInTheDocument()
    expect(screen.getByText('Output directory is in use')).toBeInTheDocument()
  })

  it('falls back to a generic message when a failed export has none', () => {
    render(
      <ExportProgress progress={null} error={null} result={exportResult({ success: false })} />,
    )

    expect(screen.getByText('Unknown error')).toBeInTheDocument()
  })

  it('shows a stream error before any result arrives', () => {
    render(<ExportProgress progress={null} error="Connection lost" result={null} />)

    expect(screen.getByText('Export Failed')).toBeInTheDocument()
    expect(screen.getByText('Connection lost')).toBeInTheDocument()
  })

  it('reports episode, frame and percentage progress while an export runs', () => {
    render(
      <ExportProgress
        progress={{
          currentEpisode: 1,
          totalEpisodes: 3,
          currentFrame: 12,
          totalFrames: 40,
          percentage: 33.4,
          status: 'Encoding videos',
        }}
        error={null}
        result={null}
      />,
    )

    expect(screen.getByText('Encoding videos')).toBeInTheDocument()
    expect(screen.getByText('Episode 1 of 3')).toBeInTheDocument()
    expect(screen.getByText('33%')).toBeInTheDocument()
    expect(screen.getByText('Frame 12 of 40')).toBeInTheDocument()
  })

  it('says the export is preparing before progress arrives', () => {
    render(<ExportProgress progress={null} error={null} result={null} />)

    expect(screen.getByText('Preparing export...')).toBeInTheDocument()
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
  })
})
