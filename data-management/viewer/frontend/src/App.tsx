import { QueryClientProvider } from '@tanstack/react-query'
import { useEffect, useId, useState } from 'react'

import { DatasetCatalog } from '@/components/app-shell/DatasetCatalog'
import { DatasetWorkspace } from '@/components/app-shell/DatasetWorkspace'
import { DataviewerEpisodeList } from '@/components/app-shell/DataviewerEpisodeList'
import { DataviewerEpisodeViewer } from '@/components/app-shell/DataviewerEpisodeViewer'
import { DataviewerShellHeader } from '@/components/app-shell/DataviewerShellHeader'
import { ReleaseWorkspace } from '@/components/release/ReleaseWorkspace'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { TooltipProvider } from '@/components/ui/tooltip'
import { useCapabilities, useDataset, useEpisodes } from '@/hooks/use-datasets'
import { useDataviewerShellState } from '@/hooks/use-dataviewer-shell-state'
import { useJointConfig } from '@/hooks/use-joint-config'
import { KeyboardShortcutsContext } from '@/hooks/use-keyboard-shortcuts'
import { useDatasetLabels } from '@/hooks/use-labels'
import { queryClient } from '@/lib/query-client'
import { useDatasetStore, useEpisodeStore } from '@/stores'

type SourceTab = 'episodes' | 'releases'

export function AppContent() {
  const shellState = useDataviewerShellState({})
  const datasetId = shellState.datasetId
  const [catalogOpen, setCatalogOpen] = useState(!datasetId)
  const [workspaceOpen, setWorkspaceOpen] = useState(false)
  const [sourceTab, setSourceTab] = useState<SourceTab>('episodes')
  const sourceTabsId = useId()
  const switchSourceTab = (tab: SourceTab) => {
    if (tab === sourceTab) return
    const playback = useEpisodeStore.getState()
    if (playback.isPlaying) playback.togglePlayback()
    document.querySelectorAll('video').forEach((video) => video.pause())
    setSourceTab(tab)
  }
  const toggleWorkspace = () => {
    const playback = useEpisodeStore.getState()
    if (playback.isPlaying) playback.togglePlayback()
    document.querySelectorAll('video').forEach((video) => video.pause())
    if (sourceTab === 'releases') {
      setSourceTab('episodes')
      setWorkspaceOpen(true)
    } else {
      setWorkspaceOpen((open) => !open)
    }
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
      if (id !== datasetId) {
        setWorkspaceOpen(false)
        setSourceTab('episodes')
      }
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

  const episodesHidden = catalogOpen || workspaceOpen || sourceTab === 'releases'
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

      {datasetId && !catalogOpen && (
        <Tabs
          value={sourceTab}
          onValueChange={(value) => switchSourceTab(value as SourceTab)}
          className="border-b px-4 pt-2"
          role="navigation"
          aria-label="Source navigation"
        >
          <TabsList aria-label="Source views" className="text-foreground">
            <TabsTrigger
              value="episodes"
              id={`${sourceTabsId}-episodes-tab`}
              aria-controls={`${sourceTabsId}-episodes-panel`}
            >
              Episodes
            </TabsTrigger>
            <TabsTrigger
              value="releases"
              id={`${sourceTabsId}-releases-tab`}
              aria-controls={`${sourceTabsId}-releases-panel`}
            >
              Releases
            </TabsTrigger>
          </TabsList>
        </Tabs>
      )}

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
            open={workspaceOpen && !catalogOpen && sourceTab === 'episodes'}
            enabled={!!capabilities?.vlmJudgeEnabled}
            onToggle={toggleWorkspace}
          />
        </div>
      )}
      {datasetId && (
        <main
          hidden={catalogOpen || sourceTab !== 'releases'}
          className={
            catalogOpen || sourceTab !== 'releases' ? 'hidden' : 'min-h-0 flex-1 overflow-y-auto'
          }
        >
          <div
            role="tabpanel"
            id={`${sourceTabsId}-releases-panel`}
            aria-labelledby={`${sourceTabsId}-releases-tab`}
          >
            {!catalogOpen && sourceTab === 'releases' && <ReleaseWorkspace datasetId={datasetId} />}
          </div>
        </main>
      )}
      {datasetId && (
        <KeyboardShortcutsContext.Provider value={!episodesHidden}>
          <div
            hidden={episodesHidden}
            inert={episodesHidden}
            className={episodesHidden ? 'hidden' : 'flex min-h-0 flex-1 flex-col sm:flex-row'}
          >
            <aside className="bg-card flex max-h-64 w-full flex-col overflow-hidden border-b sm:max-h-none sm:w-64 sm:border-r sm:border-b-0">
              <DataviewerEpisodeList
                datasetId={datasetId}
                onSelectEpisode={setSelectedEpisode}
                selectedIndex={selectedEpisode}
              />
            </aside>

            <main className="bg-background min-w-0 flex-1 overflow-hidden">
              <div
                role="tabpanel"
                id={`${sourceTabsId}-episodes-panel`}
                aria-labelledby={`${sourceTabsId}-episodes-tab`}
                className="h-full"
              >
                <DataviewerEpisodeViewer
                  visible={!episodesHidden}
                  datasetId={datasetId}
                  episodeIndex={selectedEpisode}
                  diagnosticsVisible={diagnosticsVisible}
                  canGoPreviousEpisode={canGoPreviousEpisode}
                  onPreviousEpisode={handlePreviousEpisode}
                  canGoNextEpisode={canGoNextEpisode}
                  onNextEpisode={handleNextEpisode}
                  onSaveAndNextEpisode={handleNextEpisode}
                />
              </div>
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
