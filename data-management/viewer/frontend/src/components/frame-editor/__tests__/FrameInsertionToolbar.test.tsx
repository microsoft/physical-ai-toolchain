import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useEpisodeStore, useFrameInsertionState } from '@/stores'

import { FrameInsertionToolbar } from '../FrameInsertionToolbar'

vi.mock('@/stores', () => ({
  useEpisodeStore: vi.fn(),
  useFrameInsertionState: vi.fn(),
}))

const mockedEpisodeStore = vi.mocked(useEpisodeStore)
const mockedInsertionState = vi.mocked(useFrameInsertionState)

interface EpisodeState {
  currentFrame: number
  currentEpisode: { meta: { length: number } } | undefined
}

interface InsertionState {
  insertedFrames: Set<number>
  insertFrame: ReturnType<typeof vi.fn>
  removeInsertedFrame: ReturnType<typeof vi.fn>
  clearInsertedFrames: ReturnType<typeof vi.fn>
}

function setup(
  opts: { episode?: Partial<EpisodeState>; insertion?: Partial<InsertionState> } = {},
) {
  const episodeState: EpisodeState = {
    currentFrame: 5,
    currentEpisode: { meta: { length: 100 } },
    ...opts.episode,
  }
  const insertionState: InsertionState = {
    insertedFrames: new Set(),
    insertFrame: vi.fn(),
    removeInsertedFrame: vi.fn(),
    clearInsertedFrames: vi.fn(),
    ...opts.insertion,
  }
  mockedEpisodeStore.mockImplementation(((selector: (state: EpisodeState) => unknown) =>
    selector(episodeState)) as never)
  mockedInsertionState.mockReturnValue(insertionState as never)
  return { episodeState, insertionState }
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('FrameInsertionToolbar', () => {
  describe('rendering', () => {
    it('renders heading and section labels', () => {
      setup()
      render(<FrameInsertionToolbar />)
      expect(screen.getByText('Frame Insertion')).toBeInTheDocument()
      expect(screen.getByText('Blend Factor:')).toBeInTheDocument()
      expect(screen.getByText('Insert After Range')).toBeInTheDocument()
      expect(screen.getByText('Insert by Frequency')).toBeInTheDocument()
    })

    it('does not show inserted-count badge when no frames inserted', () => {
      setup()
      render(<FrameInsertionToolbar />)
      expect(screen.queryByText(/inserted$/)).not.toBeInTheDocument()
    })

    it('shows singular badge text when exactly one frame inserted', () => {
      setup({ insertion: { insertedFrames: new Set([3]) } })
      render(<FrameInsertionToolbar />)
      expect(screen.getByText('1 frame inserted')).toBeInTheDocument()
    })

    it('shows plural badge text when multiple frames inserted', () => {
      setup({ insertion: { insertedFrames: new Set([3, 4, 7]) } })
      render(<FrameInsertionToolbar />)
      expect(screen.getByText('3 frames inserted')).toBeInTheDocument()
    })

    it('shows "Insert After" button label when current frame is not inserted', () => {
      setup({ episode: { currentFrame: 5 } })
      render(<FrameInsertionToolbar />)
      expect(screen.getByRole('button', { name: /Insert After Frame 5/ })).toBeInTheDocument()
    })

    it('shows "Remove" button label when current frame is inserted', () => {
      setup({ episode: { currentFrame: 5 }, insertion: { insertedFrames: new Set([5]) } })
      render(<FrameInsertionToolbar />)
      expect(screen.getByRole('button', { name: /Remove Frame 5/ })).toBeInTheDocument()
    })

    it('hides Clear All button when no frames inserted', () => {
      setup()
      render(<FrameInsertionToolbar />)
      expect(screen.queryByRole('button', { name: /Clear All/ })).not.toBeInTheDocument()
    })

    it('shows Clear All button when frames inserted', () => {
      setup({ insertion: { insertedFrames: new Set([1, 2]) } })
      render(<FrameInsertionToolbar />)
      expect(screen.getByRole('button', { name: /Clear All/ })).toBeInTheDocument()
    })
  })

  describe('blend factor input', () => {
    it('updates blend factor and uses it when inserting frames', () => {
      const { insertionState } = setup({ episode: { currentFrame: 5 } })
      render(<FrameInsertionToolbar />)
      const blendInput = screen.getByDisplayValue('0.5')
      fireEvent.change(blendInput, { target: { value: '0.8' } })
      fireEvent.click(screen.getByRole('button', { name: /Insert After Frame 5/ }))
      expect(insertionState.insertFrame).toHaveBeenCalledWith(5, 0.8)
    })

    it('falls back to default 0.5 when blend factor is invalid', () => {
      const { insertionState } = setup({ episode: { currentFrame: 5 } })
      render(<FrameInsertionToolbar />)
      const blendInput = screen.getByDisplayValue('0.5')
      fireEvent.change(blendInput, { target: { value: 'abc' } })
      fireEvent.click(screen.getByRole('button', { name: /Insert After Frame 5/ }))
      expect(insertionState.insertFrame).toHaveBeenCalledWith(5, 0.5)
    })
  })

  describe('toggle current frame', () => {
    it('inserts current frame when not yet inserted', () => {
      const { insertionState } = setup({ episode: { currentFrame: 5 } })
      render(<FrameInsertionToolbar />)
      fireEvent.click(screen.getByRole('button', { name: /Insert After Frame 5/ }))
      expect(insertionState.insertFrame).toHaveBeenCalledWith(5, 0.5)
      expect(insertionState.removeInsertedFrame).not.toHaveBeenCalled()
    })

    it('removes current frame when already inserted', () => {
      const { insertionState } = setup({
        episode: { currentFrame: 5 },
        insertion: { insertedFrames: new Set([5]) },
      })
      render(<FrameInsertionToolbar />)
      fireEvent.click(screen.getByRole('button', { name: /Remove Frame 5/ }))
      expect(insertionState.removeInsertedFrame).toHaveBeenCalledWith(5)
      expect(insertionState.insertFrame).not.toHaveBeenCalled()
    })

    it('disables toggle button at last frame when not inserted', () => {
      setup({ episode: { currentFrame: 99, currentEpisode: { meta: { length: 100 } } } })
      render(<FrameInsertionToolbar />)
      expect(screen.getByRole('button', { name: /Insert After Frame 99/ })).toBeDisabled()
    })

    it('keeps toggle button enabled at last frame when already inserted', () => {
      setup({
        episode: { currentFrame: 99, currentEpisode: { meta: { length: 100 } } },
        insertion: { insertedFrames: new Set([99]) },
      })
      render(<FrameInsertionToolbar />)
      expect(screen.getByRole('button', { name: /Remove Frame 99/ })).not.toBeDisabled()
    })
  })

  describe('clear all', () => {
    it('calls clearInsertedFrames when Clear All clicked', () => {
      const { insertionState } = setup({ insertion: { insertedFrames: new Set([1, 2]) } })
      render(<FrameInsertionToolbar />)
      fireEvent.click(screen.getByRole('button', { name: /Clear All/ }))
      expect(insertionState.clearInsertedFrames).toHaveBeenCalledTimes(1)
    })
  })

  describe('range insertion', () => {
    it('disables Add button when range inputs are empty', () => {
      setup()
      render(<FrameInsertionToolbar />)
      expect(screen.getByRole('button', { name: /^Add$/ })).toBeDisabled()
    })

    it('enables Add button when both range inputs are set', () => {
      setup()
      render(<FrameInsertionToolbar />)
      fireEvent.change(screen.getByPlaceholderText('Start'), { target: { value: '2' } })
      fireEvent.change(screen.getByPlaceholderText('End'), { target: { value: '4' } })
      expect(screen.getByRole('button', { name: /^Add$/ })).not.toBeDisabled()
    })

    it('inserts each frame in valid range and clears inputs', () => {
      const { insertionState } = setup()
      render(<FrameInsertionToolbar />)
      const startInput = screen.getByPlaceholderText('Start') as HTMLInputElement
      const endInput = screen.getByPlaceholderText('End') as HTMLInputElement
      fireEvent.change(startInput, { target: { value: '2' } })
      fireEvent.change(endInput, { target: { value: '4' } })
      fireEvent.click(screen.getByRole('button', { name: /^Add$/ }))
      expect(insertionState.insertFrame).toHaveBeenCalledTimes(3)
      expect(insertionState.insertFrame).toHaveBeenNthCalledWith(1, 2, 0.5)
      expect(insertionState.insertFrame).toHaveBeenNthCalledWith(2, 3, 0.5)
      expect(insertionState.insertFrame).toHaveBeenNthCalledWith(3, 4, 0.5)
      expect(startInput.value).toBe('')
      expect(endInput.value).toBe('')
    })

    it('does not insert when end exceeds totalFrames - 1', () => {
      const { insertionState } = setup({ episode: { currentEpisode: { meta: { length: 10 } } } })
      render(<FrameInsertionToolbar />)
      fireEvent.change(screen.getByPlaceholderText('Start'), { target: { value: '2' } })
      fireEvent.change(screen.getByPlaceholderText('End'), { target: { value: '9' } })
      fireEvent.click(screen.getByRole('button', { name: /^Add$/ }))
      expect(insertionState.insertFrame).not.toHaveBeenCalled()
    })

    it('does not insert when start is greater than end', () => {
      const { insertionState } = setup()
      render(<FrameInsertionToolbar />)
      fireEvent.change(screen.getByPlaceholderText('Start'), { target: { value: '5' } })
      fireEvent.change(screen.getByPlaceholderText('End'), { target: { value: '3' } })
      fireEvent.click(screen.getByRole('button', { name: /^Add$/ }))
      expect(insertionState.insertFrame).not.toHaveBeenCalled()
    })

    it('triggers handleAddRange on Enter key in range input', () => {
      const { insertionState } = setup()
      render(<FrameInsertionToolbar />)
      fireEvent.change(screen.getByPlaceholderText('Start'), { target: { value: '1' } })
      fireEvent.change(screen.getByPlaceholderText('End'), { target: { value: '2' } })
      fireEvent.keyDown(screen.getByPlaceholderText('End'), { key: 'Enter' })
      expect(insertionState.insertFrame).toHaveBeenCalledTimes(2)
    })

    it('ignores non-Enter keys in range input', () => {
      const { insertionState } = setup()
      render(<FrameInsertionToolbar />)
      fireEvent.change(screen.getByPlaceholderText('Start'), { target: { value: '1' } })
      fireEvent.change(screen.getByPlaceholderText('End'), { target: { value: '2' } })
      fireEvent.keyDown(screen.getByPlaceholderText('End'), { key: 'a' })
      expect(insertionState.insertFrame).not.toHaveBeenCalled()
    })
  })

  describe('frequency insertion', () => {
    it('renders default frequency value of 2', () => {
      setup()
      render(<FrameInsertionToolbar />)
      expect(screen.getByDisplayValue('2')).toBeInTheDocument()
    })

    it('disables Apply button when frequency is less than 1', () => {
      setup()
      render(<FrameInsertionToolbar />)
      fireEvent.change(screen.getByDisplayValue('2'), { target: { value: '0' } })
      expect(screen.getByRole('button', { name: /Apply/ })).toBeDisabled()
    })

    it('inserts frames at frequency steps using default range', () => {
      const { insertionState } = setup({ episode: { currentEpisode: { meta: { length: 10 } } } })
      render(<FrameInsertionToolbar />)
      fireEvent.change(screen.getByDisplayValue('2'), { target: { value: '3' } })
      fireEvent.click(screen.getByRole('button', { name: /Apply/ }))
      // start=0, end=8 (length-2), step=3 → 0, 3, 6 (all <length-1=9)
      expect(insertionState.insertFrame).toHaveBeenCalledTimes(3)
      expect(insertionState.insertFrame).toHaveBeenNthCalledWith(1, 0, 0.5)
      expect(insertionState.insertFrame).toHaveBeenNthCalledWith(2, 3, 0.5)
      expect(insertionState.insertFrame).toHaveBeenNthCalledWith(3, 6, 0.5)
    })

    it('inserts frames using explicit frequency from/to inputs', () => {
      const { insertionState } = setup({ episode: { currentEpisode: { meta: { length: 100 } } } })
      render(<FrameInsertionToolbar />)
      const fromInput = screen.getByPlaceholderText('0') as HTMLInputElement
      const toInput = screen.getByPlaceholderText('98') as HTMLInputElement
      fireEvent.change(fromInput, { target: { value: '10' } })
      fireEvent.change(toInput, { target: { value: '14' } })
      fireEvent.change(screen.getByDisplayValue('2'), { target: { value: '2' } })
      fireEvent.click(screen.getByRole('button', { name: /Apply/ }))
      // start=10, end=14, step=2 → 10, 12, 14
      expect(insertionState.insertFrame).toHaveBeenCalledTimes(3)
      expect(insertionState.insertFrame).toHaveBeenNthCalledWith(1, 10, 0.5)
      expect(insertionState.insertFrame).toHaveBeenNthCalledWith(2, 12, 0.5)
      expect(insertionState.insertFrame).toHaveBeenNthCalledWith(3, 14, 0.5)
      expect(fromInput.value).toBe('')
      expect(toInput.value).toBe('')
    })

    it('triggers handleAddByFrequency on Enter in frequency from input', () => {
      const { insertionState } = setup({ episode: { currentEpisode: { meta: { length: 10 } } } })
      render(<FrameInsertionToolbar />)
      fireEvent.change(screen.getByDisplayValue('2'), { target: { value: '4' } })
      fireEvent.keyDown(screen.getByPlaceholderText('0'), { key: 'Enter' })
      // start=0, end=8, step=4 → 0, 4, 8 → 8 < length-1=9 → 3 inserts
      expect(insertionState.insertFrame).toHaveBeenCalledTimes(3)
    })

    it('does not insert when frequency Enter pressed with invalid frequency', () => {
      const { insertionState } = setup()
      render(<FrameInsertionToolbar />)
      fireEvent.change(screen.getByDisplayValue('2'), { target: { value: '0' } })
      fireEvent.keyDown(screen.getByPlaceholderText('0'), { key: 'Enter' })
      expect(insertionState.insertFrame).not.toHaveBeenCalled()
    })

    it('shows preview text when frequency would insert at least one frame', () => {
      setup({ episode: { currentEpisode: { meta: { length: 10 } } } })
      render(<FrameInsertionToolbar />)
      // default freq=2, start=0, end=8 → Math.floor(8/2)+1 = 5
      expect(screen.getByText('Will insert 5 frames')).toBeInTheDocument()
    })

    it('hides preview text when frequency is below 1', () => {
      setup()
      render(<FrameInsertionToolbar />)
      fireEvent.change(screen.getByDisplayValue('2'), { target: { value: '0' } })
      expect(screen.queryByText(/Will insert/)).not.toBeInTheDocument()
    })

    it('hides preview text when start is greater than end', () => {
      setup({ episode: { currentEpisode: { meta: { length: 100 } } } })
      render(<FrameInsertionToolbar />)
      fireEvent.change(screen.getByPlaceholderText('0'), { target: { value: '50' } })
      fireEvent.change(screen.getByPlaceholderText('98'), { target: { value: '20' } })
      expect(screen.queryByText(/Will insert/)).not.toBeInTheDocument()
    })
  })

  describe('episode fallback', () => {
    it('uses default totalFrames=100 when no episode loaded', () => {
      setup({ episode: { currentFrame: 99, currentEpisode: undefined } })
      render(<FrameInsertionToolbar />)
      // currentFrame=99, totalFrames=100 → 99 >= 99 → disabled when not inserted
      expect(screen.getByRole('button', { name: /Insert After Frame 99/ })).toBeDisabled()
    })
  })
})
