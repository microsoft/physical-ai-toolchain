import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { JudgePanel } from '@/components/vlm-judge'
import type { VlmJudgeResult, VlmJudgeStatus } from '@/types'

const mockUseStatus = vi.fn()
const mockMutate = vi.fn()
const mockUseRun = vi.fn()

vi.mock('@/hooks/use-vlm-judge', () => ({
    useVlmJudgeStatus: () => mockUseStatus(),
    useRunVlmJudge: () => mockUseRun(),
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
        mockUseRun.mockReturnValue({ mutate: mockMutate, isPending: false, error: null, data: undefined })
    })

    it('shows a disabled hint when the judge backend is not enabled', () => {
        mockUseStatus.mockReturnValue({ data: status({ enabled: false }), isLoading: false, error: null })
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
            options: { instruction: 'Pick up cube', force: false },
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
            options: { instruction: undefined, force: true },
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
})
