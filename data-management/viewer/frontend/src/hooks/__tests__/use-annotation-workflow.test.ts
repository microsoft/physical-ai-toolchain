import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useAnnotationWorkflow } from '@/hooks/use-annotation-workflow'
import { useAnnotationStore, useEpisodeStore } from '@/stores'

const saveMock = vi.fn()

vi.mock('@/hooks/use-annotations', () => ({
  useSaveCurrentAnnotation: () => ({
    save: saveMock,
    isPending: false,
    isSuccess: false,
    isError: false,
    error: null,
  }),
}))

const nextEpisodeMock = vi.fn()

beforeEach(() => {
  saveMock.mockReset()
  nextEpisodeMock.mockReset()
  useAnnotationStore.getState().clear()
  useEpisodeStore.getState().reset()
  useEpisodeStore.setState({
    currentDatasetId: 'ds-1',
    currentIndex: 0,
    nextEpisode: nextEpisodeMock,
  } as never)
  useAnnotationStore.getState().initializeAnnotation('tester')
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('useAnnotationWorkflow', () => {
  it('save() invokes the underlying save and marks the store as saved', async () => {
    const onSaveSuccess = vi.fn()
    const { result } = renderHook(() => useAnnotationWorkflow({ onSaveSuccess }))

    useAnnotationStore.getState().updateNotes('hello')
    expect(useAnnotationStore.getState().isDirty).toBe(true)

    await act(async () => {
      await result.current.save()
    })

    expect(saveMock).toHaveBeenCalledTimes(1)
    expect(useAnnotationStore.getState().isDirty).toBe(false)
    expect(onSaveSuccess).toHaveBeenCalledTimes(1)
  })

  it('save() is a no-op when there is no current annotation or dataset', async () => {
    useAnnotationStore.getState().clear()

    const { result } = renderHook(() => useAnnotationWorkflow())

    await act(async () => {
      await result.current.save()
    })

    expect(saveMock).not.toHaveBeenCalled()
  })

  it('saveAndAdvance() advances to next episode by default', async () => {
    const { result } = renderHook(() => useAnnotationWorkflow())

    await act(async () => {
      await result.current.saveAndAdvance()
    })

    expect(saveMock).toHaveBeenCalledTimes(1)
    expect(nextEpisodeMock).toHaveBeenCalledTimes(1)
  })

  it('saveAndAdvance() does not advance when autoAdvance is false', async () => {
    const { result } = renderHook(() => useAnnotationWorkflow({ autoAdvance: false }))

    await act(async () => {
      await result.current.saveAndAdvance()
    })

    expect(saveMock).toHaveBeenCalledTimes(1)
    expect(nextEpisodeMock).not.toHaveBeenCalled()
  })

  it('skip() resets the annotation and advances', async () => {
    useAnnotationStore.getState().updateNotes('draft')
    expect(useAnnotationStore.getState().isDirty).toBe(true)

    const { result } = renderHook(() => useAnnotationWorkflow())

    act(() => {
      result.current.skip()
    })

    expect(nextEpisodeMock).toHaveBeenCalledTimes(1)
    expect(useAnnotationStore.getState().isDirty).toBe(false)
  })

  it('flagForReview() is idempotent', async () => {
    const { result } = renderHook(() => useAnnotationWorkflow())

    act(() => {
      result.current.flagForReview()
    })
    const after1 = useAnnotationStore.getState().currentAnnotation?.notes
    expect(after1).toContain('[FLAGGED FOR REVIEW]')

    act(() => {
      result.current.flagForReview()
    })
    const after2 = useAnnotationStore.getState().currentAnnotation?.notes
    expect(after2).toBe(after1)
  })

  it('navigateWithCheck opens dialog when dirty and runs immediately when clean', async () => {
    const action = vi.fn()
    const { result, rerender } = renderHook(() => useAnnotationWorkflow())

    act(() => {
      result.current.navigateWithCheck(action)
    })
    expect(action).toHaveBeenCalledTimes(1)
    expect(result.current.showUnsavedDialog).toBe(false)

    act(() => {
      useAnnotationStore.getState().updateNotes('dirty')
    })
    rerender()

    const dirtyAction = vi.fn()
    act(() => {
      result.current.navigateWithCheck(dirtyAction)
    })

    expect(dirtyAction).not.toHaveBeenCalled()
    expect(result.current.showUnsavedDialog).toBe(true)
    expect(result.current.pendingNavigation).not.toBeNull()
  })

  it('confirmNavigation runs the pending action and resets state', async () => {
    const action = vi.fn()
    const { result } = renderHook(() => useAnnotationWorkflow())

    act(() => {
      useAnnotationStore.getState().updateNotes('dirty')
    })
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

  it('cancelNavigation closes the dialog without running the action', async () => {
    const action = vi.fn()
    const { result } = renderHook(() => useAnnotationWorkflow())

    act(() => {
      useAnnotationStore.getState().updateNotes('dirty')
    })
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
})
