import { QueryClientProvider, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'

import { DataviewerEpisodeList } from '@/components/app-shell/DataviewerEpisodeList'
import { DataviewerEpisodeViewer } from '@/components/app-shell/DataviewerEpisodeViewer'
import { DataviewerShellHeader } from '@/components/app-shell/DataviewerShellHeader'
import { OperatorWorkspace } from '@/components/operator'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { TooltipProvider } from '@/components/ui/tooltip'
import { datasetKeys, useCapabilities, useDatasets, useEpisodes } from '@/hooks/use-datasets'
import { useDataviewerShellState } from '@/hooks/use-dataviewer-shell-state'
import { useJointConfig } from '@/hooks/use-joint-config'
import { useDatasetLabels } from '@/hooks/use-labels'
import { useOperator } from '@/hooks/use-operator'
import { queryClient } from '@/lib/query-client'

export function AppContent() {
  const activeQueryClient = useQueryClient()
  const { data: datasets } = useDatasets()
  const operator = useOperator()
  const [workspace, setWorkspace] = useState<'analysis' | 'operator'>('analysis')
  const handledRecordingRef = useRef<string | null>(null)
  const shellState = useDataviewerShellState({ datasets })
  const datasetId = shellState.datasetId
  const diagnosticsVisible = shellState.diagnosticsVisible
  const selectedEpisode = shellState.selectedEpisode
  const setDatasetId = shellState.setDatasetId
  const setSelectedEpisode = shellState.setSelectedEpisode
  const toggleDiagnostics = shellState.toggleDiagnostics
  const { data: capabilities } = useCapabilities(datasetId || undefined)
  const { data: episodes } = useEpisodes(datasetId, { limit: 1000 })
  const operatorEnabled = operator.capabilities?.enabled === true
  const visibleWorkspace = operatorEnabled ? workspace : 'analysis'

  useEffect(() => {
    const status = operator.status
    if (status?.state !== 'completed' || status.mode !== 'record' || !status.sessionId) {
      return
    }

    const recordingId = `${status.serviceInstanceId}:${status.sessionId}`
    if (handledRecordingRef.current === recordingId) {
      return
    }

    handledRecordingRef.current = recordingId
    void activeQueryClient.invalidateQueries({ queryKey: datasetKeys.list() })
  }, [activeQueryClient, operator.status])

  // Load labels for the selected dataset
  useDatasetLabels()

  // Load joint configuration for the selected dataset
  useJointConfig()

  const selectedDataset = datasets?.find((dataset) => dataset.id === datasetId) ?? null
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
      {operatorEnabled && (
        <nav className="bg-card flex justify-center border-b px-4 py-1.5" aria-label="Workspace">
          <Tabs
            value={visibleWorkspace}
            onValueChange={(value) => setWorkspace(value as 'analysis' | 'operator')}
          >
            <TabsList>
              <TabsTrigger value="analysis">Analysis</TabsTrigger>
              <TabsTrigger value="operator">Live operator</TabsTrigger>
            </TabsList>
          </Tabs>
        </nav>
      )}

      {visibleWorkspace === 'operator' ? (
        <div className="min-h-0 flex-1">
          <OperatorWorkspace operator={operator} />
        </div>
      ) : (
        <>
          <DataviewerShellHeader
            datasetId={datasetId}
            datasets={datasets ?? []}
            diagnosticsVisible={diagnosticsVisible}
            onSelectDataset={setDatasetId}
            onToggleDiagnostics={toggleDiagnostics}
            capabilities={capabilities}
            isWarmingCache={shellState.isWarmingCache}
          />

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
              />
            </main>
          </div>
        </>
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
