/**
 * Tests for useAnnotationWorkflow hook.
 */

import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/hooks/use-annotations', () => ({
  useSaveCurrentAnnotation: vi.fn(),
}))

import { useSaveCurrentAnnotation } from '@/hooks/use-annotations'
import { useAnnotationWorkflow } from '@/hooks/use-annotation-workflow'
import { useAnnotationStore, useEpisodeStore } from '@/stores'

const mockedUseSaveCurrentAnnotation = vi.mocked(useSaveCurrentAnnotation)

function setupAnnotation(notes = '') {
  const store = useAnnotationStore.getState()
  store.initializeAnnotation('tester')
  if (notes) {
    store.updateNotes(notes)
    // Mark clean so isDirty starts false unless test mutates it
    store.markSaved()
  }
}

describe('useAnnotationWorkflow', () => {
  let saveFn: ReturnType<typeof vi.fn>

  beforeEach(() => {
    saveFn = vi.fn()
    mockedUseSaveCurrentAnnotation.mockReturnValue({
      save: saveFn,
      isPending: false,
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any)
    useAnnotationStore.getState().clear()
    useEpisodeStore.getState().reset()
    useEpisodeStore.setState({ currentDatasetId: 'ds-1' })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('save bails when no current annotation', async () => {
    const onSaveSuccess = vi.fn()
    const { result } = renderHook(() => useAnnotationWorkflow({ onSaveSuccess }))

    await act(async () => {
      await result.current.save()
    })

    expect(saveFn).not.toHaveBeenCalled()
    expect(onSaveSuccess).not.toHaveBeenCalled()
  })

  it('save bails when no current dataset id', async () => {
    setupAnnotation()
    useEpisodeStore.setState({ currentDatasetId: null })
    const { result } = renderHook(() => useAnnotationWorkflow())

    await act(async () => {
      await result.current.save()
    })

    expect(saveFn).not.toHaveBeenCalled()
  })

  it('save invokes mutation, marks saved, and fires onSaveSuccess', async () => {
    setupAnnotation()
    useAnnotationStore.getState().updateNotes('changed')
    expect(useAnnotationStore.getState().isDirty).toBe(true)

    const onSaveSuccess = vi.fn()
    const { result } = renderHook(() => useAnnotationWorkflow({ onSaveSuccess }))

    await act(async () => {
      await result.current.save()
    })

    expect(saveFn).toHaveBeenCalledTimes(1)
    expect(onSaveSuccess).toHaveBeenCalledTimes(1)
    expect(useAnnotationStore.getState().isDirty).toBe(false)
  })

  it('saveAndAdvance advances when autoAdvance true (default)', async () => {
    setupAnnotation()
    useEpisodeStore.setState({
      episodes: [
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        { episodeId: 'a' } as any,
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        { episodeId: 'b' } as any,
      ],
      currentIndex: 0,
    })
    const { result } = renderHook(() => useAnnotationWorkflow())

    await act(async () => {
      await result.current.saveAndAdvance()
    })

    expect(saveFn).toHaveBeenCalledTimes(1)
    expect(useEpisodeStore.getState().currentIndex).toBe(1)
  })

  it('saveAndAdvance does not advance when autoAdvance false', async () => {
    setupAnnotation()
    useEpisodeStore.setState({
      episodes: [
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        { episodeId: 'a' } as any,
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        { episodeId: 'b' } as any,
      ],
      currentIndex: 0,
    })
    const { result } = renderHook(() => useAnnotationWorkflow({ autoAdvance: false }))

    await act(async () => {
      await result.current.saveAndAdvance()
    })

    expect(useEpisodeStore.getState().currentIndex).toBe(0)
  })

  it('skip resets annotation and advances', () => {
    setupAnnotation()
    useAnnotationStore.getState().updateNotes('dirty')
    useEpisodeStore.setState({
      episodes: [
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        { episodeId: 'a' } as any,
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        { episodeId: 'b' } as any,
      ],
      currentIndex: 0,
    })
    const { result } = renderHook(() => useAnnotationWorkflow())

    act(() => {
      result.current.skip()
    })

    expect(useAnnotationStore.getState().isDirty).toBe(false)
    expect(useEpisodeStore.getState().currentIndex).toBe(1)
  })

  it('flagForReview prepends flag note', () => {
    setupAnnotation('original')
    const { result } = renderHook(() => useAnnotationWorkflow())

    act(() => {
      result.current.flagForReview()
    })

    expect(useAnnotationStore.getState().currentAnnotation?.notes).toBe(
      '[FLAGGED FOR REVIEW]\noriginal',
    )
  })

  it('flagForReview is no-op when already flagged', () => {
    setupAnnotation('[FLAGGED FOR REVIEW]\noriginal')
    const before = useAnnotationStore.getState().currentAnnotation?.notes
    const { result } = renderHook(() => useAnnotationWorkflow())

    act(() => {
      result.current.flagForReview()
    })

    expect(useAnnotationStore.getState().currentAnnotation?.notes).toBe(before)
  })

  it('navigateWithCheck runs action immediately when not dirty', () => {
    setupAnnotation()
    const action = vi.fn()
    const { result } = renderHook(() => useAnnotationWorkflow())

    act(() => {
      result.current.navigateWithCheck(action)
    })

    expect(action).toHaveBeenCalledTimes(1)
    expect(result.current.showUnsavedDialog).toBe(false)
  })

  it('navigateWithCheck opens dialog when dirty and defers action', () => {
    setupAnnotation()
    useAnnotationStore.getState().updateNotes('dirty')
    const action = vi.fn()
    const { result } = renderHook(() => useAnnotationWorkflow())

    act(() => {
      result.current.navigateWithCheck(action)
    })

    expect(action).not.toHaveBeenCalled()
    expect(result.current.showUnsavedDialog).toBe(true)
    expect(result.current.pendingNavigation).not.toBeNull()
  })

  it('confirmNavigation discards changes and runs pending action', () => {
    setupAnnotation()
    useAnnotationStore.getState().updateNotes('dirty')
    const action = vi.fn()
    const { result } = renderHook(() => useAnnotationWorkflow())

    act(() => {
      result.current.navigateWithCheck(action)
    })
    act(() => {
      result.current.confirmNavigation()
    })

    expect(action).toHaveBeenCalledTimes(1)
    expect(result.current.showUnsavedDialog).toBe(false)
    expect(result.current.pendingNavigation).toBeNull()
    expect(useAnnotationStore.getState().isDirty).toBe(false)
  })

  it('cancelNavigation clears pending state without running action', () => {
    setupAnnotation()
    useAnnotationStore.getState().updateNotes('dirty')
    const action = vi.fn()
    const { result } = renderHook(() => useAnnotationWorkflow())

    act(() => {
      result.current.navigateWithCheck(action)
    })
    act(() => {
      result.current.cancelNavigation()
    })

    expect(action).not.toHaveBeenCalled()
    expect(result.current.showUnsavedDialog).toBe(false)
    expect(result.current.pendingNavigation).toBeNull()
  })

  it('isSaving reflects mutation pending state', () => {
    setupAnnotation()
    mockedUseSaveCurrentAnnotation.mockReturnValue({
      save: saveFn,
      isPending: true,
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any)
    const { result } = renderHook(() => useAnnotationWorkflow())
    expect(result.current.isSaving).toBe(true)
  })
})
