import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { JudgePanel } from '@/components/vlm-judge'
import type { VlmJudgeResult, VlmJudgeStatus } from '@/types'

const mockUseStatus = vi.fn()
const mockMutate = vi.fn()
const mockUseRun = vi.fn()
const mockSaveLabels = vi.fn()
const mockRunAll = vi.fn()
const mockApplyLabelsAll = vi.fn()
const mockCancel = vi.fn()
const mockBatch = vi.fn()

vi.mock('@/hooks/use-vlm-judge', () => ({
  useVlmJudgeStatus: () => mockUseStatus(),
  useRunVlmJudge: () => mockUseRun(),
}))

vi.mock('@/hooks/use-labels', () => ({
  useSaveEpisodeLabels: () => ({ mutate: mockSaveLabels, isPending: false }),
  labelKeys: { dataset: (id: string) => ['labels', id] },
}))

vi.mock('@/hooks/use-vlm-judge-batch', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/hooks/use-vlm-judge-batch')>()
  return { ...actual, useVlmJudgeBatch: () => mockBatch() }
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
  beforeEach(() => {
    mockUseStatus.mockReset()
    mockMutate.mockReset()
    mockUseRun.mockReset()
    mockSaveLabels.mockReset()
    mockRunAll.mockReset()
    mockApplyLabelsAll.mockReset()
    mockCancel.mockReset()
    mockBatch.mockReset()
    mockUseRun.mockReturnValue({
      mutate: mockMutate,
      isPending: false,
      error: null,
      data: undefined,
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

  it('shows a disabled hint when the judge backend is not enabled', () => {
    mockUseStatus.mockReturnValue({
      data: status({ enabled: false }),
      isLoading: false,
      error: null,
    })
    render(<JudgePanel datasetId="demo" episodeIndex={0} />)
    expect(screen.getByText(/not enabled for this server/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /run judge/i })).not.toBeInTheDocument()
  })

  it('renders Run button and prompts to run when no result exists yet', async () => {
    const user = userEvent.setup()
    mockUseStatus.mockReturnValue({ data: status(), isLoading: false, error: null })
    render(<JudgePanel datasetId="demo" episodeIndex={2} instruction="Pick up cube" />)
    expect(screen.getByText(/no judgment yet/i)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /run judge/i }))
    expect(mockMutate).toHaveBeenCalledWith({
      datasetId: 'demo',
      episodeIndex: 2,
      options: { instruction: 'Pick up cube', processMethod: 'gvl', force: false },
    })
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
      options: { instruction: undefined, processMethod: 'gvl', force: true },
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
      options: { instruction: undefined, processMethod: 'chronological', force: false },
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

  it('applies the current outcome as the episode label', async () => {
    const user = userEvent.setup()
    const result = judgeResult({ outcomeSuccess: true })
    mockUseStatus.mockReturnValue({ data: status({ result }), isLoading: false, error: null })
    render(<JudgePanel datasetId="demo" episodeIndex={4} />)
    await user.click(screen.getByRole('button', { name: /apply label/i }))
    expect(mockSaveLabels).toHaveBeenCalledWith({ episodeIdx: 4, labels: ['SUCCESS'] })
  })

  it('exposes batch actions and runs the judge across the dataset', async () => {
    const user = userEvent.setup()
    mockUseStatus.mockReturnValue({ data: status(), isLoading: false, error: null })
    render(<JudgePanel datasetId="demo" episodeIndex={0} totalEpisodes={3} />)
    expect(screen.getByText(/whole dataset \(3 episodes\)/i)).toBeInTheDocument()
    expect(
      screen.getByText(/uses each episode's saved or dataset instruction/i),
    ).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /run all/i }))
    expect(mockRunAll).toHaveBeenCalledWith({ processMethod: 'gvl' })
    await user.click(screen.getByRole('button', { name: /label all/i }))
    expect(mockApplyLabelsAll).toHaveBeenCalledWith({ processMethod: 'gvl' })
  })

  it('renders batch progress with a cancel control', async () => {
    const user = userEvent.setup()
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
    expect(screen.getByText('1 / 3')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /cancel/i }))
    expect(mockCancel).toHaveBeenCalled()
  })
})
