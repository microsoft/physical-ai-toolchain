import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'

import { DraftConflictDialog } from '@/components/annotation-workspace/DraftConflictDialog'
import { annotationKeys } from '@/hooks/use-annotations'
import { useAnnotationStore, useDatasetStore, useEpisodeStore } from '@/stores'
import { useLabelStore } from '@/stores/label-store'
import { installFetchMock, jsonResponse, mockFetch } from '@/test-utils/fetch-mocks'
import { createTestQueryClient, renderWithQuery } from '@/test-utils/render'
import type { EpisodeAnnotation } from '@/types'

function annotation(notes: string, score = 3): EpisodeAnnotation {
  return {
    annotatorId: 'principal-one',
    timestamp: '2026-09-18T00:00:00Z',
    taskCompleteness: { rating: 'success', confidence: 3 },
    trajectoryQuality: {
      overallScore: score,
      metrics: { smoothness: 3, efficiency: 3, safety: 3, precision: 3 },
      flags: [],
    },
    dataQuality: { overallQuality: 'good', issues: [] },
    anomalies: { anomalies: [] },
    notes,
  } as EpisodeAnnotation
}

describe('DraftConflictDialog', () => {
  beforeEach(() => {
    installFetchMock({ csrf: false })
    useDatasetStore.getState().reset()
    useEpisodeStore.getState().reset()
    useAnnotationStore.getState().clear()
    useLabelStore.getState().reset()
    const dataset = {
      id: 'ds-1',
      name: 'Dataset',
      totalEpisodes: 1,
      fps: 30,
      features: {},
      tasks: [],
    }
    useDatasetStore.getState().setDatasets([dataset])
    useDatasetStore.getState().selectDataset('ds-1')
    useEpisodeStore.setState({ currentDatasetId: 'ds-1', currentIndex: 0 })
  })

  it('rebases non-overlapping server changes while preferring local overlaps', async () => {
    const baseline = annotation('base', 3)
    const local = annotation('local', 3)
    const server = annotation('server', 5)
    useAnnotationStore.getState().loadAnnotation(baseline)
    useAnnotationStore.getState().resolveConflict(local, baseline, '"one"')
    useAnnotationStore.getState().setConflict('"two"', local)
    mockFetch
      .mockResolvedValueOnce(jsonResponse({ scope_id: 'principal-one', auth_mode: 'local' }))
      .mockResolvedValueOnce(
        jsonResponse({ annotations: [server] }, { headers: { ETag: '"two"' } }),
      )

    const queryClient = createTestQueryClient()
    queryClient.setDefaultOptions({
      queries: { retry: false, gcTime: Number.POSITIVE_INFINITY },
      mutations: { retry: false },
    })
    renderWithQuery(<DraftConflictDialog />, queryClient)
    await userEvent.click(screen.getByRole('button', { name: 'Prefer local conflicts' }))

    await waitFor(() => expect(useAnnotationStore.getState().conflict).toBeNull())
    expect(useAnnotationStore.getState().currentAnnotation?.notes).toBe('local')
    expect(useAnnotationStore.getState().currentAnnotation?.trajectoryQuality.overallScore).toBe(5)
    expect(useAnnotationStore.getState().isDirty).toBe(true)
    expect(useAnnotationStore.getState().baseEtag).toBe('"two"')
    expect(
      queryClient.getQueryData(annotationKeys.detail('ds-1', 0, 'principal-one')),
    ).toMatchObject({ etag: '"two"' })
  })

  it('retains a source conflict while the current source is unavailable', async () => {
    useLabelStore.setState({
      datasetId: 'ds-1',
      principalScopeId: 'principal-one',
      episodeLabels: { 0: ['FAILURE'] },
      savedEpisodeLabels: {},
      conflict: {
        currentEtag: 'one',
        episodeIndex: 0,
        submittedLabels: ['FAILURE'],
        sourceChanged: true,
      },
    })
    mockFetch
      .mockResolvedValueOnce(jsonResponse({ scope_id: 'principal-one', auth_mode: 'local' }))
      .mockResolvedValueOnce(
        jsonResponse(
          { dataset_id: 'ds-1', episodes: {}, available_labels: [] },
          { headers: { ETag: 'two' } },
        ),
      )
    renderWithQuery(<DraftConflictDialog />)
    await userEvent.click(screen.getByRole('button', { name: 'Prefer local conflicts' }))
    expect(await screen.findByText(/current source is unavailable/i)).toBeVisible()
    expect(useLabelStore.getState().conflict?.sourceChanged).toBe(true)
  })

  it.each(['server', 'local'] as const)(
    'explicitly resolves source-changed labels using %s',
    async (resolution) => {
      const source = { sourceId: 'source-a', sourceRevision: 'new' }
      useEpisodeStore.setState({
        currentEpisode: {
          ...source,
          meta: { index: 0, length: 10, taskIndex: 0, hasAnnotations: false },
          cameras: [],
          videoUrls: {},
          trajectoryData: [],
        },
      })
      useLabelStore.setState({
        datasetId: 'ds-1',
        principalScopeId: 'principal-one',
        availableLabels: ['SUCCESS', 'FAILURE'],
        episodeLabels: { 0: ['FAILURE'] },
        savedEpisodeLabels: { 0: ['SUCCESS'] },
        sourceScopes: { 0: { ...source, sourceRevision: 'old' } },
        conflict: {
          currentEtag: 'one',
          episodeIndex: 0,
          submittedLabels: ['FAILURE'],
          sourceChanged: true,
        },
      })
      mockFetch
        .mockResolvedValueOnce(jsonResponse({ scope_id: 'principal-one', auth_mode: 'local' }))
        .mockResolvedValueOnce(
          jsonResponse(
            {
              dataset_id: 'ds-1',
              available_labels: ['SUCCESS', 'FAILURE'],
              episodes: { '0': ['SUCCESS'] },
            },
            { headers: { ETag: 'two' } },
          ),
        )
      renderWithQuery(<DraftConflictDialog />)
      await userEvent.click(screen.getByRole('button', { name: `Prefer ${resolution} conflicts` }))
      await waitFor(() => expect(useLabelStore.getState().conflict).toBeNull())
      expect(useLabelStore.getState().episodeLabels[0]).toEqual(
        resolution === 'server' ? ['SUCCESS'] : ['FAILURE'],
      )
      expect(useLabelStore.getState().sourceScopes[0]).toEqual(source)
    },
  )
})
