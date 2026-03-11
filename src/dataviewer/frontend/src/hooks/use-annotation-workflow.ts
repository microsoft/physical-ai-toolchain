/**
 * Annotation workflow hook for save, skip, and navigation actions.
 */

import { useCallback, useState } from 'react';

import { useSaveCurrentAnnotation } from '@/hooks/use-annotations';
import { useAnnotationStore, useEpisodeStore } from '@/stores';

export interface UseAnnotationWorkflowOptions {
  /** Callback after successful save */
  onSaveSuccess?: () => void;
  /** Callback after save error */
  onSaveError?: (error: Error) => void;
  /** Whether to auto-advance after save */
  autoAdvance?: boolean;
}

export interface AnnotationWorkflowState {
  /** Whether a save is in progress */
  isSaving: boolean;
  /** Whether there are unsaved changes */
  isDirty: boolean;
  /** Whether the unsaved changes dialog is open */
  showUnsavedDialog: boolean;
  /** Pending navigation action */
  pendingNavigation: (() => void) | null;
}

export interface AnnotationWorkflowActions {
  /** Save current annotation */
  save: () => Promise<void>;
  /** Save and advance to next episode */
  saveAndAdvance: () => Promise<void>;
  /** Skip current episode without saving */
  skip: () => void;
  /** Flag current episode for review */
  flagForReview: () => void;
  /** Navigate with unsaved changes check */
  navigateWithCheck: (action: () => void) => void;
  /** Confirm pending navigation (discard changes) */
  confirmNavigation: () => void;
  /** Cancel pending navigation */
  cancelNavigation: () => void;
}

/**
 * Hook for managing the annotation workflow.
 *
 * @example
 * ```tsx
 * const { save, saveAndAdvance, skip, isDirty } = useAnnotationWorkflow({
 *   onSaveSuccess: () => toast.success('Saved!'),
 * });
 * ```
 */
export function useAnnotationWorkflow(
  options: UseAnnotationWorkflowOptions = {}
): AnnotationWorkflowState & AnnotationWorkflowActions {
  const { onSaveSuccess, onSaveError, autoAdvance = true } = options;

  const [showUnsavedDialog, setShowUnsavedDialog] = useState(false);
  const [pendingNavigation, setPendingNavigation] = useState<(() => void) | null>(null);

  const isDirty = useAnnotationStore((state) => state.isDirty);
  const isSaving = useAnnotationStore((state) => state.isSaving);
  const currentAnnotation = useAnnotationStore((state) => state.currentAnnotation);
  const updateNotes = useAnnotationStore((state) => state.updateNotes);
  const markSaved = useAnnotationStore((state) => state.markSaved);

  const nextEpisode = useEpisodeStore((state) => state.nextEpisode);
  const currentDatasetId = useEpisodeStore((state) => state.currentDatasetId);

  const saveMutation = useSaveCurrentAnnotation();

  const save = useCallback(async () => {
    if (!currentAnnotation || !currentDatasetId) return;

    try {
      saveMutation.save();
      markSaved();
      onSaveSuccess?.();
    } catch (error) {
      onSaveError?.(error as Error);
      throw error;
    }
  }, [
    currentAnnotation,
    currentDatasetId,
    saveMutation,
    markSaved,
    onSaveSuccess,
    onSaveError,
  ]);

  const saveAndAdvance = useCallback(async () => {
    await save();
    if (autoAdvance) {
      nextEpisode();
    }
  }, [save, autoAdvance, nextEpisode]);

  const skip = useCallback(() => {
    // Reset changes and advance
    useAnnotationStore.getState().resetAnnotation();
    nextEpisode();
  }, [nextEpisode]);

  const flagForReview = useCallback(() => {
    // Add a note flagging for review
    const currentNotes = currentAnnotation?.notes ?? '';
    const flagNote = '[FLAGGED FOR REVIEW]';
    if (!currentNotes.includes(flagNote)) {
      updateNotes(currentNotes ? `${flagNote}\n${currentNotes}` : flagNote);
    }
  }, [currentAnnotation?.notes, updateNotes]);

  const navigateWithCheck = useCallback(
    (action: () => void) => {
      if (isDirty) {
        setPendingNavigation(() => action);
        setShowUnsavedDialog(true);
      } else {
        action();
      }
    },
    [isDirty]
  );

  const confirmNavigation = useCallback(() => {
    // Discard changes and execute pending navigation
    useAnnotationStore.getState().resetAnnotation();
    setShowUnsavedDialog(false);
    if (pendingNavigation) {
      pendingNavigation();
      setPendingNavigation(null);
    }
  }, [pendingNavigation]);

  const cancelNavigation = useCallback(() => {
    setShowUnsavedDialog(false);
    setPendingNavigation(null);
  }, []);

  return {
    // State
    isSaving: saveMutation.isPending || isSaving,
    isDirty,
    showUnsavedDialog,
    pendingNavigation,
    // Actions
    save,
    saveAndAdvance,
    skip,
    flagForReview,
    navigateWithCheck,
    confirmNavigation,
    cancelNavigation,
  };
}
