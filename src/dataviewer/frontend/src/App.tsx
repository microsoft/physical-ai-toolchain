import { useState, useEffect, useCallback, memo } from 'react';
import { QueryClientProvider } from '@tanstack/react-query';
import { queryClient } from '@/lib/query-client';
import { useDatasets, useEpisodes, useEpisode } from '@/hooks/use-datasets';
import { useDatasetLabels } from '@/hooks/use-labels';
import { useEpisodeStore, useDatasetStore } from '@/stores';
import { useLabelStore } from '@/stores/label-store';
import { AnnotationWorkspace } from '@/components/annotation-workspace/AnnotationWorkspace';
import { LabelFilter } from '@/components/annotation-panel';
import { ThemeToggle } from '@/components/theme-toggle';
import { TooltipProvider } from '@/components/ui/tooltip';
import type { EpisodeMeta } from '@/types';

/**
 * Memoized episode list item to prevent re-renders on sibling selection changes.
 */
const EpisodeListItem = memo(function EpisodeListItem({
  episode,
  isSelected,
  onSelect,
  labels,
}: {
  episode: EpisodeMeta;
  isSelected: boolean;
  onSelect: (index: number) => void;
  labels: string[];
}) {
  const handleClick = useCallback(() => {
    onSelect(episode.index);
  }, [onSelect, episode.index]);

  return (
    <li>
      <button
        onClick={handleClick}
        className={`w-full text-left px-4 py-3 hover:bg-accent transition-colors ${isSelected ? 'bg-accent' : ''
          }`}
      >
        <div className="font-medium">Episode {episode.index}</div>
        <div className="text-sm text-muted-foreground">
          {episode.length} frames • Task {episode.taskIndex}
          {episode.hasAnnotations && (
            <span className="ml-2 text-green-600">✓ Annotated</span>
          )}
        </div>
        {labels.length > 0 && (
          <div className="flex flex-wrap gap-1 mt-1">
            {labels.map((label) => (
              <span
                key={label}
                className="inline-flex items-center rounded-full bg-primary/10 text-primary text-[10px] px-1.5 py-0 font-medium"
              >
                {label}
              </span>
            ))}
          </div>
        )}
      </button>
    </li>
  );
});

function EpisodeList({
  datasetId,
  onSelectEpisode,
  selectedIndex,
}: {
  datasetId: string;
  onSelectEpisode: (index: number) => void;
  selectedIndex: number;
}) {
  const { data: episodes, isLoading, error } = useEpisodes(datasetId, { limit: 100 });
  const episodeLabels = useLabelStore((state) => state.episodeLabels);
  const filterLabels = useLabelStore((state) => state.filterLabels);

  if (isLoading) {
    return <div className="p-4 text-muted-foreground">Loading episodes...</div>;
  }

  if (error) {
    return <div className="p-4 text-red-500">Error: {error.message}</div>;
  }

  if (!episodes || episodes.length === 0) {
    return <div className="p-4 text-muted-foreground">No episodes found</div>;
  }

  const filteredEpisodes = filterLabels.length > 0
    ? episodes.filter((ep: EpisodeMeta) => {
      const epLabels = episodeLabels[ep.index] || [];
      return filterLabels.some((fl) => epLabels.includes(fl));
    })
    : episodes;

  return (
    <div className="overflow-y-auto h-full flex flex-col">
      <LabelFilter />
      <div className="p-2 text-sm font-medium text-muted-foreground border-b">
        {filteredEpisodes.length}{filterLabels.length > 0 ? ` / ${episodes.length}` : ''} Episodes
      </div>
      <ul className="divide-y flex-1 overflow-y-auto">
        {filteredEpisodes.map((episode: EpisodeMeta) => (
          <EpisodeListItem
            key={episode.index}
            episode={episode}
            isSelected={selectedIndex === episode.index}
            onSelect={onSelectEpisode}
            labels={episodeLabels[episode.index] || []}
          />
        ))}
      </ul>
    </div>
  );
}

function EpisodeViewer({ datasetId, episodeIndex }: { datasetId: string; episodeIndex: number }) {
  const { data: episode, isLoading, error } = useEpisode(datasetId, episodeIndex);
  const setCurrentEpisode = useEpisodeStore((state) => state.setCurrentEpisode);
  const setDatasets = useDatasetStore((state) => state.setDatasets);
  const selectDataset = useDatasetStore((state) => state.selectDataset);

  // Sync dataset and episode to stores for AnnotationWorkspace
  useEffect(() => {
    // Create a minimal dataset info object for the store
    setDatasets([{
      id: datasetId,
      name: datasetId,
      totalEpisodes: 0,
      fps: 15,
      features: {},
      tasks: []
    }]);
    selectDataset(datasetId);
  }, [datasetId, setDatasets, selectDataset]);

  useEffect(() => {
    if (episode) {
      setCurrentEpisode(episode);
    }
  }, [episode, setCurrentEpisode]);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-muted-foreground">Loading episode {episodeIndex}...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-red-500">Error loading episode: {error.message}</div>
      </div>
    );
  }

  if (!episode) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-muted-foreground">No episode data</div>
      </div>
    );
  }

  // Render the new AnnotationWorkspace with all features
  return <AnnotationWorkspace />;
}

function AppContent() {
  const [datasetId, setDatasetId] = useState('');
  const [selectedEpisode, setSelectedEpisode] = useState<number>(0);
  const { data: datasets } = useDatasets();

  // Load labels for the selected dataset
  useDatasetLabels();

  // Auto-select the first available dataset
  useEffect(() => {
    if (datasets && datasets.length > 0 && !datasetId) {
      setDatasetId(datasets[0].id);
    }
  }, [datasets, datasetId]);

  return (
    <div className="flex flex-col h-screen">
      {/* Header */}
      <header className="bg-card border-b px-6 py-4 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Robotic Training Data Analysis</h1>
          <p className="text-sm text-muted-foreground">
            Episode annotation system for robot demonstration datasets
          </p>
        </div>
        <div className="flex items-center gap-3">
          <label className="text-sm">Dataset:</label>
          {datasets && datasets.length > 1 ? (
            <select
              value={datasetId}
              onChange={(e) => { setDatasetId(e.target.value); setSelectedEpisode(0); }}
              className="px-3 py-1 border border-input rounded text-sm w-64 bg-background text-foreground"
            >
              {datasets.map((ds) => (
                <option key={ds.id} value={ds.id}>{ds.name}</option>
              ))}
            </select>
          ) : (
            <input
              type="text"
              value={datasetId}
              onChange={(e) => setDatasetId(e.target.value)}
              className="px-3 py-1 border border-input rounded text-sm w-64 bg-background text-foreground"
              placeholder="Dataset ID"
            />
          )}
          <ThemeToggle />
        </div>
      </header>

      {/* Main Content */}
      <div className="flex flex-1 min-h-0">
        {/* Episode List Sidebar */}
        <aside className="w-64 border-r bg-card overflow-hidden flex flex-col">
          <EpisodeList
            datasetId={datasetId}
            onSelectEpisode={setSelectedEpisode}
            selectedIndex={selectedEpisode}
          />
        </aside>

        {/* Episode Viewer */}
        <main className="flex-1 overflow-hidden bg-background">
          <EpisodeViewer datasetId={datasetId} episodeIndex={selectedEpisode} />
        </main>
      </div>
    </div>
  );
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <AppContent />
      </TooltipProvider>
    </QueryClientProvider>
  );
}

export default App
