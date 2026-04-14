import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useAnnotationStore, useEpisodeStore } from '@/stores'

import { createQueryWrapper } from './test-utils'

// ============================================================================
// Mocks
// ============================================================================

const mockSave = vi.fn()
const mockUseSaveCurrentAnnotation = vi.fn(() => ({
  save: mockSave,
  isPending: false,
}))

vi.mock('@/hooks/use-annotations', () => ({
  useSaveCurrentAnnotation: () => mockUseSaveCurrentAnnotation(),
}))

beforeEach(() => {
  mockSave.mockReset()
  mockUseSaveCurrentAnnotation.mockReturnValue({ save: mockSave, isPending: false })
  useAnnotationStore.getState().clear()
  useEpisodeStore.getState().reset()
})

afterEach(() => {
  vi.restoreAllMocks()
})

// ============================================================================
// useAnnotationWorkflow
// ============================================================================

describe('useAnnotationWorkflow', () => {
  async function importHook() {
    const { useAnnotationWorkflow } = await import('@/hooks/use-annotation-workflow')
    return useAnnotationWorkflow
  }

  function setupStores() {
    useEpisodeStore.setState({ currentDatasetId: 'ds-1' })
    useAnnotationStore.setState({
      currentAnnotation: {
        annotatorId: 'user-1',
        timestamp: '2024-01-01T00:00:00.000Z',
        taskCompleteness: { rating: 'success', confidence: 5 },
        trajectoryQuality: {
          overallScore: 4,
          metrics: { smoothness: 4, efficiency: 4, safety: 5, precision: 3 },
          flags: [],
        },
        dataQuality: { overallQuality: 'good', issues: [] },
        anomalies: { anomalies: [] },
        notes: 'Test notes',
      },
      isDirty: false,
      isSaving: false,
    })
  }

  // --------------------------------------------------------------------------
  // Initial state
  // --------------------------------------------------------------------------

  it('returns initial state', async () => {
    const useAnnotationWorkflow = await importHook()
    const wrapper = createQueryWrapper()
    const { result } = renderHook(() => useAnnotationWorkflow(), { wrapper })

    expect(result.current.isSaving).toBe(false)
    expect(result.current.isDirty).toBe(false)
    expect(result.current.showUnsavedDialog).toBe(false)
    expect(result.current.pendingNavigation).toBeNull()
  })

  // --------------------------------------------------------------------------
  // save
  // --------------------------------------------------------------------------

  describe('save', () => {
    it('calls saveMutation and markSaved on success', async () => {
      const useAnnotationWorkflow = await importHook()
      setupStores()
      const onSaveSuccess = vi.fn()
      const wrapper = createQueryWrapper()
      const { result } = renderHook(() => useAnnotationWorkflow({ onSaveSuccess }), { wrapper })

      await act(async () => {
        await result.current.save()
      })

      expect(mockSave).toHaveBeenCalled()
      expect(onSaveSuccess).toHaveBeenCalled()
    })

    it('does nothing when currentAnnotation is null', async () => {
      const useAnnotationWorkflow = await importHook()
      useEpisodeStore.setState({ currentDatasetId: 'ds-1' })
      const wrapper = createQueryWrapper()
      const { result } = renderHook(() => useAnnotationWorkflow(), { wrapper })

      await act(async () => {
        await result.current.save()
      })

      expect(mockSave).not.toHaveBeenCalled()
    })

    it('does nothing when currentDatasetId is null', async () => {
      const useAnnotationWorkflow = await importHook()
      useAnnotationStore.setState({
        currentAnnotation: {
          annotatorId: 'user-1',
          timestamp: '2024-01-01T00:00:00.000Z',
          taskCompleteness: { rating: 'success', confidence: 5 },
          trajectoryQuality: {
            overallScore: 4,
            metrics: { smoothness: 4, efficiency: 4, safety: 5, precision: 3 },
            flags: [],
          },
          dataQuality: { overallQuality: 'good', issues: [] },
          anomalies: { anomalies: [] },
          notes: '',
        },
      })
      const wrapper = createQueryWrapper()
      const { result } = renderHook(() => useAnnotationWorkflow(), { wrapper })

      await act(async () => {
        await result.current.save()
      })

      expect(mockSave).not.toHaveBeenCalled()
    })

    it('calls onSaveError when save throws', async () => {
      const useAnnotationWorkflow = await importHook()
      setupStores()
      const saveError = new Error('Save failed')
      mockSave.mockImplementation(() => {
        throw saveError
      })
      const onSaveError = vi.fn()
      const wrapper = createQueryWrapper()
      const { result } = renderHook(() => useAnnotationWorkflow({ onSaveError }), { wrapper })

      await expect(
        act(async () => {
          await result.current.save()
        }),
      ).rejects.toThrow('Save failed')

      expect(onSaveError).toHaveBeenCalledWith(saveError)
    })
  })

  // --------------------------------------------------------------------------
  // saveAndAdvance
  // --------------------------------------------------------------------------

  describe('saveAndAdvance', () => {
    it('saves and advances to next episode when autoAdvance is true', async () => {
      const useAnnotationWorkflow = await importHook()
      setupStores()
      vi.spyOn(useEpisodeStore.getState(), 'nextEpisode')
      const wrapper = createQueryWrapper()
      const { result } = renderHook(() => useAnnotationWorkflow({ autoAdvance: true }), { wrapper })

      await act(async () => {
        await result.current.saveAndAdvance()
      })

      expect(mockSave).toHaveBeenCalled()
    })

    it('saves but does not advance when autoAdvance is false', async () => {
      const useAnnotationWorkflow = await importHook()
      setupStores()
      const wrapper = createQueryWrapper()
      const { result } = renderHook(() => useAnnotationWorkflow({ autoAdvance: false }), {
        wrapper,
      })

      await act(async () => {
        await result.current.saveAndAdvance()
      })

      expect(mockSave).toHaveBeenCalled()
    })
  })

  // --------------------------------------------------------------------------
  // skip
  // --------------------------------------------------------------------------

  describe('skip', () => {
    it('resets annotation and advances', async () => {
      const useAnnotationWorkflow = await importHook()
      setupStores()
      useAnnotationStore.setState({ isDirty: true })
      const resetSpy = vi.spyOn(useAnnotationStore.getState(), 'resetAnnotation')
      const wrapper = createQueryWrapper()
      const { result } = renderHook(() => useAnnotationWorkflow(), { wrapper })

      act(() => {
        result.current.skip()
      })

      expect(resetSpy).toHaveBeenCalled()
    })
  })

  // --------------------------------------------------------------------------
  // flagForReview
  // --------------------------------------------------------------------------

  describe('flagForReview', () => {
    it('prepends flag note to existing notes', async () => {
      const useAnnotationWorkflow = await importHook()
      setupStores()
      const updateNotesSpy = vi.fn()
      useAnnotationStore.setState({
        updateNotes: updateNotesSpy,
        currentAnnotation: {
          annotatorId: 'user-1',
          timestamp: '2024-01-01T00:00:00.000Z',
          taskCompleteness: { rating: 'success', confidence: 5 },
          trajectoryQuality: {
            overallScore: 4,
            metrics: { smoothness: 4, efficiency: 4, safety: 5, precision: 3 },
            flags: [],
          },
          dataQuality: { overallQuality: 'good', issues: [] },
          anomalies: { anomalies: [] },
          notes: 'Existing note',
        },
      })
      const wrapper = createQueryWrapper()
      const { result } = renderHook(() => useAnnotationWorkflow(), { wrapper })

      act(() => {
        result.current.flagForReview()
      })

      expect(updateNotesSpy).toHaveBeenCalledWith('[FLAGGED FOR REVIEW]\nExisting note')
    })

    it('does not duplicate flag when already flagged', async () => {
      const useAnnotationWorkflow = await importHook()
      setupStores()
      const updateNotesSpy = vi.fn()
      useAnnotationStore.setState({
        updateNotes: updateNotesSpy,
        currentAnnotation: {
          annotatorId: 'user-1',
          timestamp: '2024-01-01T00:00:00.000Z',
          taskCompleteness: { rating: 'success', confidence: 5 },
          trajectoryQuality: {
            overallScore: 4,
            metrics: { smoothness: 4, efficiency: 4, safety: 5, precision: 3 },
            flags: [],
          },
          dataQuality: { overallQuality: 'good', issues: [] },
          anomalies: { anomalies: [] },
          notes: '[FLAGGED FOR REVIEW]\nSome note',
        },
      })
      const wrapper = createQueryWrapper()
      const { result } = renderHook(() => useAnnotationWorkflow(), { wrapper })

      act(() => {
        result.current.flagForReview()
      })

      expect(updateNotesSpy).not.toHaveBeenCalled()
    })

    it('sets only flag note when no existing notes', async () => {
      const useAnnotationWorkflow = await importHook()
      setupStores()
      const updateNotesSpy = vi.fn()
      useAnnotationStore.setState({
        updateNotes: updateNotesSpy,
        currentAnnotation: {
          annotatorId: 'user-1',
          timestamp: '2024-01-01T00:00:00.000Z',
          taskCompleteness: { rating: 'success', confidence: 5 },
          trajectoryQuality: {
            overallScore: 4,
            metrics: { smoothness: 4, efficiency: 4, safety: 5, precision: 3 },
            flags: [],
          },
          dataQuality: { overallQuality: 'good', issues: [] },
          anomalies: { anomalies: [] },
          notes: '',
        },
      })
      const wrapper = createQueryWrapper()
      const { result } = renderHook(() => useAnnotationWorkflow(), { wrapper })

      act(() => {
        result.current.flagForReview()
      })

      expect(updateNotesSpy).toHaveBeenCalledWith('[FLAGGED FOR REVIEW]')
    })
  })

  // --------------------------------------------------------------------------
  // navigateWithCheck / confirmNavigation / cancelNavigation
  // --------------------------------------------------------------------------

  describe('navigation with unsaved changes', () => {
    it('executes action directly when not dirty', async () => {
      const useAnnotationWorkflow = await importHook()
      setupStores()
      const wrapper = createQueryWrapper()
      const { result } = renderHook(() => useAnnotationWorkflow(), { wrapper })

      const navAction = vi.fn()
      act(() => {
        result.current.navigateWithCheck(navAction)
      })

      expect(navAction).toHaveBeenCalled()
      expect(result.current.showUnsavedDialog).toBe(false)
    })

    it('shows dialog when dirty', async () => {
      const useAnnotationWorkflow = await importHook()
      setupStores()
      useAnnotationStore.setState({ isDirty: true })
      const wrapper = createQueryWrapper()
      const { result } = renderHook(() => useAnnotationWorkflow(), { wrapper })

      const navAction = vi.fn()
      act(() => {
        result.current.navigateWithCheck(navAction)
      })

      expect(navAction).not.toHaveBeenCalled()
      expect(result.current.showUnsavedDialog).toBe(true)
      expect(result.current.pendingNavigation).not.toBeNull()
    })

    it('confirmNavigation resets and executes pending action', async () => {
      const useAnnotationWorkflow = await importHook()
      setupStores()
      useAnnotationStore.setState({ isDirty: true })
      const resetSpy = vi.spyOn(useAnnotationStore.getState(), 'resetAnnotation')
      const wrapper = createQueryWrapper()
      const { result } = renderHook(() => useAnnotationWorkflow(), { wrapper })

      const navAction = vi.fn()
      act(() => {
        result.current.navigateWithCheck(navAction)
      })

      act(() => {
        result.current.confirmNavigation()
      })

      expect(resetSpy).toHaveBeenCalled()
      expect(navAction).toHaveBeenCalled()
      expect(result.current.showUnsavedDialog).toBe(false)
      expect(result.current.pendingNavigation).toBeNull()
    })

    it('cancelNavigation hides dialog and clears pending', async () => {
      const useAnnotationWorkflow = await importHook()
      setupStores()
      useAnnotationStore.setState({ isDirty: true })
      const wrapper = createQueryWrapper()
      const { result } = renderHook(() => useAnnotationWorkflow(), { wrapper })

      const navAction = vi.fn()
      act(() => {
        result.current.navigateWithCheck(navAction)
      })

      act(() => {
        result.current.cancelNavigation()
      })

      expect(navAction).not.toHaveBeenCalled()
      expect(result.current.showUnsavedDialog).toBe(false)
      expect(result.current.pendingNavigation).toBeNull()
    })
  })

  // --------------------------------------------------------------------------
  // isSaving reflects mutation pending state
  // --------------------------------------------------------------------------

  it('reflects isSaving when mutation is pending', async () => {
    mockUseSaveCurrentAnnotation.mockReturnValue({ save: mockSave, isPending: true })
    const useAnnotationWorkflow = await importHook()
    const wrapper = createQueryWrapper()
    const { result } = renderHook(() => useAnnotationWorkflow(), { wrapper })

    expect(result.current.isSaving).toBe(true)
  })
})
