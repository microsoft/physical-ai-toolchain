import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { DataviewerEpisodeViewer } from '../DataviewerEpisodeViewer'

const mockSetCurrentEpisode = vi.fn()

vi.mock('@/hooks/use-datasets', () => ({
  useEpisode: vi.fn(),
}))

vi.mock('@/stores', () => ({
  useEpisodeStore: (
    selector: (state: { setCurrentEpisode: typeof mockSetCurrentEpisode }) => unknown,
  ) => selector({ setCurrentEpisode: mockSetCurrentEpisode }),
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
    mockSetCurrentEpisode.mockReset()
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

  it('shows the loading message while the episode is fetching', () => {
    vi.mocked(useEpisode).mockReturnValue({
      data: undefined,
      isLoading: true,
      error: null,
    } as unknown as ReturnType<typeof useEpisode>)

    render(<DataviewerEpisodeViewer {...baseProps} episodeIndex={3} />)

    expect(screen.getByText('Loading episode 3...')).toBeInTheDocument()
    expect(screen.queryByTestId('annotation-workspace')).not.toBeInTheDocument()
  })

  it('surfaces the error message when the fetch fails', () => {
    vi.mocked(useEpisode).mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new Error('boom'),
    } as unknown as ReturnType<typeof useEpisode>)

    render(<DataviewerEpisodeViewer {...baseProps} />)

    expect(screen.getByText('Error loading episode: boom')).toBeInTheDocument()
    expect(screen.queryByTestId('annotation-workspace')).not.toBeInTheDocument()
  })

  it('renders the no-data placeholder when the episode is missing', () => {
    vi.mocked(useEpisode).mockReturnValue({
      data: undefined,
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useEpisode>)

    render(<DataviewerEpisodeViewer {...baseProps} />)

    expect(screen.getByText('No episode data')).toBeInTheDocument()
    expect(screen.queryByTestId('annotation-workspace')).not.toBeInTheDocument()
  })

  it('publishes the loaded episode to the episode store', () => {
    const episode = { meta: { index: 2 }, episode_index: 2, length: 5 }
    vi.mocked(useEpisode).mockReturnValue({
      data: episode,
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useEpisode>)

    render(<DataviewerEpisodeViewer {...baseProps} episodeIndex={2} />)

    expect(mockSetCurrentEpisode).toHaveBeenCalledWith(episode)
  })
})
