import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
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
    <div data-testid="annotation-workspace" data-diagnostics={String(props.diagnosticsVisible)}>
      <button type="button" onClick={() => (props.onSaveAndNextEpisode as () => void)()}>
        Save and continue
      </button>
    </div>
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

  it('keeps an empty atomic save status region mounted before publishing an update', async () => {
    const user = userEvent.setup()
    vi.mocked(useEpisode).mockReturnValue({
      data: { meta: { index: 0 }, episode_index: 0, length: 10 },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useEpisode>)

    render(<DataviewerEpisodeViewer {...baseProps} />)

    const status = screen.getByTestId('episode-navigation-status')
    expect(status).toHaveAttribute('role', 'status')
    expect(status).toHaveAttribute('aria-atomic', 'true')
    expect(status).toBeEmptyDOMElement()

    await user.click(screen.getByRole('button', { name: 'Save and continue' }))

    expect(status).toHaveTextContent('Episode changes saved.')
  })

  it('shows the loading message while the episode is fetching', () => {
    vi.mocked(useEpisode).mockReturnValue({
      data: undefined,
      isLoading: true,
      error: null,
    } as unknown as ReturnType<typeof useEpisode>)

    render(<DataviewerEpisodeViewer {...baseProps} episodeIndex={3} />)

    expect(screen.getByRole('status')).toHaveTextContent('Loading episode 3...')
    expect(screen.queryByTestId('annotation-workspace')).not.toBeInTheDocument()
  })

  it('surfaces the error message when the fetch fails', () => {
    vi.mocked(useEpisode).mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new Error('boom'),
    } as unknown as ReturnType<typeof useEpisode>)

    render(<DataviewerEpisodeViewer {...baseProps} />)

    expect(screen.getByRole('alert')).toHaveTextContent('Error loading episode: boom')
    expect(screen.queryByTestId('annotation-workspace')).not.toBeInTheDocument()
  })

  it('renders the no-data placeholder when the episode is missing', () => {
    vi.mocked(useEpisode).mockReturnValue({
      data: undefined,
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useEpisode>)

    render(<DataviewerEpisodeViewer {...baseProps} />)

    expect(screen.getByRole('status')).toHaveTextContent('No episode data')
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
