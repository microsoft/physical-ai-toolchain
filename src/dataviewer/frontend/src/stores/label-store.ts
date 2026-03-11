/**
 * Label store for managing episode labels and available label options.
 */

import { create } from 'zustand';
import { devtools } from 'zustand/middleware';

interface LabelState {
    /** Available label options for the current dataset */
    availableLabels: string[];
    /** Labels per episode: episode index -> label list */
    episodeLabels: Record<number, string[]>;
    /** Saved labels per episode from the last persisted dataset state */
    savedEpisodeLabels: Record<number, string[]>;
    /** Whether the label data has been loaded */
    isLoaded: boolean;
    /** Label filter: only show episodes with these labels (empty = show all) */
    filterLabels: string[];
}

interface LabelActions {
    /** Set available label options */
    setAvailableLabels: (labels: string[]) => void;
    /** Add a new label option */
    addLabelOption: (label: string) => void;
    /** Remove a label option and strip it from assignments */
    removeLabelOption: (label: string) => void;
    /** Set all episode labels at once (bulk load) */
    setAllEpisodeLabels: (episodes: Record<string, string[]>) => void;
    /** Set labels for a specific episode */
    setEpisodeLabels: (episodeIndex: number, labels: string[]) => void;
    /** Commit the saved baseline for a specific episode */
    commitEpisodeLabels: (episodeIndex: number, labels?: string[]) => void;
    /** Toggle a label on/off for an episode */
    toggleLabel: (episodeIndex: number, label: string) => void;
    /** Set filter labels */
    setFilterLabels: (labels: string[]) => void;
    /** Toggle a filter label */
    toggleFilterLabel: (label: string) => void;
    /** Mark loaded */
    setLoaded: (loaded: boolean) => void;
    /** Reset store */
    reset: () => void;
}

type LabelStore = LabelState & LabelActions;

export const DEFAULT_LABELS: string[] = ['SUCCESS', 'FAILURE', 'PARTIAL'];

const initialState: LabelState = {
    availableLabels: DEFAULT_LABELS,
    episodeLabels: {},
    savedEpisodeLabels: {},
    isLoaded: false,
    filterLabels: [],
};

export const useLabelStore = create<LabelStore>()(
    devtools(
        (set, get) => ({
            ...initialState,

            setAvailableLabels: (labels) => {
                set({ availableLabels: labels }, false, 'setAvailableLabels');
            },

            addLabelOption: (label) => {
                const normalized = label.trim().toUpperCase();
                if (!normalized) return;
                const { availableLabels } = get();
                if (!availableLabels.includes(normalized)) {
                    set(
                        { availableLabels: [...availableLabels, normalized] },
                        false,
                        'addLabelOption',
                    );
                }
            },

            removeLabelOption: (label) => {
                const normalized = label.trim().toUpperCase();
                if (!normalized) return;

                const { availableLabels, episodeLabels, savedEpisodeLabels, filterLabels } = get();
                const nextEpisodeLabels = Object.fromEntries(
                    Object.entries(episodeLabels).map(([episodeIndex, labels]) => [
                        episodeIndex,
                        labels.filter((existing) => existing !== normalized),
                    ]),
                ) as Record<number, string[]>;
                const nextSavedEpisodeLabels = Object.fromEntries(
                    Object.entries(savedEpisodeLabels).map(([episodeIndex, labels]) => [
                        episodeIndex,
                        labels.filter((existing) => existing !== normalized),
                    ]),
                ) as Record<number, string[]>;

                set(
                    {
                        availableLabels: availableLabels.filter((existing) => existing !== normalized),
                        episodeLabels: nextEpisodeLabels,
                        savedEpisodeLabels: nextSavedEpisodeLabels,
                        filterLabels: filterLabels.filter((existing) => existing !== normalized),
                    },
                    false,
                    'removeLabelOption',
                );
            },

            setAllEpisodeLabels: (episodes) => {
                const parsed: Record<number, string[]> = {};
                for (const [key, labels] of Object.entries(episodes)) {
                    parsed[Number(key)] = labels;
                }
                set({ episodeLabels: parsed, savedEpisodeLabels: parsed }, false, 'setAllEpisodeLabels');
            },

            setEpisodeLabels: (episodeIndex, labels) => {
                const { episodeLabels } = get();
                set(
                    { episodeLabels: { ...episodeLabels, [episodeIndex]: labels } },
                    false,
                    'setEpisodeLabels',
                );
            },

            commitEpisodeLabels: (episodeIndex, labels) => {
                const { episodeLabels, savedEpisodeLabels } = get();
                const nextLabels = labels ?? episodeLabels[episodeIndex] ?? [];

                set(
                    {
                        episodeLabels: { ...episodeLabels, [episodeIndex]: nextLabels },
                        savedEpisodeLabels: { ...savedEpisodeLabels, [episodeIndex]: nextLabels },
                    },
                    false,
                    'commitEpisodeLabels',
                );
            },

            toggleLabel: (episodeIndex, label) => {
                const { episodeLabels } = get();
                const current = episodeLabels[episodeIndex] || [];
                const updated = current.includes(label)
                    ? current.filter((l) => l !== label)
                    : [...current, label];
                set(
                    { episodeLabels: { ...episodeLabels, [episodeIndex]: updated } },
                    false,
                    'toggleLabel',
                );
            },

            setFilterLabels: (labels) => {
                set({ filterLabels: labels }, false, 'setFilterLabels');
            },

            toggleFilterLabel: (label) => {
                const { filterLabels } = get();
                const updated = filterLabels.includes(label)
                    ? filterLabels.filter((l) => l !== label)
                    : [...filterLabels, label];
                set({ filterLabels: updated }, false, 'toggleFilterLabel');
            },

            setLoaded: (loaded) => {
                set({ isLoaded: loaded }, false, 'setLoaded');
            },

            reset: () => {
                set(initialState, false, 'reset');
            },
        }),
        { name: 'label-store' },
    ),
);
