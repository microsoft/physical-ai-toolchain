import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { DatasetWorkspace } from '@/components/app-shell/DatasetWorkspace'
import { JudgePanel } from '@/components/vlm-judge'
import { ApiClientError } from '@/lib/api-client'
import { formatEpisodeList, parseEpisodeList } from '@/lib/episode-list'
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
    useJudgeDataset: () => ({ annotated: {}, usable: {}, ...mockDatasetJobs() }),
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
  it('describes every withdrawal disposition before and after removal', async () => {
    const user = userEvent.setup()
    const fields = [
      { episodeIndex: 0, field: 'labels/AI', disposition: 'removable_fields', runIds: ['run-one'] },
      { episodeIndex: 1, field: 'labels/HUMAN', disposition: 'preserved_human' },
      { episodeIndex: 2, field: 'labels/LEGACY', disposition: 'legacy_unknown' },
      {
        episodeIndex: 3,
        field: 'labels/RESTORED',
        disposition: 'legacy_unknown',
        reason: 'legacy_value_restored',
        runIds: ['old-run'],
      },
      {
        episodeIndex: 4,
        field: 'labels/OWNERS',
        disposition: 'conflicts',
        reason: 'multiple_human_authors',
        humanAuthors: 2,
      },
      { episodeIndex: 5, field: 'labels/UNKNOWN', disposition: 'conflicts' },
    ]
    const summary = {
      fields,
      removableFields: 1,
      preservedHuman: 1,
      legacyUnknown: 2,
      conflicts: 2,
      episodes: 6,
      acceptedUnchanged: 0,
    }
    mockDatasetAction.mockResolvedValue({ id: 'preview', summary })
    const workspace = {
      inventory: {},
      jobs: {},
      approvals: {},
      review: {},
      reset: { data: { id: 'reset', status: 'succeeded', summary } },
      act: mockDatasetAction,
      refresh: vi.fn(),
    }
    mockDatasetJobs.mockReturnValue(workspace)
    mockBatch.mockReturnValue({ submit: vi.fn(), isPending: false })
    render(<DatasetWorkspace datasetId="demo" open enabled onToggle={vi.fn()} />)
    expect(screen.getByText(/Removed: AI value.*run-one/)).toBeVisible()
    expect(screen.getByText(/Kept: a person edited/)).toBeVisible()
    expect(screen.getByText(/Kept: origin unknown/)).toBeVisible()
    expect(screen.getByText(/value existed before AI labeling.*old-run/)).toBeVisible()
    expect(screen.getByText(/2 people edited this value/)).toBeVisible()
    expect(screen.getByText(/ownership could not be resolved/)).toBeVisible()

    await user.click(screen.getByRole('button', { name: 'Preview label removal' }))

    expect(screen.getByText(/Will be removed: AI value.*run-one/)).toBeVisible()
  })

  it('can deselect and reselect camera views before evaluating a sample', async () => {
    const user = userEvent.setup()
    const submit = vi.fn().mockResolvedValue({ id: 'sample' })
    mockBatch.mockReturnValue({ submit, isPending: false })
    mockDatasetJobs.mockReturnValue({
      inventory: { data: { items: [0, 1], total: 2 } },
      annotated: { data: [0] },
      usable: { data: [0] },
      jobs: {},
      approvals: {},
      reset: {},
      review: {},
      act: mockDatasetAction,
      refresh: vi.fn(),
    })
    mockSampleEvidence.mockReturnValue({
      data: [{ index: 0, reference: { snapshotId: 'snapshot', labelsRevision: 'labels' } }],
    })
    useEpisodeStore.setState({
      currentEpisode: {
        meta: { index: 0, length: 10, taskIndex: 0, hasAnnotations: false },
        cameras: ['front', 'wrist'],
        videoUrls: {},
        trajectoryData: [],
      },
    })
    render(<DatasetWorkspace datasetId="demo" open enabled onToggle={vi.fn()} />)

    await user.click(screen.getByRole('checkbox', { name: 'front' }))
    expect(screen.getByRole('checkbox', { name: 'wrist' })).toBeDisabled()
    await user.click(screen.getByRole('checkbox', { name: 'front' }))
    await user.click(screen.getByRole('button', { name: 'Evaluate grounding episodes' }))

    expect(submit).toHaveBeenCalledWith(
      expect.objectContaining({ options: { processMethod: 'gvl', views: ['wrist', 'front'] } }),
    )
  })

  it('enforces both grounding and episode-list size limits', async () => {
    const user = userEvent.setup()
    mockBatch.mockReturnValue({ submit: vi.fn(), isPending: false })
    mockDatasetJobs.mockReturnValue({
      inventory: { data: { items: Array.from({ length: 30 }, (_, index) => index), total: 30 } },
      jobs: {},
      approvals: {},
      reset: {},
      review: {},
      act: mockDatasetAction,
      refresh: vi.fn(),
    })
    render(<DatasetWorkspace datasetId="demo" open enabled onToggle={vi.fn()} />)

    await user.type(screen.getByRole('textbox', { name: 'Grounding episodes' }), '0-25')

    expect(screen.getByText('Use at most 25 grounding episodes.')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Evaluate grounding episodes' })).toBeDisabled()
    expect(parseEpisodeList('0-10000').error).toBe('Enter at most 10000 episodes.')
    expect(parseEpisodeList('0-25', new Set()).error).toContain(', ...')
  })

  it('keeps sample controls available after a rejected submission', async () => {
    const user = userEvent.setup()
    const submit = vi.fn().mockRejectedValue(new Error('Synthetic submission failure'))
    mockBatch.mockReturnValue({ submit, isPending: false })
    mockDatasetJobs.mockReturnValue({
      inventory: { data: { items: [0], total: 1 } },
      annotated: { data: [0] },
      usable: { data: [0] },
      jobs: {},
      approvals: {},
      reset: {},
      review: {},
      act: mockDatasetAction,
      refresh: vi.fn(),
    })
    mockSampleEvidence.mockReturnValue({
      data: [{ index: 0, reference: { snapshotId: 'snapshot', labelsRevision: 'labels' } }],
    })
    render(<DatasetWorkspace datasetId="demo" open enabled onToggle={vi.fn()} />)

    await user.click(screen.getByRole('button', { name: 'Evaluate grounding episodes' }))

    expect(submit).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: 'Evaluate grounding episodes' })).toBeEnabled()
    expect(screen.queryByText(/Job review:/)).not.toBeInTheDocument()
  })

  it('reviews label baselines, approves the configuration and submits both target modes', async () => {
    const user = userEvent.setup()
    const submit = vi.fn().mockResolvedValue({ id: 'sample' })
    const sampleJob = {
      id: 'sample',
      mode: 'sample',
      status: 'succeeded',
      total: 1,
      judged: 1,
      applied: 0,
      errors: 0,
      config: { processMethod: 'chronological', views: ['wrist'] },
      configRevision: 'configuration',
      targets: [
        {
          episodeIndex: 0,
          comparison: 'disagreement',
          sample: { humanOutcome: 'success', baselineSource: 'label' },
          result: judgeResult({ outcomeSuccess: false }),
        },
      ],
    }
    mockBatch.mockReturnValue({ submit, isPending: false })
    mockDatasetAction.mockResolvedValue({ id: 'approval', configRevision: 'configuration' })
    mockDatasetJobs.mockReturnValue({
      inventory: { data: { items: [0, 1, 2], total: 3, snapshotId: 'inventory' } },
      annotated: { data: [0] },
      usable: { data: [0] },
      jobs: { data: { items: [sampleJob], total: 1 } },
      approvals: {
        data: {
          items: [
            { id: 'approval', current: true, sampleJobId: 'sample' },
            { id: 'old', current: false },
          ],
        },
      },
      reset: {},
      review: { data: sampleJob },
      act: mockDatasetAction,
      refresh: vi.fn(),
    })
    const reference = { snapshotId: 'snapshot', labelsRevision: 'label-revision' }
    mockSampleEvidence.mockReturnValue({ data: [{ index: 0, reference }] })
    act(() =>
      useEpisodeStore.setState({
        currentEpisode: {
          meta: { index: 0, length: 10, taskIndex: 0, hasAnnotations: false },
          cameras: ['top', 'wrist'],
          videoUrls: {},
          trajectoryData: [],
        },
      }),
    )
    render(<DatasetWorkspace datasetId="demo" open enabled onToggle={vi.fn()} />)
    await user.selectOptions(
      screen.getByRole('combobox', { name: 'Process method' }),
      'chronological',
    )
    await user.click(screen.getByRole('checkbox', { name: 'top' }))
    expect(screen.getByRole('checkbox', { name: 'wrist' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Evaluate grounding episodes' }))
    expect(submit).toHaveBeenLastCalledWith(
      expect.objectContaining({
        mode: 'sample',
        indices: [0],
        samples: { 0: reference },
        options: { processMethod: 'chronological', views: ['wrist'] },
      }),
    )
    expect(screen.getByText(/saved label success; judge false/)).toBeVisible()
    expect(screen.getByRole('button', { name: 'Approve configuration' })).toBeDisabled()
    await user.click(
      screen.getByRole('checkbox', { name: 'I reviewed disagreements and inconclusive results' }),
    )
    await user.click(screen.getByRole('button', { name: 'Approve configuration' }))
    expect(mockDatasetAction).toHaveBeenLastCalledWith({
      kind: 'approve',
      jobId: 'sample',
      acknowledgeExceptions: true,
    })
    await user.click(screen.getByRole('button', { name: 'Judge targets' }))
    expect(submit).toHaveBeenLastCalledWith(
      expect.objectContaining({ mode: 'judge', indices: [1, 2], approvalId: 'approval' }),
    )
    await user.click(screen.getByRole('button', { name: 'Judge and label targets' }))
    expect(submit).toHaveBeenLastCalledWith(
      expect.objectContaining({ mode: 'judge-and-label', indices: [1, 2] }),
    )
    expect(screen.getByRole('option', { name: 'old (stale)' })).toBeDisabled()
    await user.type(screen.getByRole('textbox', { name: 'Target episodes' }), '9')
    expect(screen.getByRole('button', { name: 'Judge targets' })).toBeDisabled()
    expect(screen.getByText('Not in this dataset: 9.')).toBeVisible()
    await user.clear(screen.getByRole('textbox', { name: 'Target episodes' }))
    await user.selectOptions(screen.getByRole('combobox', { name: 'Configuration approval' }), '')
    await user.selectOptions(
      screen.getByRole('combobox', { name: 'Configuration approval' }),
      'approval',
    )
    expect(screen.getByRole('button', { name: 'Judge targets' })).toBeEnabled()
  })

  it('reviews durable sample and target jobs, retries failures, pages history and refreshes', async () => {
    const user = userEvent.setup()
    const refresh = vi.fn()
    mockDatasetAction.mockResolvedValue({})
    mockBatch.mockReturnValue({ submit: vi.fn(), isPending: false })
    mockDatasetJobs.mockReturnValue({
      inventory: {},
      approvals: {},
      reset: {},
      review: {},
      jobs: {
        error: new Error('refresh failed'),
        data: {
          total: 30,
          items: [
            { id: 'sample', mode: 'sample', status: 'succeeded', config: {}, total: 1 },
            { id: 'failed', mode: 'judge', status: 'failed', config: {}, total: 1 },
          ],
        },
      },
      act: mockDatasetAction,
      refresh,
    })
    render(<DatasetWorkspace datasetId="demo" open enabled onToggle={vi.fn()} />)
    expect(screen.getByText(/Previously loaded jobs remain visible/)).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Review samples' }))
    await user.click(screen.getByRole('button', { name: 'Job details' }))
    await user.click(screen.getByRole('button', { name: 'Retry job failed' }))
    expect(mockDatasetAction).toHaveBeenLastCalledWith({ kind: 'retry', jobId: 'failed' })
    await user.click(screen.getByRole('button', { name: 'Next jobs' }))
    expect(screen.getByRole('button', { name: 'Previous jobs' })).toBeEnabled()
    await user.click(screen.getByRole('button', { name: 'Previous jobs' }))
    expect(screen.getByRole('button', { name: 'Previous jobs' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Refresh dataset jobs' }))
    expect(refresh).toHaveBeenCalledOnce()
  })

  it('reports unavailable saved baselines and approvals and retries a partial label removal', async () => {
    const user = userEvent.setup()
    mockDatasetAction.mockResolvedValue({})
    mockBatch.mockReturnValue({
      submit: vi.fn(),
      isPending: false,
      error: new Error('private request'),
    })
    mockSampleEvidence.mockReturnValue({ error: new Error('private annotation') })
    mockDatasetJobs.mockReturnValue({
      inventory: { error: new Error('private inventory') },
      annotated: { error: new Error('private annotation') },
      usable: {},
      approvals: { error: new Error('private approval') },
      jobs: {},
      review: {},
      reset: { data: { status: 'partial' } },
      act: mockDatasetAction,
      refresh: vi.fn(),
    })
    render(<DatasetWorkspace datasetId="demo" open enabled onToggle={vi.fn()} />)
    expect(screen.getByText('Episode inventory could not be loaded.')).toBeVisible()
    expect(screen.getByText('Annotated episodes could not be loaded.')).toBeVisible()
    expect(
      screen.getByText('Approval status is unavailable. Refresh before submitting.'),
    ).toBeVisible()
    expect(
      screen.getByText('Saved annotations could not be loaded. Refresh before evaluating.'),
    ).toBeVisible()
    expect(screen.queryByText(/private/)).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Retry label removal' }))
    expect(mockDatasetAction).toHaveBeenLastCalledWith({ kind: 'retry-reset' })
  })

  it('retries retained judge evidence and selects a scoring method without rerunning automatically', async () => {
    const user = userEvent.setup()
    const refetch = vi.fn()
    mockUseStatus.mockReturnValue({ data: status({ result: judgeResult() }), isLoading: false })
    mockEvidence.mockReturnValue({
      error: new Error('private error'),
      data: {
        items: [
          {
            resultKind: 'judge',
            runId: 'saved',
            resultId: 'result',
            applicability: 'current',
            applied: true,
            input: { snapshotId: 'snapshot' },
            result: judgeResult(),
          },
          {
            resultKind: 'task-findings',
            runId: 'task',
            resultId: 'task-result',
            applicability: 'current',
            input: { snapshotId: 'snapshot' },
            result: {
              judgeModel: 'echo',
              findings: {
                object: 'cube',
                pickFrom: 'table',
                graspSuccess: true,
                placeSuccess: false,
                movementQuality: 'steady',
                notes: 'saved',
              },
            },
          },
        ],
      },
      refetch,
    })
    render(<JudgePanel datasetId="demo" episodeIndex={0} />)
    await user.click(screen.getByRole('button', { name: 'Retry evidence' }))
    expect(refetch).toHaveBeenCalledOnce()
    expect(mockMutate).not.toHaveBeenCalled()
    await user.click(screen.getByRole('combobox'))
    await user.click(screen.getByRole('option', { name: 'Chronological' }))
    await user.click(screen.getByRole('button', { name: /re-evaluate/i }))
    expect(mockMutate).toHaveBeenCalledWith(
      expect.objectContaining({ options: { processMethod: 'chronological', force: false } }),
    )
    expect(screen.getByText('cube')).toBeInTheDocument()
  })

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
      if (state !== 'denied') {
        expect(screen.getByText('Judge operations are unavailable.')).toBeVisible()
      }
      expect(screen.queryByRole('button', { name: 'Refresh dataset jobs' })).not.toBeInTheDocument()
      expect(
        screen.queryByRole('button', {
          name: /evaluate grounding|judge.*targets|preview.*withdrawal|preview label removal/i,
        }),
      ).not.toBeInTheDocument()
      expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
      expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
      expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
      expect(screen.queryByText('Loading episodes...')).not.toBeInTheDocument()
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
    await user.type(screen.getByRole('textbox', { name: 'Target episodes' }), '3')
    expect(screen.getByRole('textbox', { name: 'Target episodes' })).toHaveValue('3')
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
    expect(screen.getByRole('textbox', { name: 'Target episodes' })).toHaveValue('')
  })

  it('parses comma-separated episode numbers and ranges', () => {
    expect(parseEpisodeList(' 0, 2,4-6 ,2')).toEqual({ indices: [0, 2, 4, 5, 6], error: null })
    expect(parseEpisodeList('')).toEqual({ indices: [], error: null })
    expect(parseEpisodeList('3,x').error).toMatch(/"x" is not an episode number/)
    expect(parseEpisodeList('8-4').error).toMatch(/low to high/)
    expect(parseEpisodeList('1,9', new Set([1, 2])).error).toBe('Not in this dataset: 9.')
    expect(formatEpisodeList([8, 0, 2, 4, 5, 6, 9])).toBe('0, 2, 4-6, 8, 9')
  })

  it('prefills grounding with annotated episodes and judges the remaining episodes', async () => {
    const user = userEvent.setup()
    const submit = vi.fn().mockResolvedValue({ id: 'sample-job' })
    mockBatch.mockReturnValue({ submit, isPending: false })
    mockReadiness.mockReturnValue({ ready: true })
    mockDatasetJobs.mockReturnValue({
      inventory: { data: { items: [3, 4, 5], total: 3 } },
      annotated: { data: [3, 5] },
      usable: { data: [3] },
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
      data: [{ index: 3, reference, rating: 'success', instruction: 'Saved instruction' }],
    })
    render(<DatasetWorkspace datasetId="demo" open enabled onToggle={vi.fn()} />)
    expect(
      screen.getByText(
        '2 of 3 episodes with saved labels or annotations; 1 usable for grounding: 3',
      ),
    ).toBeVisible()
    expect(screen.getByText('Not usable yet: 5.')).toBeVisible()
    expect(screen.getByRole('textbox', { name: 'Grounding episodes' })).toHaveAccessibleDescription(
      /Instruction source is unrestricted/,
    )
    expect(screen.getByRole('textbox', { name: 'Grounding episodes' })).toHaveValue('3')
    expect(screen.getByText('2 of 3 episodes selected')).toBeVisible()
    expect(mockReadiness).toHaveBeenLastCalledWith('demo', [4, 5, 3], true)
    expect(screen.getByRole('button', { name: 'Judge targets' })).toHaveAccessibleDescription(
      /Evaluate the grounding episodes/,
    )
    await user.click(screen.getByRole('button', { name: 'Evaluate grounding episodes' }))
    expect(submit).toHaveBeenCalledWith(
      expect.objectContaining({ mode: 'sample', indices: [3], samples: { 3: reference } }),
    )
  })

  it('explains invalid grounding input and episodes without a usable saved annotation', async () => {
    const user = userEvent.setup()
    mockBatch.mockReturnValue({ submit: vi.fn(), isPending: false })
    mockReadiness.mockReturnValue({ ready: true })
    mockDatasetJobs.mockReturnValue({
      inventory: { data: { items: [3, 4], total: 2 } },
      annotated: { data: [3, 4] },
      usable: { data: [3, 4] },
      jobs: {},
      approvals: {},
      reset: {},
      review: {},
      act: mockDatasetAction,
      refresh: vi.fn(),
    })
    mockSampleEvidence.mockReturnValue({
      data: [
        { index: 3, reference: null },
        {
          index: 4,
          reference: { annotationAuthorId: 'a', annotationRevision: 'r', snapshotId: 's' },
        },
      ],
    })
    render(<DatasetWorkspace datasetId="demo" open enabled onToggle={vi.fn()} />)
    const evaluate = screen.getByRole('button', { name: 'Evaluate grounding episodes' })
    expect(evaluate).toBeDisabled()
    expect(evaluate).toHaveAccessibleDescription(
      'Episodes 3 need a saved task instruction and one SUCCESS, FAILURE or PARTIAL label, or a saved task-completeness rating.',
    )
    const input = screen.getByRole('textbox', { name: 'Grounding episodes' })
    await user.clear(input)
    await user.type(input, '4,7')
    expect(input).toHaveAttribute('aria-invalid', 'true')
    expect(input).toHaveAccessibleDescription(/Not in this dataset: 7\./)
    expect(evaluate).toHaveAccessibleDescription('Fix the grounding episode list.')
  })

  it.each([
    ['conflicted', [{ key: 'labels', status: 'pending' }], /not applied because labels changed/],
    ['partial', [{ key: 'labels', status: 'pending' }], /Retry it under Remove AI-applied labels/],
    ['conflicted', [{ key: 'labels', status: 'succeeded' }], null],
  ])('explains whether a %s label removal blocks batch controls', (status, resources, reason) => {
    mockBatch.mockReturnValue({ submit: vi.fn(), isPending: false })
    mockDatasetJobs.mockReturnValue({
      inventory: { data: { items: [3], total: 1 } },
      jobs: {},
      approvals: {},
      reset: {
        data: {
          id: 'reset',
          status,
          resources,
          summary: { fields: [], conflicts: 1, removableFields: 0 },
        },
      },
      review: {},
      act: mockDatasetAction,
      refresh: vi.fn(),
    })
    render(<DatasetWorkspace datasetId="demo" open enabled onToggle={vi.fn()} />)
    const description = screen
      .getByRole('button', { name: 'Evaluate grounding episodes' })
      .getAttribute('aria-describedby')
    const text = description ? document.getElementById(description)?.textContent : ''
    if (reason) expect(text).toMatch(reason)
    else expect(text).toBe('Enter at least one grounding episode.')
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
    await user.type(screen.getByRole('textbox', { name: 'Target episodes' }), '1005')
    expect(mockReadiness).toHaveBeenLastCalledWith('demo', [1005], true)
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
    [new ApiClientError('denied', 'HTTP_403', 403), /not authorized/],
    [new ApiClientError('absent', 'HTTP_404', 404), /run was not found/],
    [new ApiClientError('server', 'HTTP_500', 500), /failed on the server/],
    [new Error('local failure'), /Label application failed: local failure/],
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
