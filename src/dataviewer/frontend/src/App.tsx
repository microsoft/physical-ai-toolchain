import { QueryClientProvider } from '@tanstack/react-query';

import { DataviewerEpisodeList } from '@/components/app-shell/DataviewerEpisodeList';
import { DataviewerEpisodeViewer } from '@/components/app-shell/DataviewerEpisodeViewer';
import { DataviewerShellHeader } from '@/components/app-shell/DataviewerShellHeader';
import { TooltipProvider } from '@/components/ui/tooltip';
import { useCapabilities, useDatasets, useEpisodes } from '@/hooks/use-datasets';
import { useDataviewerShellState } from '@/hooks/use-dataviewer-shell-state';
import { useJointConfig } from '@/hooks/use-joint-config';
import { useDatasetLabels } from '@/hooks/use-labels';
import { queryClient } from '@/lib/query-client';

export function AppContent() {
  const { data: datasets } = useDatasets();
  const shellState = useDataviewerShellState({ datasets });
  const datasetId = shellState.datasetId;
  const diagnosticsVisible = shellState.diagnosticsVisible;
  const selectedEpisode = shellState.selectedEpisode;
  const setDatasetId = shellState.setDatasetId;
  const setSelectedEpisode = shellState.setSelectedEpisode;
  const toggleDiagnostics = shellState.toggleDiagnostics;
  const { data: capabilities } = useCapabilities(datasetId || undefined);
  const { data: episodes } = useEpisodes(datasetId, { limit: 100 });

  // Load labels for the selected dataset
  useDatasetLabels();

  // Load joint configuration for the selected dataset
  useJointConfig();

  const selectedDataset = datasets?.find((dataset) => dataset.id === datasetId) ?? null;
  const totalEpisodes = episodes?.length ?? selectedDataset?.totalEpisodes ?? 0;
  const canGoPreviousEpisode = selectedEpisode > 0;
  const canGoNextEpisode = totalEpisodes > 0 && selectedEpisode < totalEpisodes - 1;

  const handlePreviousEpisode = () => {
    setSelectedEpisode(Math.max(selectedEpisode - 1, 0));
  };

  const handleNextEpisode = () => {
    if (totalEpisodes === 0) {
      return;
    }

    setSelectedEpisode(Math.min(selectedEpisode + 1, totalEpisodes - 1));
  };

  return (
    <div className="flex flex-col h-screen">
      <DataviewerShellHeader
        datasetId={datasetId}
        datasets={datasets ?? []}
        diagnosticsVisible={diagnosticsVisible}
        onSelectDataset={setDatasetId}
        onToggleDiagnostics={toggleDiagnostics}
        capabilities={capabilities}
        isWarmingCache={shellState.isWarmingCache}
      />

      <div className="flex flex-1 min-h-0">
        <aside className="w-64 border-r bg-card overflow-hidden flex flex-col">
          <DataviewerEpisodeList
            datasetId={datasetId}
            onSelectEpisode={setSelectedEpisode}
            selectedIndex={selectedEpisode}
          />
        </aside>

        <main className="flex-1 overflow-hidden bg-background">
          <DataviewerEpisodeViewer
            datasetId={datasetId}
            episodeIndex={selectedEpisode}
            diagnosticsVisible={diagnosticsVisible}
            canGoPreviousEpisode={canGoPreviousEpisode}
            onPreviousEpisode={handlePreviousEpisode}
            canGoNextEpisode={canGoNextEpisode}
            onNextEpisode={handleNextEpisode}
            onSaveAndNextEpisode={handleNextEpisode}
          />
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
