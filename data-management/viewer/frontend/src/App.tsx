import { QueryClientProvider } from '@tanstack/react-query'
import { useEffect, useState } from 'react'

import { DatasetCatalog } from '@/components/app-shell/DatasetCatalog'
import { DatasetWorkspace } from '@/components/app-shell/DatasetWorkspace'
import { DataviewerEpisodeList } from '@/components/app-shell/DataviewerEpisodeList'
import { DataviewerEpisodeViewer } from '@/components/app-shell/DataviewerEpisodeViewer'
import { DataviewerShellHeader } from '@/components/app-shell/DataviewerShellHeader'
import { TooltipProvider } from '@/components/ui/tooltip'
import { useCapabilities, useDataset, useEpisodes } from '@/hooks/use-datasets'
import { useDataviewerShellState } from '@/hooks/use-dataviewer-shell-state'
import { useJointConfig } from '@/hooks/use-joint-config'
import { KeyboardShortcutsContext } from '@/hooks/use-keyboard-shortcuts'
import { useDatasetLabels } from '@/hooks/use-labels'
import { queryClient } from '@/lib/query-client'
import { useDatasetStore, useEpisodeStore } from '@/stores'

export function AppContent() {
  const shellState = useDataviewerShellState({})
  const datasetId = shellState.datasetId
  const [catalogOpen, setCatalogOpen] = useState(!datasetId)
  const [workspaceOpen, setWorkspaceOpen] = useState(false)
  const toggleWorkspace = () => {
    const playback = useEpisodeStore.getState()
    if (playback.isPlaying) playback.togglePlayback()
    document.querySelectorAll('video').forEach((video) => video.pause())
    setWorkspaceOpen((open) => !open)
    document.getElementById('dataset-workspace-toggle')?.focus()
  }
  const openCatalog = () => {
    const playback = useEpisodeStore.getState()
    if (playback.isPlaying) playback.togglePlayback()
    document.querySelectorAll('video').forEach((video) => video.pause())
    setCatalogOpen(true)
  }
  const { data: selectedDataset } = useDataset(datasetId || undefined)
  useEffect(() => {
    const store = useDatasetStore.getState()
    store.setDatasets(selectedDataset ? [selectedDataset] : [])
    if (selectedDataset) store.selectDataset(selectedDataset.id)
    else store.clearSelection()
  }, [selectedDataset])
  const diagnosticsVisible = shellState.diagnosticsVisible
  const selectedEpisode = shellState.selectedEpisode
  const setDatasetId = (id: string) => {
    if (shellState.setDatasetId(id) !== false) {
      setCatalogOpen(false)
      if (id !== datasetId) setWorkspaceOpen(false)
    }
  }
  const setSelectedEpisode = shellState.setSelectedEpisode
  const toggleDiagnostics = shellState.toggleDiagnostics
  const { data: capabilities } = useCapabilities(datasetId || undefined)
  const { data: episodes } = useEpisodes(datasetId, { limit: 1000 })

  // Load labels for the selected dataset
  useDatasetLabels()

  // Load joint configuration for the selected dataset
  useJointConfig()

  const totalEpisodes = episodes?.length ?? selectedDataset?.totalEpisodes ?? 0
  const canGoPreviousEpisode = selectedEpisode > 0
  const canGoNextEpisode = totalEpisodes > 0 && selectedEpisode < totalEpisodes - 1

  const handlePreviousEpisode = () => {
    setSelectedEpisode(Math.max(selectedEpisode - 1, 0))
  }

  const handleNextEpisode = () => {
    if (totalEpisodes === 0) {
      return
    }

    setSelectedEpisode(Math.min(selectedEpisode + 1, totalEpisodes - 1))
  }

  return (
    <div className="flex h-screen flex-col">
      <DataviewerShellHeader
        datasetId={datasetId}
        datasets={selectedDataset ? [selectedDataset] : []}
        onOpenCatalog={openCatalog}
        catalogOpen={catalogOpen || !datasetId}
        diagnosticsVisible={diagnosticsVisible}
        onSelectDataset={setDatasetId}
        onToggleDiagnostics={toggleDiagnostics}
        capabilities={capabilities}
        isWarmingCache={shellState.isWarmingCache}
      />

      <DatasetCatalog
        visible={catalogOpen || !datasetId}
        selectedId={datasetId}
        onSelect={setDatasetId}
        onReturn={() => {
          setCatalogOpen(false)
          document.getElementById('dataset-selector')?.focus()
        }}
      />
      {datasetId && (
        <div hidden={catalogOpen} className={catalogOpen ? 'hidden' : 'min-h-0 overflow-y-auto'}>
          <DatasetWorkspace
            key={datasetId}
            datasetId={datasetId}
            open={workspaceOpen}
            enabled={!!capabilities?.vlmJudgeEnabled}
            onToggle={toggleWorkspace}
          />
        </div>
      )}
      {datasetId && (
        <KeyboardShortcutsContext.Provider value={!catalogOpen && !workspaceOpen}>
          <div
            hidden={catalogOpen || workspaceOpen}
            inert={catalogOpen || workspaceOpen}
            className={
              catalogOpen || workspaceOpen ? 'hidden' : 'flex min-h-0 flex-1 flex-col sm:flex-row'
            }
          >
            <aside className="bg-card flex max-h-64 w-full flex-col overflow-hidden border-b sm:max-h-none sm:w-64 sm:border-r sm:border-b-0">
              <DataviewerEpisodeList
                datasetId={datasetId}
                onSelectEpisode={setSelectedEpisode}
                selectedIndex={selectedEpisode}
              />
            </aside>

            <main className="bg-background min-w-0 flex-1 overflow-hidden">
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
        </KeyboardShortcutsContext.Provider>
      )}
    </div>
  )
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <AppContent />
      </TooltipProvider>
    </QueryClientProvider>
  )
}

export default App
