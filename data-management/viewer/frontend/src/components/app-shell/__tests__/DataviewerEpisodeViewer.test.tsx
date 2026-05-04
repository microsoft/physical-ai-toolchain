import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { DataviewerEpisodeViewer } from '../DataviewerEpisodeViewer'

vi.mock('@/hooks/use-datasets', () => ({
  useEpisode: vi.fn(),
}))

vi.mock('@/components/annotation-workspace/AnnotationWorkspace', () => ({
  AnnotationWorkspace: (props: Record<string, unknown>) => (
    <div data-testid="annotation-workspace" data-diagnostics={String(props.diagnosticsVisible)} />
  ),
}))

const { useEpisode } = await import('@/hooks/use-datasets')

const baseProps = {
  datasetId: 'ds-1',
  episodeIndex: 0,
  diagnosticsVisible: false,
  canGoPreviousEpisode: false,
  onPreviousEpisode: vi.fn(),
  canGoNextEpisode: true,
  onNextEpisode: vi.fn(),
  onSaveAndNextEpisode: vi.fn(),
}

describe('DataviewerEpisodeViewer', () => {
  afterEach(() => {
    vi.mocked(useEpisode).mockReset()
  })

  it('renders the AnnotationWorkspace once the episode loads', () => {
    vi.mocked(useEpisode).mockReturnValue({
      data: { meta: { index: 0 }, episode_index: 0, length: 10 },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useEpisode>)

    render(<DataviewerEpisodeViewer {...baseProps} />)

    expect(screen.getByTestId('annotation-workspace')).toBeInTheDocument()
  })
})
