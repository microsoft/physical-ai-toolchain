/**
 * Episode store for managing current episode and navigation state.
 */

import { create } from 'zustand';
import { devtools } from 'zustand/middleware';

import type { EpisodeData,EpisodeMeta } from '@/types';

interface EpisodeState {
  /** List of episode metadata for current dataset */
  episodes: EpisodeMeta[];
  /** Currently loaded episode data */
  currentEpisode: EpisodeData | null;
  /** Current episode index */
  currentIndex: number;
  /** Current dataset ID */
  currentDatasetId: string | null;
  /** Loading state */
  isLoading: boolean;
  /** Error message if any */
  error: string | null;
  /** Current playback frame */
  currentFrame: number;
  /** Whether video is playing */
  isPlaying: boolean;
  /** Playback speed multiplier */
  playbackSpeed: number;
}

interface EpisodeActions {
  /** Set the list of episodes */
  setEpisodes: (episodes: EpisodeMeta[]) => void;
  /** Set the current episode data */
  setCurrentEpisode: (episode: EpisodeData | null) => void;
  /** Navigate to a specific episode index */
  navigateToEpisode: (index: number) => void;
  /** Navigate to the next episode */
  nextEpisode: () => void;
  /** Navigate to the previous episode */
  previousEpisode: () => void;
  /** Set loading state */
  setLoading: (isLoading: boolean) => void;
  /** Set error state */
  setError: (error: string | null) => void;
  /** Set the current playback frame */
  setCurrentFrame: (frame: number) => void;
  /** Toggle play/pause */
  togglePlayback: () => void;
  /** Set playback speed */
  setPlaybackSpeed: (speed: number) => void;
  /** Reset the store to initial state */
  reset: () => void;
}

type EpisodeStore = EpisodeState & EpisodeActions;

const initialState: EpisodeState = {
  episodes: [],
  currentEpisode: null,
  currentIndex: -1,
  currentDatasetId: null,
  isLoading: false,
  error: null,
  currentFrame: 0,
  isPlaying: false,
  playbackSpeed: 1.0,
};

/**
 * Zustand store for episode state management.
 *
 * @example
 * ```tsx
 * const { currentEpisode, nextEpisode, previousEpisode } = useEpisodeStore();
 *
 * // Navigate between episodes
 * nextEpisode();
 * previousEpisode();
 *
 * // Navigate to specific episode
 * navigateToEpisode(42);
 * ```
 */
export const useEpisodeStore = create<EpisodeStore>()(
  devtools(
    (set, get) => ({
      ...initialState,

      setEpisodes: (episodes) => {
        set({ episodes, error: null }, false, 'setEpisodes');
      },

      setCurrentEpisode: (episode) => {
        set(
          {
            currentEpisode: episode,
            currentIndex: episode?.meta.index ?? -1,
            currentFrame: 0,
            isPlaying: false,
          },
          false,
          'setCurrentEpisode'
        );
      },

      navigateToEpisode: (index) => {
        const { episodes } = get();
        if (index >= 0 && index < episodes.length) {
          set({ currentIndex: index, isLoading: true }, false, 'navigateToEpisode');
        }
      },

      nextEpisode: () => {
        const { currentIndex, episodes } = get();
        if (currentIndex < episodes.length - 1) {
          set(
            { currentIndex: currentIndex + 1, isLoading: true },
            false,
            'nextEpisode'
          );
        }
      },

      previousEpisode: () => {
        const { currentIndex } = get();
        if (currentIndex > 0) {
          set(
            { currentIndex: currentIndex - 1, isLoading: true },
            false,
            'previousEpisode'
          );
        }
      },

      setLoading: (isLoading) => {
        set({ isLoading }, false, 'setLoading');
      },

      setError: (error) => {
        set({ error, isLoading: false }, false, 'setError');
      },

      setCurrentFrame: (frame) => {
        const { currentEpisode } = get();
        const maxFrame = currentEpisode?.meta.length ?? 0;
        const clampedFrame = Math.max(0, Math.min(frame, maxFrame - 1));
        set({ currentFrame: clampedFrame }, false, 'setCurrentFrame');
      },

      togglePlayback: () => {
        set((state) => ({ isPlaying: !state.isPlaying }), false, 'togglePlayback');
      },

      setPlaybackSpeed: (speed) => {
        set({ playbackSpeed: speed }, false, 'setPlaybackSpeed');
      },

      reset: () => {
        set(initialState, false, 'reset');
      },
    }),
    { name: 'episode-store' }
  )
);

// Selector hooks for common patterns
export const useCurrentEpisodeIndex = () =>
  useEpisodeStore((state) => state.currentIndex);

export const useEpisodeNavigation = () => {
  const currentIndex = useEpisodeStore((state) => state.currentIndex);
  const episodesLength = useEpisodeStore((state) => state.episodes.length);
  const nextEpisode = useEpisodeStore((state) => state.nextEpisode);
  const previousEpisode = useEpisodeStore((state) => state.previousEpisode);
  
  return {
    canGoNext: currentIndex < episodesLength - 1,
    canGoPrevious: currentIndex > 0,
    nextEpisode,
    previousEpisode,
  };
};

export const usePlaybackControls = () => {
  const currentFrame = useEpisodeStore((state) => state.currentFrame);
  const isPlaying = useEpisodeStore((state) => state.isPlaying);
  const playbackSpeed = useEpisodeStore((state) => state.playbackSpeed);
  const setCurrentFrame = useEpisodeStore((state) => state.setCurrentFrame);
  const togglePlayback = useEpisodeStore((state) => state.togglePlayback);
  const setPlaybackSpeed = useEpisodeStore((state) => state.setPlaybackSpeed);
  
  return {
    currentFrame,
    isPlaying,
    playbackSpeed,
    setCurrentFrame,
    togglePlayback,
    setPlaybackSpeed,
  };
};
