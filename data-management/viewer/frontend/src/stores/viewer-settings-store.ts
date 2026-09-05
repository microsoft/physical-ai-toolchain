/**
 * Viewer display settings store.
 *
 * Non-destructive display adjustments applied via CSS filters.
 * These settings affect only the visual rendering and do NOT modify
 * frame data or export output.
 */

import { create } from 'zustand'
import { devtools } from 'zustand/middleware'
import { useShallow } from 'zustand/react/shallow'

import type { ColorAdjustment } from '@/types/episode-edit'

interface ViewerSettingsState {
  /** Display-only color adjustments */
  displayAdjustment: Required<ColorAdjustment>
  /** Whether viewer adjustments are active (non-default) */
  isActive: boolean
  /** Auto-play episodes on load */
  autoPlay: boolean
  /** Loop playback when reaching the end */
  autoLoop: boolean
  /**
   * Open/collapsed state for foldable annotation sections, keyed by section id.
   * A missing key falls back to the section's own default. Persists across
   * episode switches so folds are not reset when panels remount.
   */
  sectionOpen: Record<string, boolean>
}

interface ViewerSettingsActions {
  /** Update a single adjustment parameter */
  setAdjustment: (key: keyof ColorAdjustment, value: number) => void
  /** Reset all display adjustments to defaults */
  resetAdjustments: () => void
  /** Set auto-play preference */
  setAutoPlay: (enabled: boolean) => void
  /** Set auto-loop preference */
  setAutoLoop: (enabled: boolean) => void
  /** Set the open/collapsed state for a foldable section. */
  setSectionOpen: (id: string, open: boolean) => void
}

type ViewerSettingsStore = ViewerSettingsState & ViewerSettingsActions

const DEFAULT_DISPLAY: Required<ColorAdjustment> = {
  brightness: 0,
  contrast: 0,
  saturation: 0,
  gamma: 1,
  hue: 0,
}

function isNonDefault(adj: Required<ColorAdjustment>): boolean {
  return (
    adj.brightness !== 0 ||
    adj.contrast !== 0 ||
    adj.saturation !== 0 ||
    adj.gamma !== 1 ||
    adj.hue !== 0
  )
}

export const useViewerSettingsStore = create<ViewerSettingsStore>()(
  devtools(
    (set) => ({
      displayAdjustment: { ...DEFAULT_DISPLAY },
      isActive: false,
      autoPlay: true,
      autoLoop: true,
      sectionOpen: {},

      setAdjustment: (key, value) =>
        set((state) => {
          const next = { ...state.displayAdjustment, [key]: value }
          return { displayAdjustment: next, isActive: isNonDefault(next) }
        }),

      resetAdjustments: () => set({ displayAdjustment: { ...DEFAULT_DISPLAY }, isActive: false }),

      setAutoPlay: (enabled) => set({ autoPlay: enabled }),
      setAutoLoop: (enabled) => set({ autoLoop: enabled }),

      setSectionOpen: (id, open) =>
        set((state) => ({ sectionOpen: { ...state.sectionOpen, [id]: open } })),
    }),
    { name: 'viewer-settings' },
  ),
)

/** Convenience hook returning display adjustment and actions. */
export function useViewerDisplay() {
  return useViewerSettingsStore(
    useShallow((s) => ({
      displayAdjustment: s.displayAdjustment,
      isActive: s.isActive,
      setAdjustment: s.setAdjustment,
      resetAdjustments: s.resetAdjustments,
    })),
  )
}

/** Convenience hook returning playback preference settings. */
export function usePlaybackSettings() {
  return useViewerSettingsStore(
    useShallow((s) => ({
      autoPlay: s.autoPlay,
      autoLoop: s.autoLoop,
      setAutoPlay: s.setAutoPlay,
      setAutoLoop: s.setAutoLoop,
    })),
  )
}

/**
 * Hook returning the open state and a toggle for a foldable section. The state
 * lives in the module-level store so it survives episode switches.
 */
export function useSectionOpen(id: string, defaultOpen: boolean) {
  const open = useViewerSettingsStore((s) => s.sectionOpen[id] ?? defaultOpen)
  const setSectionOpen = useViewerSettingsStore((s) => s.setSectionOpen)
  return { open, toggle: () => setSectionOpen(id, !open) }
}
