import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useEditStore, useEpisodeStore } from '@/stores'

import { FrameRemovalToolbar } from '../FrameRemovalToolbar'

vi.mock('@/stores', () => ({
  useEpisodeStore: vi.fn(),
  useEditStore: vi.fn(),
}))

const mockedEpisodeStore = vi.mocked(useEpisodeStore)
const mockedEditStore = vi.mocked(useEditStore)

interface EpisodeState {
  currentFrame: number
  currentEpisode: { meta: { length: number } } | undefined
}

interface EditState {
  removedFrames: Set<number>
  toggleFrameRemoval: ReturnType<typeof vi.fn>
  addFrameRange: ReturnType<typeof vi.fn>
  addFramesByFrequency: ReturnType<typeof vi.fn>
  clearRemovedFrames: ReturnType<typeof vi.fn>
}

function setup(
  opts: {
    episode?: Partial<EpisodeState>
    edit?: Partial<EditState>
  } = {},
) {
  const episodeState: EpisodeState = {
    currentFrame: 5,
    currentEpisode: undefined,
    ...opts.episode,
  }
  const editState: EditState = {
    removedFrames: new Set(),
    toggleFrameRemoval: vi.fn(),
    addFrameRange: vi.fn(),
    addFramesByFrequency: vi.fn(),
    clearRemovedFrames: vi.fn(),
    ...opts.edit,
  }
  mockedEpisodeStore.mockImplementation(((selector: (state: EpisodeState) => unknown) =>
    selector(episodeState)) as never)
  mockedEditStore.mockImplementation(((selector: (state: EditState) => unknown) =>
    selector(editState)) as never)
  return { episodeState, editState }
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('FrameRemovalToolbar', () => {
  it('renders the Frame Removal heading', () => {
    setup()
    render(<FrameRemovalToolbar />)
    expect(screen.getByText('Frame Removal')).toBeInTheDocument()
  })

  it('does not render badge when no frames are removed', () => {
    setup()
    render(<FrameRemovalToolbar />)
    expect(screen.queryByText(/frames? removed/)).not.toBeInTheDocument()
  })

  it('renders singular badge when one frame is removed', () => {
    setup({ edit: { removedFrames: new Set([3]) } })
    render(<FrameRemovalToolbar />)
    expect(screen.getByText('1 frame removed')).toBeInTheDocument()
  })

  it('renders plural badge when multiple frames are removed', () => {
    setup({ edit: { removedFrames: new Set([1, 2, 3]) } })
    render(<FrameRemovalToolbar />)
    expect(screen.getByText('3 frames removed')).toBeInTheDocument()
  })

  it('renders Remove button label for non-removed current frame', () => {
    setup({ episode: { currentFrame: 5 } })
    render(<FrameRemovalToolbar />)
    expect(screen.getByRole('button', { name: /Remove Frame 5/ })).toBeInTheDocument()
  })

  it('renders Restore button label when current frame is in removed set', () => {
    setup({
      episode: { currentFrame: 5 },
      edit: { removedFrames: new Set([5]) },
    })
    render(<FrameRemovalToolbar />)
    expect(screen.getByRole('button', { name: /Restore Frame 5/ })).toBeInTheDocument()
  })

  it('calls toggleFrameRemoval with current frame when toggle button clicked', async () => {
    const user = userEvent.setup()
    const { editState } = setup({ episode: { currentFrame: 7 } })
    render(<FrameRemovalToolbar />)
    await user.click(screen.getByRole('button', { name: /Remove Frame 7/ }))
    expect(editState.toggleFrameRemoval).toHaveBeenCalledWith(7)
  })

  it('does not show Clear All button when no frames are removed', () => {
    setup()
    render(<FrameRemovalToolbar />)
    expect(screen.queryByRole('button', { name: /Clear All/ })).not.toBeInTheDocument()
  })

  it('shows Clear All button when frames are removed', () => {
    setup({ edit: { removedFrames: new Set([1]) } })
    render(<FrameRemovalToolbar />)
    expect(screen.getByRole('button', { name: /Clear All/ })).toBeInTheDocument()
  })

  it('calls clearRemovedFrames when Clear All clicked', async () => {
    const user = userEvent.setup()
    const { editState } = setup({ edit: { removedFrames: new Set([1, 2]) } })
    render(<FrameRemovalToolbar />)
    await user.click(screen.getByRole('button', { name: /Clear All/ }))
    expect(editState.clearRemovedFrames).toHaveBeenCalled()
  })

  it('disables range Add button when inputs are empty', () => {
    setup()
    render(<FrameRemovalToolbar />)
    const addButton = screen.getByRole('button', { name: /^Add$/ })
    expect(addButton).toBeDisabled()
  })

  it('calls addFrameRange with parsed numeric values', () => {
    const { editState } = setup()
    render(<FrameRemovalToolbar />)

    fireEvent.change(screen.getByPlaceholderText('Start'), { target: { value: '10' } })
    fireEvent.change(screen.getByPlaceholderText('End'), { target: { value: '20' } })
    fireEvent.click(screen.getByRole('button', { name: /^Add$/ }))

    expect(editState.addFrameRange).toHaveBeenCalledWith(10, 20)
  })

  it('clears range inputs after a successful add', () => {
    setup()
    render(<FrameRemovalToolbar />)

    const startInput = screen.getByPlaceholderText('Start') as HTMLInputElement
    const endInput = screen.getByPlaceholderText('End') as HTMLInputElement
    fireEvent.change(startInput, { target: { value: '10' } })
    fireEvent.change(endInput, { target: { value: '20' } })
    fireEvent.click(screen.getByRole('button', { name: /^Add$/ }))

    expect(startInput.value).toBe('')
    expect(endInput.value).toBe('')
  })

  it('does not call addFrameRange when start > end', () => {
    const { editState } = setup()
    render(<FrameRemovalToolbar />)

    fireEvent.change(screen.getByPlaceholderText('Start'), { target: { value: '20' } })
    fireEvent.change(screen.getByPlaceholderText('End'), { target: { value: '10' } })
    fireEvent.click(screen.getByRole('button', { name: /^Add$/ }))

    expect(editState.addFrameRange).not.toHaveBeenCalled()
  })

  it('triggers addFrameRange on Enter key in range input', () => {
    const { editState } = setup()
    render(<FrameRemovalToolbar />)

    const startInput = screen.getByPlaceholderText('Start')
    const endInput = screen.getByPlaceholderText('End')
    fireEvent.change(startInput, { target: { value: '5' } })
    fireEvent.change(endInput, { target: { value: '15' } })
    fireEvent.keyDown(endInput, { key: 'Enter' })

    expect(editState.addFrameRange).toHaveBeenCalledWith(5, 15)
  })

  it('uses totalFrames default of 100 when no episode loaded', () => {
    setup()
    render(<FrameRemovalToolbar />)
    expect(screen.getByPlaceholderText('99')).toBeInTheDocument()
  })

  it('uses episode meta length for totalFrames placeholder', () => {
    setup({ episode: { currentEpisode: { meta: { length: 250 } } } })
    render(<FrameRemovalToolbar />)
    expect(screen.getByPlaceholderText('249')).toBeInTheDocument()
  })

  it('renders default frequency value of 2', () => {
    setup()
    render(<FrameRemovalToolbar />)
    expect(screen.getByDisplayValue('2')).toBeInTheDocument()
  })

  it('shows frequency preview when valid range is computed', () => {
    setup({ episode: { currentEpisode: { meta: { length: 100 } } } })
    render(<FrameRemovalToolbar />)
    // start=0, end=99, freq=2 → floor(99/2)+1 = 50
    expect(screen.getByText(/Will remove 50 frames/)).toBeInTheDocument()
  })

  it('disables Apply button when frequency is below 1', () => {
    setup()
    render(<FrameRemovalToolbar />)

    const freqInput = screen.getByDisplayValue('2')
    fireEvent.change(freqInput, { target: { value: '0' } })

    expect(screen.getByRole('button', { name: /Apply/ })).toBeDisabled()
  })

  it('calls addFramesByFrequency with defaults when range inputs are empty', () => {
    const { editState } = setup({
      episode: { currentEpisode: { meta: { length: 50 } } },
    })
    render(<FrameRemovalToolbar />)
    fireEvent.click(screen.getByRole('button', { name: /Apply/ }))
    expect(editState.addFramesByFrequency).toHaveBeenCalledWith(0, 49, 2)
  })

  it('calls addFramesByFrequency with provided start/end values', () => {
    const { editState } = setup()
    render(<FrameRemovalToolbar />)

    fireEvent.change(screen.getByPlaceholderText('0'), { target: { value: '10' } })
    fireEvent.change(screen.getByPlaceholderText('99'), { target: { value: '40' } })
    fireEvent.click(screen.getByRole('button', { name: /Apply/ }))

    expect(editState.addFramesByFrequency).toHaveBeenCalledWith(10, 40, 2)
  })

  it('triggers frequency apply on Enter key', () => {
    const { editState } = setup()
    render(<FrameRemovalToolbar />)
    const freqInput = screen.getByDisplayValue('2')
    fireEvent.keyDown(freqInput, { key: 'Enter' })
    expect(editState.addFramesByFrequency).toHaveBeenCalled()
  })

  it('does not show frequency preview when frequency is invalid', () => {
    setup()
    render(<FrameRemovalToolbar />)
    const freqInput = screen.getByDisplayValue('2')
    fireEvent.change(freqInput, { target: { value: '0' } })
    expect(screen.queryByText(/Will remove/)).not.toBeInTheDocument()
  })
})
