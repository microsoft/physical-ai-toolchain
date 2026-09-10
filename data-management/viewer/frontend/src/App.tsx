import { QueryClientProvider } from '@tanstack/react-query'
import { useEffect, useState } from 'react'

import { DataviewerEpisodeList } from '@/components/app-shell/DataviewerEpisodeList'
import { DataviewerEpisodeViewer } from '@/components/app-shell/DataviewerEpisodeViewer'
import { DataviewerShellHeader } from '@/components/app-shell/DataviewerShellHeader'
import { OperatorWorkspace } from '@/components/operator'
import { TooltipProvider } from '@/components/ui/tooltip'
import { useCapabilities, useDatasets, useEpisodes } from '@/hooks/use-datasets'
import { useDataviewerShellState } from '@/hooks/use-dataviewer-shell-state'
import { useJointConfig } from '@/hooks/use-joint-config'
import { useDatasetLabels } from '@/hooks/use-labels'
import { useOperator } from '@/hooks/use-operator'
import { queryClient } from '@/lib/query-client'

export function AppContent() {
  const [activeMode, setActiveMode] = useState<'analyze' | 'operate'>('analyze')
  const [theme, setTheme] = useState<'light' | 'dark'>(() => {
    const stored = localStorage.getItem('dataviewer-theme')
    if (stored === 'light' || stored === 'dark') return stored
    return globalThis.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  })
  const { data: datasets } = useDatasets()
  const operator = useOperator()
  const shellState = useDataviewerShellState({ datasets })
  const datasetId = shellState.datasetId
  const diagnosticsVisible = shellState.diagnosticsVisible
  const selectedEpisode = shellState.selectedEpisode
  const setDatasetId = shellState.setDatasetId
  const setSelectedEpisode = shellState.setSelectedEpisode
  const toggleDiagnostics = shellState.toggleDiagnostics
  const { data: capabilities } = useCapabilities(datasetId || undefined)
  const { data: episodes } = useEpisodes(datasetId, { limit: 1000 })

  // Load labels for the selected dataset
  useDatasetLabels()

  // Load joint configuration for the selected dataset
  useJointConfig()

  const selectedDataset = datasets?.find((dataset) => dataset.id === datasetId) ?? null
  const totalEpisodes = episodes?.length ?? selectedDataset?.totalEpisodes ?? 0
  const canGoPreviousEpisode = selectedEpisode > 0
  const canGoNextEpisode = totalEpisodes > 0 && selectedEpisode < totalEpisodes - 1

  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark')
    document.documentElement.style.colorScheme = theme
    localStorage.setItem('dataviewer-theme', theme)
  }, [theme])

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
        activeMode={activeMode}
        datasetId={datasetId}
        datasets={datasets ?? []}
        diagnosticsVisible={diagnosticsVisible}
        onSelectDataset={setDatasetId}
        onModeChange={setActiveMode}
        onStopSession={() => void operator.stopSession()}
        onToggleTheme={() => setTheme((current) => (current === 'light' ? 'dark' : 'light'))}
        onToggleDiagnostics={toggleDiagnostics}
        operatorStatus={operator.status}
        theme={theme}
        capabilities={capabilities}
        isWarmingCache={shellState.isWarmingCache}
      />

      {activeMode === 'analyze' ? (
        <div className="flex min-h-0 flex-1">
          <aside className="bg-card flex w-64 flex-col overflow-hidden border-r">
            <DataviewerEpisodeList
              datasetId={datasetId}
              onSelectEpisode={setSelectedEpisode}
              selectedIndex={selectedEpisode}
            />
          </aside>

          <main className="bg-background flex-1 overflow-hidden">
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
      ) : (
        <main className="min-h-0 flex-1 overflow-hidden">
          <OperatorWorkspace operator={operator} />
        </main>
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
