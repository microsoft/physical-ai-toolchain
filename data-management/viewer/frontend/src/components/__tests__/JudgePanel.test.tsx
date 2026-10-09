import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { DatasetWorkspace } from '@/components/app-shell/DatasetWorkspace'
import { JudgePanel } from '@/components/vlm-judge'
import { ApiClientError } from '@/lib/api-client'
import { useEpisodeStore } from '@/stores/episode-store'
import type { VlmJudgeResult, VlmJudgeStatus } from '@/types'

const mockUseStatus = vi.fn()
const mockUseCapabilities = vi.fn()
const mockMutate = vi.fn()
const mockUseRun = vi.fn()
const mockEvidence = vi.fn()
const mockApplyResult = vi.fn()
const mockSaveLabels = vi.fn()
const mockRunAll = vi.fn()
const mockApplyLabelsAll = vi.fn()
const mockCancel = vi.fn()
const mockBatch = vi.fn()
const mockReadiness = vi.fn()
const mockDatasetJobs = vi.fn()
const mockDatasetAction = vi.fn()
const mockSampleEvidence = vi.fn()
const mockPrincipal = vi.fn()
let principalScope = 'principal-one'
let applicationError: Error | null = null

vi.mock('@/hooks/use-principal-context', () => ({
  usePrincipalContext: () => mockPrincipal(),
}))

vi.mock('@/hooks/use-episode-readiness', () => ({
  useEpisodeReadiness: (...args: unknown[]) => mockReadiness(...args),
}))

vi.mock('@/hooks/use-vlm-judge', () => ({
  useVlmJudgeStatus: (...args: unknown[]) => mockUseStatus(...args),
  useRunVlmJudge: () => mockUseRun(),
  useJudgeEvidence: () => mockEvidence(),
  useApplyJudgeResult: () => ({
    mutate: mockApplyResult,
    isPending: false,
    error: applicationError,
  }),
}))

vi.mock('@/hooks/use-datasets', () => ({
  useCapabilities: (...args: unknown[]) => mockUseCapabilities(...args),
}))

vi.mock('@/hooks/use-labels', () => ({
  useSaveEpisodeLabels: () => ({ mutate: mockSaveLabels, isPending: false }),
  labelKeys: { dataset: (id: string) => ['labels', id] },
}))

vi.mock('@/hooks/use-vlm-judge-batch', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/hooks/use-vlm-judge-batch')>()
  return {
    ...actual,
    useVlmJudgeBatch: () => mockBatch(),
    useJudgeDataset: () => mockDatasetJobs(),
    useJudgeSamples: () => mockSampleEvidence(),
  }
})

vi.mock('@/stores/label-store', () => ({
  useLabelStore: Object.assign(vi.fn(), { getState: () => ({ episodeLabels: {} }) }),
}))

function status(partial: Partial<VlmJudgeStatus> = {}): VlmJudgeStatus {
  return {
    enabled: true,
    cached: false,
    judgeModel: 'Qwen/Qwen3-VL-4B-Instruct',
    promptVersion: 'test-v1',
    cacheKey: null,
    result: null,
    ...partial,
  }
}

function judgeResult(partial: Partial<VlmJudgeResult> = {}): VlmJudgeResult {
  return {
    episodeId: 'demo/episode_000000',
    instruction: 'Pick up the orange',
    judgeModel: 'Qwen/Qwen3-VL-4B-Instruct',
    promptVersion: 'test-v1',
    nFrames: 6,
    outcomeSuccess: true,
    outcomeConfidence: 0.83,
    outcomeNValidVotes: 3,
    progressPerFrame: [0, 25, 50, 75, 90, 100],
    voc: 0.92,
    milestones: [],
    failureMode: null,
    cached: false,
    ...partial,
  }
}

describe('JudgePanel', () => {
  it.each(['disabled', 'checking-access', 'access-error', 'denied'])(
    'hides batch controls while %s and keeps the return action available',
    async (state) => {
      const user = userEvent.setup()
      const onToggle = vi.fn()
      if (state === 'checking-access') mockPrincipal.mockReturnValue({ isPending: true })
      if (state === 'access-error') mockPrincipal.mockReturnValue({ error: new Error('private') })
      mockDatasetJobs.mockReturnValue({
        inventory: { isPending: true },
        jobs: {},
        approvals: {},
        reset: {},
        review: {},
        denied: state === 'denied',
      })
      mockReadiness.mockReturnValue({ ready: false, reason: 'Checking saved episode readiness.' })
      mockBatch.mockReturnValue({ submit: vi.fn() })
      render(
        <DatasetWorkspace
          datasetId="demo"
          open
          enabled={state !== 'disabled'}
          onToggle={onToggle}
        />,
      )
      const message = {
        disabled: 'VLM judge is disabled.',
        'checking-access': 'Checking dataset access...',
        'access-error': 'Dataset access could not be verified.',
        denied: 'Dataset operation access is denied.',
      }[state]
      expect(screen.getByText(message!)).toBeVisible()
      expect(screen.queryByRole('button', { name: 'Refresh dataset jobs' })).not.toBeInTheDocument()
      expect(
        screen.queryByRole('button', {
          name: /evaluate samples|judge.*targets|preview.*withdrawal|preview label removal/i,
        }),
      ).not.toBeInTheDocument()
      expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
      expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
      expect(screen.queryByText('Loading episode inventory...')).not.toBeInTheDocument()
      expect(screen.queryByText('Checking saved episode readiness.')).not.toBeInTheDocument()
      expect(screen.queryByText('0 active jobs')).not.toBeInTheDocument()
      expect(screen.queryByText('private')).not.toBeInTheDocument()
      await user.click(screen.getByRole('button', { name: 'Return to episode' }))
      expect(onToggle).toHaveBeenCalledOnce()
    },
  )

  it.each(['principal', 'source'])('clears selected targets when the %s changes', async (scope) => {
    useEpisodeStore.getState().reset()
    const user = userEvent.setup()
    mockDatasetJobs.mockReturnValue({
      inventory: { data: { items: [3], total: 1 } },
      jobs: {},
      approvals: {},
      reset: {},
      review: {},
      act: vi.fn(),
      refresh: vi.fn(),
    })
    mockBatch.mockReturnValue({ submit: vi.fn() })
    const { rerender } = render(
      <DatasetWorkspace datasetId="demo" open enabled onToggle={vi.fn()} />,
    )
    await user.click(screen.getByRole('checkbox', { name: 'Target episode 3' }))
    expect(screen.getByRole('checkbox', { name: 'Target episode 3' })).toBeChecked()
    if (scope === 'principal') principalScope = 'principal-two'
    else
      act(() =>
        useEpisodeStore.setState({
          currentEpisode: {
            sourceId: 'replacement',
            sourceRevision: 'new',
            meta: { index: 3, length: 3, taskIndex: 0, hasAnnotations: false },
            cameras: [],
            videoUrls: {},
            trajectoryData: [],
          },
        }),
      )
    rerender(<DatasetWorkspace datasetId="demo" open enabled onToggle={vi.fn()} />)
    expect(screen.getByRole('checkbox', { name: 'Target episode 3' })).not.toBeChecked()
  })
  it('reviews explicit saved human sample references before submitting a sample job', async () => {
    const user = userEvent.setup()
    const submit = vi.fn().mockResolvedValue({ id: 'sample-job' })
    mockBatch.mockReturnValue({ submit, isPending: false })
    mockDatasetJobs.mockReturnValue({
      inventory: { data: { items: [3], total: 1 } },
      jobs: {},
      approvals: {},
      reset: {},
      review: {},
      act: mockDatasetAction,
      refresh: vi.fn(),
    })
    const reference = {
      annotationAuthorId: 'reviewer',
      annotationRevision: 'saved-revision',
      snapshotId: 'snapshot',
    }
    mockSampleEvidence.mockReturnValue({
      data: [
        {
          index: 3,
          authors: ['reviewer'],
          reference,
          rating: 'success',
          instruction: 'Saved instruction',
        },
      ],
    })
    render(<DatasetWorkspace datasetId="demo" open enabled onToggle={vi.fn()} />)
    await user.click(screen.getByRole('checkbox', { name: 'Sample episode 3' }))
    expect(screen.getByRole('button', { name: 'Evaluate samples' })).toBeDisabled()
    await user.selectOptions(
      screen.getByRole('combobox', { name: 'Saved author for episode 3' }),
      'reviewer',
    )
    expect(screen.getByText('saved-revision')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Evaluate samples' }))
    expect(submit).toHaveBeenCalledWith(
      expect.objectContaining({ mode: 'sample', indices: [3], samples: { 3: reference } }),
    )
  })

  it('requires acknowledgment and confirms only the displayed reset preview', async () => {
    const user = userEvent.setup()
    const preview = {
      id: 'preview-id',
      summary: {
        removableFields: 4,
        acceptedUnchanged: 1,
        preservedHuman: 2,
        legacyUnknown: 1,
        conflicts: 0,
        episodes: 2,
        fields: [{ episodeIndex: 3, field: 'labels.SUCCESS', disposition: 'remove' }],
      },
    }
    mockDatasetAction.mockResolvedValue(preview)
    mockDatasetJobs.mockReturnValue({
      inventory: {},
      jobs: {},
      approvals: {},
      reset: {},
      review: {},
      act: mockDatasetAction,
      refresh: vi.fn(),
    })
    mockBatch.mockReturnValue({ submit: vi.fn(), isPending: false })
    render(<DatasetWorkspace datasetId="demo" open enabled onToggle={vi.fn()} />)
    await user.click(screen.getByRole('button', { name: 'Preview label removal' }))
    expect(screen.getByText('labels.SUCCESS')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Confirm label removal' })).toBeDisabled()
    await user.click(screen.getByRole('checkbox', { name: 'I reviewed this removal preview' }))
    await user.click(screen.getByRole('button', { name: 'Confirm label removal' }))
    expect(mockDatasetAction).toHaveBeenLastCalledWith({
      kind: 'confirm-reset',
      previewId: 'preview-id',
    })
  })
  it('explains a completed withdrawal conflict and offers a missing-run preview', async () => {
    const user = userEvent.setup()
    const summary = {
      removableFields: 1,
      acceptedUnchanged: 0,
      preservedHuman: 0,
      legacyUnknown: 0,
      conflicts: 1,
      episodes: 1,
      unlistedRunIds: ['lost-run'],
      fields: [
        {
          episodeIndex: 2,
          field: 'labels/SUCCESS',
          disposition: 'removable_fields',
          runIds: ['kept'],
        },
        {
          episodeIndex: 1,
          field: 'labels/SUCCESS',
          disposition: 'conflicts',
          reason: 'unlisted_machine_run',
          blockingRunIds: ['lost-run'],
        },
      ],
    }
    mockDatasetAction.mockResolvedValue({ id: 'preview-id', summary })
    mockDatasetJobs.mockReturnValue({
      inventory: {},
      jobs: {},
      approvals: {},
      reset: {
        data: {
          id: 'reset',
          status: 'conflicted',
          resources: [{ key: 'labels', status: 'succeeded' }],
          summary,
        },
      },
      review: {},
      act: mockDatasetAction,
      refresh: vi.fn(),
    })
    mockBatch.mockReturnValue({ submit: vi.fn(), isPending: false })
    render(<DatasetWorkspace datasetId="demo" open enabled onToggle={vi.fn()} />)
    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent('1 AI value(s) were removed and 1 were left unchanged')
    expect(alert).toHaveTextContent(/Episode 1, labels\/SUCCESS: Not removed: .*missing.*lost-run/)
    expect(alert).not.toHaveTextContent('Episode 2')
    await user.click(screen.getByRole('button', { name: 'Preview label removal' }))
    await user.click(screen.getByRole('button', { name: 'Preview including missing runs' }))
    expect(mockDatasetAction).toHaveBeenLastCalledWith({
      kind: 'preview-reset',
      includeUnlistedRuns: true,
    })
  })

  it('uses actual dataset targets, gates unapproved launches and cancels durable jobs', async () => {
    const user = userEvent.setup()
    mockDatasetAction.mockResolvedValue({})
    mockDatasetJobs.mockReturnValue({
      inventory: { data: { items: [3, 1005], total: 2, snapshotId: 'inventory' } },
      jobs: {
        data: {
          items: [
            {
              id: 'durable',
              mode: 'judge',
              status: 'running',
              total: 2,
              judged: 1,
              applied: 0,
              errors: 0,
              config: {},
            },
          ],
          total: 1,
        },
      },
      approvals: { data: { items: [], total: 0 } },
      reset: { data: null },
      review: {},
      act: mockDatasetAction,
      isPending: false,
      error: null,
      refresh: vi.fn(),
    })
    mockBatch.mockReturnValue({ submit: vi.fn(), isPending: false, error: null })
    render(<DatasetWorkspace datasetId="demo" open enabled onToggle={vi.fn()} />)
    await user.click(screen.getByRole('checkbox', { name: 'Target episode 1005' }))
    expect(mockReadiness).toHaveBeenCalledWith('demo', [1005], true)
    expect(screen.getByRole('button', { name: 'Judge targets' })).toBeDisabled()
    expect(screen.getByText(/1 judged.*0 applied/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Cancel job durable' }))
    expect(mockDatasetAction).toHaveBeenCalledWith({ kind: 'cancel', jobId: 'durable' })
    expect(screen.queryByRole('tab', { name: /dataset|episodes/i })).not.toBeInTheDocument()
  })
  beforeEach(() => {
    principalScope = 'principal-one'
    mockPrincipal.mockImplementation(() => ({ data: { scopeId: principalScope } }))
    mockSampleEvidence.mockReturnValue({ data: [] })
    mockUseStatus.mockReset()
    mockUseCapabilities.mockReset()
    mockMutate.mockReset()
    mockUseRun.mockReset()
    mockApplyResult.mockReset()
    applicationError = null
    mockEvidence.mockImplementation(() => ({
      data: {
        items: mockUseStatus()?.data?.result
          ? [
              {
                resultKind: 'judge',
                runId: 'canonical-run',
                resultId: 'canonical-result',
                applicability: 'current',
                input: { snapshotId: 'saved-input' },
                result: mockUseStatus().data.result,
              },
            ]
          : [],
        total: 0,
      },
      refetch: vi.fn(),
    }))
    mockSaveLabels.mockReset()
    mockRunAll.mockReset()
    mockApplyLabelsAll.mockReset()
    mockCancel.mockReset()
    mockBatch.mockReset()
    mockReadiness.mockReset()
    mockReadiness.mockReturnValue({ ready: true, reason: null })
    mockUseRun.mockReturnValue({
      mutate: mockMutate,
      isPending: false,
      error: null,
      data: undefined,
    })
    mockUseCapabilities.mockReturnValue({
      data: { vlmJudgeEnabled: true },
      isLoading: false,
    })
    mockBatch.mockReturnValue({
      progress: null,
      error: null,
      isRunning: false,
      runAll: mockRunAll,
      applyLabelsAll: mockApplyLabelsAll,
      cancel: mockCancel,
    })
  })

  it('does not display a retained mutation result outside the current status scope', () => {
    mockUseStatus.mockReturnValue({ data: status(), isLoading: false, error: null })
    mockUseRun.mockReturnValue({
      mutate: mockMutate,
      isPending: false,
      error: null,
      data: judgeResult(),
    })
    render(<JudgePanel datasetId="another" episodeIndex={3} />)
    expect(screen.queryByText('SUCCESS')).not.toBeInTheDocument()
  })

  it('shows a disabled hint when the judge backend is not enabled', () => {
    mockUseCapabilities.mockReturnValue({
      data: { vlmJudgeEnabled: false },
      isLoading: false,
    })
    mockUseStatus.mockReturnValue({
      data: undefined,
      isLoading: false,
      error: null,
    })
    render(<JudgePanel datasetId="demo" episodeIndex={0} />)
    expect(mockUseStatus).toHaveBeenCalledWith({
      datasetId: 'demo',
      episodeIndex: 0,
      enabled: false,
    })
    expect(screen.getByText(/not enabled for this server/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /run judge/i })).not.toBeInTheDocument()
  })

  it('blocks launch controls while keeping saved evidence visible', async () => {
    mockReadiness.mockReturnValue({
      ready: false,
      reason: 'Episode 0: Save label changes before judging.',
    })
    mockUseStatus.mockReturnValue({
      data: status({ result: judgeResult() }),
      isLoading: false,
      error: null,
    })
    render(<JudgePanel datasetId="demo" episodeIndex={0} totalEpisodes={2} />)
    expect(screen.getByRole('button', { name: /force fresh/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /re-evaluate/i })).toBeDisabled()
    expect(screen.getByText('SUCCESS')).toBeVisible()
    expect(screen.getAllByText(/Episode 0: Save label changes/)[0]).toBeVisible()
    expect(mockMutate).not.toHaveBeenCalled()
  })

  it('renders Run button and prompts to run when no result exists yet', async () => {
    const user = userEvent.setup()
    mockUseStatus.mockReturnValue({ data: status(), isLoading: false, error: null })
    render(<JudgePanel datasetId="demo" episodeIndex={2} />)
    expect(screen.getByText(/no judgment yet/i)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /run judge/i }))
    expect(mockMutate).toHaveBeenCalledWith({
      datasetId: 'demo',
      episodeIndex: 2,
      options: { processMethod: 'gvl', force: false },
    })
  })

  it('keeps an explicit judge camera selection for single requests', async () => {
    const user = userEvent.setup()
    mockUseStatus.mockReturnValue({ data: status(), isLoading: false, error: null })
    const { rerender } = render(
      <JudgePanel
        datasetId="demo"
        episodeIndex={0}
        cameras={['front', 'wrist']}
        totalEpisodes={2}
      />,
    )
    await user.click(screen.getByRole('checkbox', { name: 'front' }))
    expect(screen.getByRole('checkbox', { name: 'wrist' })).toBeDisabled()
    rerender(
      <JudgePanel
        datasetId="demo"
        episodeIndex={1}
        cameras={['front', 'wrist']}
        totalEpisodes={2}
      />,
    )
    await user.click(screen.getByRole('button', { name: /run judge/i }))
    expect(mockMutate).toHaveBeenCalledWith({
      datasetId: 'demo',
      episodeIndex: 1,
      options: { processMethod: 'gvl', force: false, views: ['wrist'] },
    })
    rerender(<JudgePanel datasetId="demo" episodeIndex={1} cameras={['side']} totalEpisodes={2} />)
    expect(screen.getByRole('button', { name: /run judge/i })).toBeDisabled()
    expect(screen.queryByRole('button', { name: /run all/i })).not.toBeInTheDocument()
    expect(screen.getByText('Choose a judge camera.')).toBeVisible()
    await user.click(screen.getByRole('checkbox', { name: 'side' }))
    expect(screen.getByRole('button', { name: /run judge/i })).toBeEnabled()
  })

  it('renders the cached SUCCESS result and exposes Force fresh', async () => {
    const user = userEvent.setup()
    const result = judgeResult({ cached: true })
    mockUseStatus.mockReturnValue({
      data: status({ cached: true, result }),
      isLoading: false,
      error: null,
    })
    render(<JudgePanel datasetId="demo" episodeIndex={1} />)
    expect(screen.getByText('SUCCESS')).toBeInTheDocument()
    expect(screen.getByText(/cached/i)).toBeInTheDocument()
    expect(screen.getByText(/voc 0\.92/i)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /force fresh/i }))
    expect(mockMutate).toHaveBeenCalledWith({
      datasetId: 'demo',
      episodeIndex: 1,
      options: { processMethod: 'gvl', force: true },
    })
  })

  it('shows unavailable process reward text when every progress value is zero', () => {
    const result = judgeResult({ progressPerFrame: [0, 0, 0, 0, 0, 0], voc: 1 })
    mockUseStatus.mockReturnValue({ data: status({ result }), isLoading: false, error: null })

    render(<JudgePanel datasetId="demo" episodeIndex={1} />)

    expect(screen.getByText(/process reward unavailable/i)).toBeInTheDocument()
    expect(screen.queryByLabelText(/per-frame task-completion progress/i)).not.toBeInTheDocument()
    expect(screen.getByText(/voc 1\.00/i)).toBeInTheDocument()
  })

  it('exposes the scoring technique and sends the configured method on run', async () => {
    const user = userEvent.setup()
    mockUseStatus.mockReturnValue({
      data: status({
        processMethod: 'chronological',
        processMethods: ['gvl', 'chronological'],
        backend: 'qwen3-vl',
        nFrames: 12,
      }),
      isLoading: false,
      error: null,
    })
    render(<JudgePanel datasetId="demo" episodeIndex={0} />)
    expect(screen.getByText(/scoring technique/i)).toBeInTheDocument()
    expect(screen.getByText(/backend: qwen3-vl/i)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /run judge/i }))
    expect(mockMutate).toHaveBeenCalledWith({
      datasetId: 'demo',
      episodeIndex: 0,
      options: { processMethod: 'chronological', force: false },
    })
  })

  it('renders failure milestones when the judge produced them', () => {
    const result = judgeResult({
      outcomeSuccess: false,
      outcomeConfidence: 0.67,
      failureMode: 'missed_grasp',
      milestones: [
        { name: 'approach_object', completed: true, frameRange: '0-3', evidence: 'extends arm' },
        { name: 'grasp_object', completed: false, frameRange: '3-5', evidence: 'closes on air' },
      ],
    })
    mockUseStatus.mockReturnValue({ data: status({ result }), isLoading: false, error: null })
    render(<JudgePanel datasetId="demo" episodeIndex={1} />)
    expect(screen.getByText('FAILURE')).toBeInTheDocument()
    expect(screen.getByText(/missed grasp/i)).toBeInTheDocument()
    expect(screen.getByText(/approach object/i)).toBeInTheDocument()
    expect(screen.getByText(/grasp object/i)).toBeInTheDocument()
  })

  it('surfaces mutation errors', () => {
    mockUseRun.mockReturnValue({
      mutate: mockMutate,
      isPending: false,
      error: new Error('VLM backend error: timeout'),
      data: undefined,
    })
    mockUseStatus.mockReturnValue({ data: status(), isLoading: false, error: null })
    render(<JudgePanel datasetId="demo" episodeIndex={0} />)
    expect(screen.getByText(/timeout/i)).toBeInTheDocument()
  })

  it('explains how to recover when no task instruction is available', () => {
    mockUseRun.mockReturnValue({
      mutate: mockMutate,
      isPending: false,
      error: new Error('No task instruction available; provide one via the request body'),
      data: undefined,
    })
    mockUseStatus.mockReturnValue({ data: status(), isLoading: false, error: null })
    render(<JudgePanel datasetId="demo" episodeIndex={0} />)
    expect(screen.getByText(/add a language instruction/i)).toBeInTheDocument()
  })

  it('shows an in-progress bar and a Running label while a judge run is pending', () => {
    mockUseRun.mockReturnValue({
      mutate: mockMutate,
      isPending: true,
      error: null,
      data: undefined,
    })
    mockUseStatus.mockReturnValue({ data: status(), isLoading: false, error: null })
    render(<JudgePanel datasetId="demo" episodeIndex={0} />)
    expect(screen.getByRole('progressbar', { name: /running judge/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /running/i })).toBeDisabled()
    expect(screen.queryByText(/no judgment yet/i)).not.toBeInTheDocument()
  })

  it('requests canonical application without relabeling machine output as human', async () => {
    const user = userEvent.setup()
    const result = judgeResult({ outcomeSuccess: true })
    mockUseStatus.mockReturnValue({ data: status({ result }), isLoading: false, error: null })
    render(<JudgePanel datasetId="demo" episodeIndex={4} />)
    await user.click(screen.getByRole('button', { name: /apply label/i }))
    expect(mockApplyResult).toHaveBeenCalledWith({
      datasetId: 'demo',
      episodeIndex: 4,
      runId: 'canonical-run',
    })
    expect(mockSaveLabels).not.toHaveBeenCalled()
  })

  it.each([
    [new ApiClientError('invalid', 'HTTP_422', 422), /rejected .* as invalid.*will not resolve/i],
    [new ApiClientError('conflict', 'HTTP_409', 409), /conflicts with newer saved inputs/i],
  ])('distinguishes application failure %#', (error, message) => {
    applicationError = error
    mockUseStatus.mockReturnValue({
      data: status({ result: judgeResult() }),
      isLoading: false,
      error: null,
    })
    render(<JudgePanel datasetId="demo" episodeIndex={0} />)
    expect(screen.getByRole('alert')).toHaveTextContent(message)
  })

  it('retains marked withdrawn history without resurrecting a cached status result', () => {
    mockUseStatus.mockReturnValue({ data: status({ result: judgeResult() }) })
    mockEvidence.mockReturnValue({
      data: {
        items: [
          {
            resultKind: 'judge',
            runId: 'old-run',
            applicability: 'withdrawn',
            input: { snapshotId: 'old-input' },
            result: judgeResult(),
          },
        ],
        total: 1,
      },
      refetch: vi.fn(),
    })
    render(<JudgePanel datasetId="demo" episodeIndex={0} />)
    expect(screen.getByText(/withdrawn/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /apply label/i })).not.toBeInTheDocument()
  })

  it('leaves approval-gated dataset operations to the dataset workspace', () => {
    mockUseStatus.mockReturnValue({ data: status(), isLoading: false, error: null })
    render(<JudgePanel datasetId="demo" episodeIndex={0} totalEpisodes={3} />)
    expect(screen.queryByRole('button', { name: /run all|label all/i })).not.toBeInTheDocument()
    expect(mockReadiness).toHaveBeenCalledWith('demo', [0], true)
    expect(mockBatch).not.toHaveBeenCalled()
  })

  it('does not present browser-owned batch progress inside an episode', () => {
    mockBatch.mockReturnValue({
      progress: { phase: 'judging', done: 1, total: 3 },
      error: null,
      isRunning: true,
      runAll: mockRunAll,
      applyLabelsAll: mockApplyLabelsAll,
      cancel: mockCancel,
    })
    mockUseStatus.mockReturnValue({ data: status(), isLoading: false, error: null })
    render(<JudgePanel datasetId="demo" episodeIndex={0} totalEpisodes={3} />)
    expect(screen.queryByText('1 / 3')).not.toBeInTheDocument()
    expect(mockBatch).not.toHaveBeenCalled()
  })
  it('explains specialized judge terminology at the point of use', () => {
    mockUseStatus.mockReturnValue({ data: status(), isLoading: false, error: null })
    render(<JudgePanel datasetId="demo" episodeIndex={0} />)

    expect(screen.getByText(/VLM \(vision-language model\)/i)).toBeInTheDocument()
    expect(screen.getAllByText(/GVL.*shuffle-and-rank/i).length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText(/VOC.*value-order correlation/i)).toBeInTheDocument()
  })
})
