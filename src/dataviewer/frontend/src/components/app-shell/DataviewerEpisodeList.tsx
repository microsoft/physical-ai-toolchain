import { memo, useCallback } from 'react';

import { LabelFilter } from '@/components/annotation-panel';
import { useEpisodes } from '@/hooks/use-datasets';
import { useLabelStore } from '@/stores/label-store';
import type { EpisodeMeta } from '@/types';

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
  }, [episode.index, onSelect]);

  return (
    <li>
      <button
        onClick={handleClick}
        className={`w-full text-left px-4 py-3 hover:bg-accent transition-colors ${isSelected ? 'bg-accent' : ''}`}
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

interface DataviewerEpisodeListProps {
  datasetId: string;
  onSelectEpisode: (index: number) => void;
  selectedIndex: number;
}

export function DataviewerEpisodeList({
  datasetId,
  onSelectEpisode,
  selectedIndex,
}: DataviewerEpisodeListProps) {
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
    ? episodes.filter((episode: EpisodeMeta) => {
      const episodeLabelValues = episodeLabels[episode.index] || [];
      return filterLabels.some((filterLabel) => episodeLabelValues.includes(filterLabel));
    })
    : episodes;

  return (
    <div className="overflow-y-auto h-full flex flex-col">
      <div
        className="flex items-start justify-between gap-2 border-b px-2 py-1.5"
        data-testid="episode-list-toolbar"
      >
        <div className="min-w-0 flex-1">
          <LabelFilter compact />
        </div>
        <div className="shrink-0 pt-0.5 text-xs font-medium text-muted-foreground">
          {filteredEpisodes.length}{filterLabels.length > 0 ? ` / ${episodes.length}` : ''} Episodes
        </div>
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
