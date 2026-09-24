import { cleanup, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ReleaseDialog } from '@/components/release/ReleaseDialog'
import {
  useCancelRelease,
  useReleaseEligibility,
  useReleaseJob,
  useSubmitRelease,
} from '@/hooks/use-releases'
import { ApiClientError } from '@/lib/api-client'
import { recordDiagnosticEvent } from '@/lib/playback-diagnostics'
import { renderWithQuery } from '@/test-utils/render'
import type { ReleaseWorkflowResponse, ReviewDecision } from '@/types'

vi.mock('@/hooks/use-releases', () => ({
  useCancelRelease: vi.fn(),
  useReleaseEligibility: vi.fn(),
  useReleaseJob: vi.fn(),
  useSubmitRelease: vi.fn(),
}))

vi.mock('@/lib/playback-diagnostics', () => ({
  recordDiagnosticEvent: vi.fn(),
}))

const decision = {
  decisionId: 'decision-1',
  decision: 'accept',
  reasonCodes: ['evidence-reviewed'],
  actorId: 'reviewer',
  qualityRunId: 'quality-1',
} as ReviewDecision

const workflow: ReleaseWorkflowResponse = {
  releaseId: 'release-1',
  jobId: 'job-1',
  state: 'running',
  eligibleEpisodes: [{ episodeIndex: 2, decisionId: 'decision-1', qualityRunId: 'quality-1' }],
  excludedEpisodes: [],
  rejectedEpisodes: [],
  eligibilityFingerprint: 'f'.repeat(64),
  conflict: null,
  verification: { manifestPath: null, checksumsPath: null, verified: false },
}

function mutation(overrides: Record<string, unknown> = {}) {
  return { mutate: vi.fn(), isPending: false, data: undefined, error: null, ...overrides }
}

beforeEach(() => {
  vi.clearAllMocks()
  window.localStorage.clear()
  vi.mocked(useReleaseEligibility).mockReturnValue({
    data: {
      eligibleEpisodes: workflow.eligibleEpisodes,
      excludedEpisodes: [],
      rejectedEpisodes: [],
      eligibilityFingerprint: workflow.eligibilityFingerprint,
    },
    isPending: false,
    isFetching: false,
    refetch: vi.fn().mockResolvedValue({
      data: {
        eligibleEpisodes: workflow.eligibleEpisodes,
        excludedEpisodes: [],
        rejectedEpisodes: [],
        eligibilityFingerprint: workflow.eligibilityFingerprint,
      },
    }),
    error: null,
  } as unknown as ReturnType<typeof useReleaseEligibility>)
  vi.mocked(useSubmitRelease).mockReturnValue(
    mutation() as unknown as ReturnType<typeof useSubmitRelease>,
  )
  vi.mocked(useReleaseJob).mockReturnValue({
    data: undefined,
    error: null,
  } as ReturnType<typeof useReleaseJob>)
  vi.mocked(useCancelRelease).mockReturnValue(
    mutation() as unknown as ReturnType<typeof useCancelRelease>,
  )
})

afterEach(cleanup)

describe('ReleaseDialog', () => {
  it('shows eligibility and submits only backend-derived destination configuration', async () => {
    const user = userEvent.setup()
    const submit = vi.fn()
    vi.mocked(useSubmitRelease).mockReturnValue(
      mutation({ mutate: submit }) as unknown as ReturnType<typeof useSubmitRelease>,
    )

    renderWithQuery(
      <ReleaseDialog
        open
        onOpenChange={vi.fn()}
        datasetId="dataset-1"
        episodeIndex={2}
        actorId="reviewer"
        decision={decision}
      />,
    )

    expect(screen.getByText(/1 included/i)).toBeInTheDocument()
    await user.type(screen.getByLabelText(/release id/i), 'release-1')
    await user.type(screen.getByLabelText(/reason/i), 'Approved training set')
    await user.click(screen.getByRole('button', { name: /create release/i }))

    expect(submit).toHaveBeenCalledWith(
      expect.objectContaining({
        datasetId: 'dataset-1',
        actorId: 'reviewer',
        destinationKind: 'local',
        targetFormat: { name: 'lerobot', version: '3.0' },
        episodes: [],
      }),
    )
    expect(screen.queryByLabelText(/path|prefix/i)).not.toBeInTheDocument()
  })

  it('explains exclusions and disables release without an accepted decision', () => {
    vi.mocked(useReleaseEligibility).mockReturnValue({
      data: {
        eligibleEpisodes: [],
        excludedEpisodes: [{ episodeIndex: 2, reasonCodes: ['quality-required-check-failed'] }],
        rejectedEpisodes: [],
        eligibilityFingerprint: 'e'.repeat(64),
      },
      isPending: false,
      isFetching: false,
      refetch: vi.fn(),
      error: null,
    } as unknown as ReturnType<typeof useReleaseEligibility>)

    renderWithQuery(
      <ReleaseDialog
        open
        onOpenChange={vi.fn()}
        datasetId="dataset-1"
        episodeIndex={2}
        actorId="reviewer"
        decision={{ ...decision, decision: 'reject' }}
      />,
    )

    expect(screen.getByText(/quality required check failed/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /create release/i })).toBeDisabled()
  })

  it('explains how to recover when saved source bytes changed after acceptance', () => {
    vi.mocked(useReleaseEligibility).mockReturnValue({
      data: {
        eligibleEpisodes: [],
        excludedEpisodes: [{ episodeIndex: 2, reasonCodes: ['source-identity-changed'] }],
        rejectedEpisodes: [],
        eligibilityFingerprint: 'e'.repeat(64),
      },
      isPending: false,
      isFetching: false,
      refetch: vi.fn(),
      error: null,
    } as unknown as ReturnType<typeof useReleaseEligibility>)

    renderWithQuery(
      <ReleaseDialog
        open
        onOpenChange={vi.fn()}
        datasetId="dataset-1"
        episodeIndex={2}
        actorId="reviewer"
        decision={decision}
      />,
    )

    expect(
      screen.getByText(
        /saved episode data changed after acceptance\. run quality and accept the current saved version before release\./i,
      ),
    ).toBeVisible()
  })

  it('shows durable progress, cancellation, verification, and conflicts', async () => {
    const user = userEvent.setup()
    const cancel = vi.fn()
    vi.mocked(useReleaseJob).mockReturnValue({
      data: workflow,
      error: null,
    } as unknown as ReturnType<typeof useReleaseJob>)
    vi.mocked(useCancelRelease).mockReturnValue(
      mutation({ mutate: cancel }) as unknown as ReturnType<typeof useCancelRelease>,
    )
    const { rerender } = renderWithQuery(
      <ReleaseDialog
        open
        onOpenChange={vi.fn()}
        datasetId="dataset-1"
        episodeIndex={2}
        actorId="reviewer"
        decision={decision}
      />,
    )

    expect(screen.getByText(/running/i)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /cancel release/i }))
    expect(cancel).toHaveBeenCalledWith('job-1')

    vi.mocked(useReleaseJob).mockReturnValue({
      data: {
        ...workflow,
        state: 'succeeded',
        verification: {
          manifestPath: 'releases/release-1/manifest.json',
          checksumsPath: 'releases/release-1/checksums.sha256',
          verified: true,
        },
      },
      error: null,
    } as unknown as ReturnType<typeof useReleaseJob>)
    rerender(
      <ReleaseDialog
        open
        onOpenChange={vi.fn()}
        datasetId="dataset-1"
        episodeIndex={2}
        actorId="reviewer"
        decision={decision}
      />,
    )

    expect(screen.getByText('releases/release-1/manifest.json')).toBeInTheDocument()
    expect(screen.getByText('releases/release-1/checksums.sha256')).toBeInTheDocument()

    vi.mocked(useReleaseJob).mockReturnValue({
      data: undefined,
      error: new ApiClientError('The request conflicts with the current state', 'HTTP_409', 409),
    } as unknown as ReturnType<typeof useReleaseJob>)
    rerender(
      <ReleaseDialog
        open
        onOpenChange={vi.fn()}
        datasetId="dataset-1"
        episodeIndex={2}
        actorId="reviewer"
        decision={decision}
      />,
    )

    expect(screen.getByText(/conflicts with the current state/i)).toBeInTheDocument()
  })

  it('uses stable eligibility input while release details are edited', async () => {
    const user = userEvent.setup()

    renderWithQuery(
      <ReleaseDialog
        open
        onOpenChange={vi.fn()}
        datasetId="dataset-1"
        episodeIndex={2}
        actorId="reviewer"
        decision={decision}
      />,
    )

    const initialRequest = vi.mocked(useReleaseEligibility).mock.calls.at(-1)?.[0]
    await user.type(screen.getByLabelText(/release id/i), 'release-1')
    await user.type(screen.getByLabelText(/reason/i), 'Approved training set')

    expect(vi.mocked(useReleaseEligibility).mock.calls.at(-1)?.[0]).toBe(initialRequest)
    expect(initialRequest?.episodes).toEqual([])
  })

  it('keeps mixed accepted and rejected datasets releasable', () => {
    vi.mocked(useReleaseEligibility).mockReturnValue({
      data: {
        eligibleEpisodes: workflow.eligibleEpisodes,
        excludedEpisodes: [{ episodeIndex: 4, reasonCodes: ['unreviewed'] }],
        rejectedEpisodes: [
          { episodeIndex: 3, reasonCodes: ['rejected'], decisionId: 'decision-3' },
        ],
        eligibilityFingerprint: 'a'.repeat(64),
      },
      isPending: false,
      isFetching: false,
      refetch: vi.fn(),
      error: null,
    } as unknown as ReturnType<typeof useReleaseEligibility>)

    renderWithQuery(
      <ReleaseDialog
        open
        onOpenChange={vi.fn()}
        datasetId="dataset-1"
        episodeIndex={2}
        actorId="reviewer"
        decision={decision}
      />,
    )

    expect(screen.getByText(/1 included/i)).toBeVisible()
    expect(screen.getByText(/2 excluded/i)).toBeVisible()
    expect(screen.getByText(/episode 3.*rejected/i)).toBeVisible()
    expect(screen.getByText(/episode 4.*unreviewed/i)).toBeVisible()
  })

  it('clears terminal job persistence and can start another release', async () => {
    const user = userEvent.setup()
    const reset = vi.fn()
    const succeededWorkflow = {
      ...workflow,
      state: 'succeeded',
      verification: {
        manifestPath: 'releases/release-1/manifest.json',
        checksumsPath: 'releases/release-1/checksums.sha256',
        verified: true,
      },
    } satisfies ReleaseWorkflowResponse
    window.localStorage.setItem('dataviewer:release-job:dataset-1', workflow.jobId)
    vi.mocked(useSubmitRelease).mockReturnValue(
      mutation({ reset }) as unknown as ReturnType<typeof useSubmitRelease>,
    )
    vi.mocked(useReleaseJob).mockImplementation(
      (jobId) =>
        ({
          data: jobId ? succeededWorkflow : undefined,
          error: null,
        }) as ReturnType<typeof useReleaseJob>,
    )

    renderWithQuery(
      <ReleaseDialog
        open
        onOpenChange={vi.fn()}
        datasetId="dataset-1"
        episodeIndex={2}
        actorId="reviewer"
        decision={decision}
      />,
    )

    expect(window.localStorage.getItem('dataviewer:release-job:dataset-1')).toBeNull()
    await user.click(screen.getByRole('button', { name: /start new release/i }))

    expect(reset).toHaveBeenCalledOnce()
    expect(screen.getByLabelText(/release id/i)).toBeEnabled()
    expect(screen.getByRole('button', { name: /create release/i })).toBeInTheDocument()
  })

  it('shows backend conflict detail and records each job state once', () => {
    const conflictWorkflow = {
      ...workflow,
      state: 'conflict',
      conflict: 'Release destination already contains release-1.',
    } satisfies ReleaseWorkflowResponse
    vi.mocked(useReleaseJob).mockReturnValue({
      data: conflictWorkflow,
      error: null,
    } as ReturnType<typeof useReleaseJob>)

    const { rerender } = renderWithQuery(
      <ReleaseDialog
        open
        onOpenChange={vi.fn()}
        datasetId="dataset-1"
        episodeIndex={2}
        actorId="reviewer"
        decision={decision}
      />,
    )

    expect(screen.getByText(conflictWorkflow.conflict)).toBeInTheDocument()
    rerender(
      <ReleaseDialog
        open
        onOpenChange={vi.fn()}
        datasetId="dataset-1"
        episodeIndex={2}
        actorId="reviewer"
        decision={decision}
      />,
    )
    expect(recordDiagnosticEvent).toHaveBeenCalledOnce()
  })
})
