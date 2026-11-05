import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  fetchSourceEpisodeMedia,
  getReleaseArtifact,
  getSourceRelease,
  listSourceEpisodes,
  listSourceReleases,
  type ReleaseJobStatus,
  ReleaseRequestError,
} from '@/api/releases'
import { ReleaseWorkspace } from '@/components/release/ReleaseWorkspace'
import { usePrincipalContext } from '@/hooks/use-principal-context'
import { useRelease } from '@/hooks/use-release'
import { useEpisodeStore } from '@/stores'
import { renderWithQuery } from '@/test-utils/render'

const { dialogSpy } = vi.hoisted(() => ({
  dialogSpy: { props: null as null | Record<string, unknown> },
}))

vi.mock('@/hooks/use-principal-context', () => ({
  usePrincipalContext: vi.fn(),
}))

vi.mock('@/hooks/use-release', () => ({
  useRelease: vi.fn(),
}))

vi.mock('@/components/release/ReleaseDialog', () => ({
  ReleaseDialog: (props: Record<string, unknown>) => {
    dialogSpy.props = props
    return null
  },
}))

vi.mock('@/api/releases', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/releases')>()),
  listSourceReleases: vi.fn(),
  getSourceRelease: vi.fn(),
  listSourceEpisodes: vi.fn(),
  fetchSourceEpisodeMedia: vi.fn(),
  getReleaseArtifact: vi.fn(),
}))

const JOB_ID = 'a'.repeat(32)
const HANDLE = 'b'.repeat(64)

type ReleaseHook = ReturnType<typeof useRelease>

function statusOf(state: ReleaseJobStatus['state'], overrides: Partial<ReleaseJobStatus> = {}) {
  return {
    jobId: JOB_ID,
    state,
    cancelRequested: false,
    effectsStopped: false,
    cleanupState: 'none',
    capacityState: 'ok',
    destinationCurrentBytes: 0,
    destinationPeakBytes: 0,
    schema: 'v1',
    scratchBytes: 0,
    peakScratchBytes: 0,
    ioCounters: {},
    diagnostics: {},
    ...overrides,
  } satisfies ReleaseJobStatus
}

/** Build a release hook double. Fields not supplied get idle defaults. */
function makeRelease(patch: Record<string, unknown> = {}) {
  const base = {
    inventory: { isError: false, isSuccess: true, data: { items: [], total: 0 }, refetch: vi.fn() },
    inventoryItems: [],
    inventoryProgress: { loaded: 0, total: null },
    inventoryReady: true,
    requestDetails: vi.fn(),
    detailError: null,
    capabilities: {
      data: {
        schema: 'v1',
        jobStore: true,
        planning: true,
        destinations: [{ alias: 'local' }],
        operations: [],
        target: { name: 'v3', version: '1' },
        routes: [],
        capacity: 'ok',
      },
      error: null,
    },
    activeJobId: null as string | null,
    status: { data: undefined, isError: false, isFetching: false, error: null, refetch: vi.fn() },
    result: { data: undefined, isError: false },
    preview: vi.fn(),
    isPreviewing: false,
    submit: vi.fn().mockResolvedValue({ jobId: JOB_ID, replayed: false }),
    isSubmitting: false,
    cancel: vi.fn().mockResolvedValue(undefined),
    isCancelling: false,
    recover: vi.fn().mockResolvedValue(undefined),
    isRecovering: false,
    startNew: vi.fn(),
    selectJob: vi.fn(),
    hasPendingSubmission: false,
    pendingLookup: { isError: false, isFetching: false, refetch: vi.fn() },
    jobs: {
      data: undefined,
      isError: false,
      isLoading: false,
      hasNextPage: false,
      isFetchingNextPage: false,
      fetchNextPage: vi.fn(),
    },
    ...patch,
  }
  return base as unknown as ReleaseHook
}

function renderWorkspace() {
  renderWithQuery(<ReleaseWorkspace datasetId="dataset-1" />)
}

describe('ReleaseWorkspace', () => {
  beforeEach(() => {
    dialogSpy.props = null
    vi.mocked(usePrincipalContext).mockReturnValue({ data: { scopeId: 'scope-1' } } as never)
    useEpisodeStore.setState({
      currentEpisode: { sourceId: 'source-1', sourceRevision: 'rev-1', cameras: [] } as never,
    })
    vi.mocked(useRelease).mockReturnValue(makeRelease())
    vi.mocked(listSourceReleases).mockResolvedValue({ items: [] })
  })

  afterEach(() => {
    cleanup()
    useEpisodeStore.setState({ currentEpisode: null })
    vi.clearAllMocks()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('explains denied access and disables release creation', async () => {
    vi.mocked(useRelease).mockReturnValue(
      makeRelease({
        capabilities: {
          data: undefined,
          error: new ReleaseRequestError('denied', 'forbidden', 403),
        },
      }),
    )
    renderWorkspace()
    expect(await screen.findByText('Release access is denied for this account.')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Create release' })).toBeDisabled()
  })

  it('formats I/O progress counters with readable labels and byte units', () => {
    vi.mocked(useRelease).mockReturnValue(
      makeRelease({
        activeJobId: JOB_ID,
        status: {
          data: statusOf('running', {
            ioCounters: {
              affectedDownloadBytes: 2458922,
              sourceVerificationBytes: 0,
              storageCopyBytes: 8207783,
              generatedUploadBytes: 2048,
            },
          }),
        },
      }),
    )
    renderWorkspace()
    const counters = within(screen.getByRole('list', { name: 'I/O counters' }))
    expect(counters.getByText('Affected input read').closest('li')).toHaveTextContent('2.3 MiB')
    expect(counters.getByText('Source verification read').closest('li')).toHaveTextContent('0 B')
    expect(counters.getByText('Copied within storage').closest('li')).toHaveTextContent('7.8 MiB')
    expect(counters.getByText('Generated output uploaded').closest('li')).toHaveTextContent(
      '2.0 KiB',
    )
    expect(counters.queryByText('2458922')).not.toBeInTheDocument()
  })

  it('explains accepted jobs are waiting for a worker rather than processing bytes', () => {
    vi.mocked(useRelease).mockReturnValue(
      makeRelease({ activeJobId: JOB_ID, status: { data: statusOf('accepted') } }),
    )
    renderWorkspace()
    expect(screen.getByText('Accepted. Waiting for a release worker.')).toBeVisible()
    expect(screen.getByRole('heading', { name: 'Job aaaaaaaa' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Create release' })).toBeDisabled()
  })

  it('shows stored creation dates next to job and release identities', async () => {
    const createdAt = '2026-11-04T07:00:00+00:00'
    vi.mocked(useRelease).mockReturnValue(
      makeRelease({
        jobs: { data: { pages: [{ items: [{ ...statusOf('committed'), createdAt }] }] } },
      }),
    )
    vi.mocked(listSourceReleases).mockResolvedValue({
      items: [
        {
          handle: HANDLE,
          datasetName: 'Dated release',
          packageId: 'package-1',
          manifestSha256: HANDLE,
          createdAt,
        },
      ],
    })
    renderWorkspace()
    await screen.findByText('Dated release')
    for (const name of ['Release jobs', 'Source releases']) {
      const timestamp = within(screen.getByRole('list', { name })).getByText(/^Created /)
      expect(timestamp.tagName).toBe('TIME')
      expect(timestamp).toHaveAttribute('datetime', createdAt)
      expect(timestamp).toHaveTextContent('2026')
    }
  })

  it('passes the full inventory to the dialog only once it is ready', () => {
    const inventoryItem = {
      episodeIndex: 0,
      detailed: true,
      resources: [],
      annotation: false,
      edits: true,
      labels: ['success'],
      provenance: { origins: ['human'] },
      judge: {
        status: 'saved_analysis',
        freshness: 'unknown',
        confidence: 0.8,
        outcome: 'success',
      },
    }
    vi.mocked(useRelease).mockReturnValue(
      makeRelease({ inventoryItems: [inventoryItem], inventoryProgress: { loaded: 1, total: 1 } }),
    )
    const { unmount } = renderWithQuery(<ReleaseWorkspace datasetId="dataset-1" />)
    expect(dialogSpy.props?.items).toEqual([inventoryItem])
    expect(dialogSpy.props?.inventoryReady).toBe(true)
    unmount()

    vi.mocked(useRelease).mockReturnValue(
      makeRelease({ inventoryReady: false, inventory: { isError: false, data: undefined } }),
    )
    renderWorkspace()
    expect(dialogSpy.props?.items).toEqual([])
    expect(dialogSpy.props?.inventoryReady).toBe(false)
    expect(screen.getByText('Loading source inventory...')).toBeVisible()
  })

  it('retains dialog rows while a background inventory refresh blocks preview', () => {
    const item = { episodeIndex: 6, detailed: true, resources: [], annotation: false, edits: false }
    vi.mocked(useRelease).mockReturnValue(
      makeRelease({
        inventoryReady: false,
        inventoryItems: [item],
        inventory: { isError: false, isFetching: true, data: { items: [item], total: 1 } },
      }),
    )

    renderWorkspace()

    expect(dialogSpy.props?.items).toEqual([expect.objectContaining({ episodeIndex: 6 })])
    expect(dialogSpy.props?.inventoryReady).toBe(false)
  })

  it('routes dialog submission through the hook and waits for it', async () => {
    const submit = vi.fn().mockResolvedValue({ jobId: JOB_ID, replayed: false })
    vi.mocked(useRelease).mockReturnValue(makeRelease({ submit }))
    renderWorkspace()
    const request = {
      destination: 'local',
      packageId: 'p',
      attemptId: 'x',
      datasetId: 'dataset-1',
      episodeIndices: [0],
    }
    const submission = {
      previewDigest: 'd',
      processingDigest: 'p',
      localDiskAcknowledged: true,
      idempotencyKey: 'k',
    }
    const onSubmit = dialogSpy.props?.onSubmit as (r: unknown, s: unknown) => Promise<void>
    await onSubmit(request, submission)
    expect(submit).toHaveBeenCalledWith(request, submission)
  })

  it('announces inventory progress and offers a keyboard-operable retry after failure', async () => {
    const user = userEvent.setup()
    const refetch = vi.fn()
    vi.mocked(useRelease).mockReturnValue(
      makeRelease({ inventoryReady: false, inventoryProgress: { loaded: 20, total: 296 } }),
    )
    const { rerender } = renderWithQuery(<ReleaseWorkspace datasetId="dataset-1" />)
    expect(screen.getByRole('status')).toHaveTextContent('20 of 296 episodes loaded')
    expect(dialogSpy.props?.inventoryMessage).toContain('20 of 296')

    vi.mocked(useRelease).mockReturnValue(
      makeRelease({ inventoryReady: false, inventory: { isError: true, refetch } }),
    )
    rerender(<ReleaseWorkspace datasetId="dataset-1" />)
    const retry = screen.getByRole('button', { name: 'Retry inventory' })
    retry.focus()
    await user.keyboard('{Enter}')
    expect(refetch).toHaveBeenCalledOnce()
    expect(dialogSpy.props?.inventoryFailed).toBe(true)
  })

  it('cancels the active job only after an explicit click', async () => {
    const user = userEvent.setup()
    const cancel = vi.fn().mockResolvedValue(undefined)
    vi.mocked(useRelease).mockReturnValue(
      makeRelease({
        activeJobId: JOB_ID,
        cancel,
        status: {
          data: statusOf('running'),
          isError: false,
          isFetching: false,
          error: null,
          refetch: vi.fn(),
        },
      }),
    )
    renderWorkspace()
    expect(cancel).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Cancel job' }))
    expect(cancel).toHaveBeenCalledOnce()
  })

  it('offers verified artifacts only after the job commits', () => {
    vi.mocked(useRelease).mockReturnValue(
      makeRelease({
        activeJobId: JOB_ID,
        status: {
          data: statusOf('running'),
          isError: false,
          isFetching: false,
          error: null,
          refetch: vi.fn(),
        },
        result: { data: undefined, isError: false },
      }),
    )
    const { unmount } = renderWithQuery(<ReleaseWorkspace datasetId="dataset-1" />)
    expect(screen.queryByRole('button', { name: 'Download manifest' })).not.toBeInTheDocument()
    unmount()

    vi.mocked(useRelease).mockReturnValue(
      makeRelease({
        activeJobId: JOB_ID,
        status: {
          data: statusOf('committed'),
          isError: false,
          isFetching: false,
          error: null,
          refetch: vi.fn(),
        },
        result: {
          data: { schema: 'v1', jobId: JOB_ID, result: {}, artifacts: ['manifest'] },
          isError: false,
        },
      }),
    )
    renderWorkspace()
    expect(screen.getByRole('button', { name: 'Download manifest' })).toBeVisible()
  })

  it('shows release details as read-only without editable inputs', async () => {
    const user = userEvent.setup()
    vi.mocked(listSourceReleases).mockResolvedValue({
      items: [
        {
          handle: HANDLE,
          datasetName: 'Release A',
          packageId: 'pkg-1',
          manifestSha256: 'c'.repeat(64),
        },
      ],
    })
    vi.mocked(getSourceRelease).mockResolvedValue({
      handle: HANDLE,
      schema: 'v1',
      totalEpisodes: 2,
      annotations: [],
      labels: null,
      frozenEvidence: { sourceEpisodeId: 'native/episode_000017' },
    })
    vi.mocked(listSourceEpisodes).mockResolvedValue({ items: [], total: 0, nextCursor: null })
    renderWorkspace()
    await user.click(await screen.findByRole('button', { name: /Release A/ }))
    const heading = await screen.findByText('Release details (read-only)')
    const detail = heading.closest('section') as HTMLElement
    expect(within(detail).getByText('Episodes')).toBeVisible()
    expect(within(detail).queryByRole('textbox')).not.toBeInTheDocument()
    const evidence = within(detail)
      .getByText('Original frozen evidence')
      .closest('details') as HTMLElement
    expect(evidence).toHaveTextContent('native/episode_000017')
    expect(getSourceRelease).toHaveBeenCalledWith('dataset-1', HANDLE)
    expect(getReleaseArtifact).not.toHaveBeenCalled()
    await waitFor(() => expect(listSourceEpisodes).toHaveBeenCalled())
  })

  it('opens a listed job through the hook without cancelling it', async () => {
    const user = userEvent.setup()
    const selectJob = vi.fn()
    vi.mocked(useRelease).mockReturnValue(
      makeRelease({
        selectJob,
        jobs: {
          data: { pages: [{ items: [{ jobId: JOB_ID, state: 'committed' }], nextCursor: null }] },
          isError: false,
          isLoading: false,
          hasNextPage: false,
          isFetchingNextPage: false,
          fetchNextPage: vi.fn(),
        },
      }),
    )
    renderWorkspace()
    await user.click(screen.getByRole('button', { name: 'Open job aaaaaaaa' }))
    expect(selectJob).toHaveBeenCalledWith(JOB_ID)
  })

  it('loads the next page of jobs on request', async () => {
    const user = userEvent.setup()
    const fetchNextPage = vi.fn()
    vi.mocked(useRelease).mockReturnValue(
      makeRelease({
        jobs: {
          data: { pages: [{ items: [], nextCursor: 20 }] },
          isError: false,
          isLoading: false,
          hasNextPage: true,
          isFetchingNextPage: false,
          fetchNextPage,
        },
      }),
    )
    renderWorkspace()
    await user.click(screen.getByRole('button', { name: 'Load more jobs' }))
    expect(fetchNextPage).toHaveBeenCalledOnce()
  })

  it('collapses and reopens job panes without cancelling or forgetting the job', async () => {
    const user = userEvent.setup()
    const release = makeRelease({
      activeJobId: JOB_ID,
      status: { data: statusOf('committed') },
      result: { data: { schema: 'v1', result: {}, artifacts: ['manifest'] } },
      jobs: { data: { pages: [{ items: [{ jobId: JOB_ID, state: 'committed' }] }] } },
    })
    vi.mocked(useRelease).mockReturnValue(release)
    renderWorkspace()

    await user.click(screen.getByRole('button', { name: 'Close job aaaaaaaa' }))
    expect(screen.queryByRole('heading', { name: 'Job aaaaaaaa' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Download manifest' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Open job aaaaaaaa' })).toHaveAttribute(
      'aria-expanded',
      'false',
    )

    await user.click(screen.getByRole('button', { name: 'Open job aaaaaaaa' }))
    expect(screen.getByRole('button', { name: 'Download manifest' })).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Close job details' }))
    expect(screen.getByRole('button', { name: 'Open job aaaaaaaa' })).toHaveFocus()
    expect(release.cancel).not.toHaveBeenCalled()
    expect(release.startNew).not.toHaveBeenCalled()
  })

  it('keeps running-job safeguards when collapsed and opens a different job without accumulating panes', async () => {
    const user = userEvent.setup()
    const otherJob = 'd'.repeat(32)
    const release = makeRelease({
      activeJobId: JOB_ID,
      status: { data: statusOf('running') },
      jobs: {
        data: {
          pages: [
            {
              items: [
                { jobId: JOB_ID, state: 'running' },
                { jobId: otherJob, state: 'running' },
              ],
            },
          ],
        },
      },
    })
    vi.mocked(useRelease).mockReturnValue(release)
    const { rerender } = renderWithQuery(<ReleaseWorkspace datasetId="dataset-1" />)
    await user.click(screen.getByRole('button', { name: 'Close job aaaaaaaa' }))
    expect(screen.getByRole('button', { name: 'Create release' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Open job dddddddd' }))
    expect(release.selectJob).toHaveBeenCalledWith(otherJob)

    vi.mocked(useRelease).mockReturnValue(
      makeRelease({
        ...release,
        activeJobId: otherJob,
        status: { ...release.status, data: statusOf('running', { jobId: otherJob }) },
      }),
    )
    rerender(<ReleaseWorkspace datasetId="dataset-1" />)
    expect(screen.getByRole('heading', { name: 'Job dddddddd' })).toBeVisible()
    expect(screen.queryByRole('heading', { name: 'Job aaaaaaaa' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Close job dddddddd' })).toHaveAttribute(
      'aria-expanded',
      'true',
    )
    expect(screen.getByRole('button', { name: 'Open job aaaaaaaa' })).toHaveAttribute(
      'aria-expanded',
      'false',
    )
    expect(release.cancel).not.toHaveBeenCalled()
    expect(release.startNew).not.toHaveBeenCalled()
  })

  it('offers lookup and discard for a pending submission and blocks creation', async () => {
    const user = userEvent.setup()
    const refetch = vi.fn()
    const startNew = vi.fn()
    vi.mocked(useRelease).mockReturnValue(
      makeRelease({
        hasPendingSubmission: true,
        pendingLookup: { isError: false, isFetching: false, refetch },
        startNew,
      }),
    )
    renderWorkspace()
    expect(screen.getByRole('button', { name: 'Create release' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Look up submission' }))
    expect(refetch).toHaveBeenCalledOnce()
    await user.click(screen.getByRole('button', { name: 'Discard submission' }))
    expect(startNew).toHaveBeenCalledOnce()
  })

  it.each(['sealed', 'committing', 'cancelling'] as const)(
    'recovers a %s job and renders counters and capacity',
    async (state) => {
      const user = userEvent.setup()
      const hook = makeRelease({
        activeJobId: JOB_ID,
        status: {
          data: statusOf(state, {
            cancelRequested: true,
            effectsStopped: true,
            destinationCurrentBytes: 2048,
            destinationPeakBytes: -1,
            scratchBytes: Number.NaN,
            ioCounters: { read_bytes: 17 },
          }),
          refetch: vi.fn(),
        },
      })
      vi.mocked(useRelease).mockReturnValue(hook)
      renderWorkspace()
      expect(screen.getByRole('status')).toHaveTextContent('Cancellation requested.')
      expect(screen.getByText('2.0 KiB')).toBeVisible()
      expect(screen.getAllByText('Unknown')).toHaveLength(2)
      expect(screen.getByText('Yes')).toBeVisible()
      expect(screen.getByRole('list', { name: 'I/O counters' })).toHaveTextContent('read_bytes17')
      expect(screen.getByRole('button', { name: 'Cancel job' })).toBeDisabled()
      await user.click(screen.getByRole('button', { name: 'Refresh status' }))
      expect(hook.status.refetch).toHaveBeenCalledOnce()
      await user.click(screen.getByRole('button', { name: 'Recover job' }))
      expect(hook.recover).toHaveBeenCalledOnce()
    },
  )

  it('offers a cleanup retry for a failed cancellation and disables it while recovering', async () => {
    const user = userEvent.setup()
    const recover = vi.fn().mockResolvedValue(undefined)
    const status = {
      data: statusOf('cancelling', { cancelRequested: true, cleanupState: 'failed' }),
      isError: false,
      isFetching: false,
      error: null,
      refetch: vi.fn(),
    }
    vi.mocked(useRelease).mockReturnValue(makeRelease({ activeJobId: JOB_ID, status, recover }))
    const { rerender } = renderWithQuery(<ReleaseWorkspace datasetId="dataset-1" />)
    const retry = screen.getByRole('button', { name: 'Retry cleanup' })
    expect(screen.getByText(/Cleanup failed\./)).toBeVisible()
    await user.click(retry)
    expect(recover).toHaveBeenCalledOnce()

    vi.mocked(useRelease).mockReturnValue(
      makeRelease({ activeJobId: JOB_ID, status, recover, isRecovering: true }),
    )
    rerender(<ReleaseWorkspace datasetId="dataset-1" />)
    expect(screen.getByRole('button', { name: 'Retry cleanup' })).toBeDisabled()
  })

  it('shows a fixed safe error when recovery is denied and keeps the job', async () => {
    const user = userEvent.setup()
    const startNew = vi.fn()
    const recover = vi
      .fn()
      .mockRejectedValue(new ReleaseRequestError('private provider detail', 'forbidden', 403))
    vi.mocked(useRelease).mockReturnValue(
      makeRelease({
        activeJobId: JOB_ID,
        status: {
          data: statusOf('cancelling', { cancelRequested: true, cleanupState: 'failed' }),
          isError: false,
          isFetching: false,
          error: null,
          refetch: vi.fn(),
        },
        recover,
        startNew,
      }),
    )
    renderWorkspace()
    await user.click(screen.getByRole('button', { name: 'Retry cleanup' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Release request failed: forbidden.')
    expect(alert).not.toHaveTextContent('private provider detail')
    expect(screen.getByRole('heading', { name: 'Job aaaaaaaa' })).toBeVisible()
    expect(startNew).not.toHaveBeenCalled()
  })

  it.each([
    [new Error('private provider detail'), 'Release request failed.'],
    [
      new ReleaseRequestError('private provider detail', 'conflict', 409),
      'Release request failed: conflict.',
    ],
  ])('sanitizes a rejected cancellation', async (error, message) => {
    vi.mocked(useRelease).mockReturnValue(
      makeRelease({
        activeJobId: JOB_ID,
        status: { data: statusOf('running') },
        cancel: vi.fn().mockRejectedValue(error),
      }),
    )
    renderWorkspace()
    await userEvent.setup().click(screen.getByRole('button', { name: 'Cancel job' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(message as string)
    expect(screen.queryByText(/private provider detail/)).not.toBeInTheDocument()
  })

  it.each([
    ['manifest', { source_revision: 'r1' }, 'json'],
    ['inventory', { objects: [] }, 'json'],
    ['receipt', { verified: true }, 'json'],
    ['report', 'verified', 'txt'],
  ])(
    'downloads verified %s content and releases its object URL',
    async (name, content, extension) => {
      const createObjectURL = vi.fn().mockReturnValue('blob:artifact')
      const revokeObjectURL = vi.fn()
      vi.stubGlobal(
        'URL',
        Object.assign(class extends URL {}, { createObjectURL, revokeObjectURL }),
      )
      const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
      vi.mocked(getReleaseArtifact).mockResolvedValue({ name, content } as never)
      vi.mocked(useRelease).mockReturnValue(
        makeRelease({
          activeJobId: JOB_ID,
          status: { data: statusOf('committed') },
          result: { data: { schema: 'curated-release-api/v1', result: {}, artifacts: [name] } },
        }),
      )
      renderWorkspace()
      await userEvent.setup().click(screen.getByRole('button', { name: `Download ${name}` }))
      expect(getReleaseArtifact).toHaveBeenCalledWith(JOB_ID, name)
      await waitFor(() => expect(click).toHaveBeenCalledOnce())
      const anchor = click.mock.instances[0] as HTMLAnchorElement
      expect(anchor.download).toBe(`${name}.${extension}`)
      expect(anchor.href).toBe('blob:artifact')
      expect(createObjectURL.mock.calls[0][0].type).toBe(
        extension === 'json' ? 'application/json' : 'text/plain',
      )
      expect(document.body.contains(anchor)).toBe(false)
      await waitFor(() => expect(revokeObjectURL).toHaveBeenCalledWith('blob:artifact'))
    },
  )

  it.each([
    [{ error: new Error('private') }, 'Dataset access could not be verified.'],
    [{ data: undefined }, 'Checking dataset access...'],
  ])('does not open a workflow until principal access is established', (principal, message) => {
    vi.mocked(usePrincipalContext).mockReturnValue(principal as never)
    renderWorkspace()
    expect(screen.getByText(message as string)).toBeVisible()
    expect(useRelease).not.toHaveBeenCalled()
  })

  it.each([false, true])(
    'restores dialog focus to an enabled trigger or the workspace',
    async (blocked) => {
      const hook = makeRelease()
      vi.mocked(useRelease).mockReturnValue(hook)
      const { rerender } = renderWithQuery(<ReleaseWorkspace datasetId="dataset-1" />)
      await userEvent.setup().click(screen.getByRole('button', { name: 'Create release' }))
      expect(dialogSpy.props?.open).toBe(true)
      if (blocked) {
        vi.mocked(useRelease).mockReturnValue(makeRelease({ hasPendingSubmission: true }))
        rerender(<ReleaseWorkspace datasetId="dataset-1" />)
      }
      const event = new Event('close', { cancelable: true })
      const close = dialogSpy.props?.onCloseAutoFocus as (event: Event) => void
      close(event)
      expect(event.defaultPrevented).toBe(true)
      expect(
        blocked
          ? screen.getByRole('region', { name: 'Releases' })
          : screen.getByRole('button', { name: 'Create release' }),
      ).toHaveFocus()
    },
  )

  it('clears an inaccessible job locally and shows independent loading failures', async () => {
    const hook = makeRelease({
      activeJobId: JOB_ID,
      status: { isError: true },
      jobs: { isError: true },
      inventory: { isError: true },
    })
    vi.mocked(useRelease).mockReturnValue(hook)
    vi.mocked(listSourceReleases).mockRejectedValueOnce(new Error('private'))
    renderWorkspace()
    expect(screen.getByRole('status')).toHaveTextContent('Job status could not be loaded.')
    expect(screen.getByText('Release jobs could not be loaded.')).toBeVisible()
    expect(screen.getByText(/Source inventory could not be loaded/)).toBeVisible()
    expect(await screen.findByText('Source releases could not be loaded.')).toBeVisible()
    await userEvent.setup().click(screen.getByRole('button', { name: 'Clear saved job' }))
    expect(hook.startNew).toHaveBeenCalledOnce()
    expect(hook.cancel).not.toHaveBeenCalled()
  })

  describe('retained episode media', () => {
    beforeEach(() => {
      vi.stubGlobal(
        'URL',
        Object.assign(class extends URL {}, {
          createObjectURL: vi.fn().mockReturnValue('blob:camera'),
          revokeObjectURL: vi.fn(),
        }),
      )
      vi.mocked(listSourceReleases).mockResolvedValue({
        items: [
          {
            handle: HANDLE,
            datasetName: 'Release A',
            packageId: 'pkg',
            manifestSha256: 'c'.repeat(64),
          },
        ],
      })
      vi.mocked(getSourceRelease).mockResolvedValue({
        handle: HANDLE,
        schema: 'v1',
        totalEpisodes: 2,
        annotations: [],
        labels: { episodes: { '0': ['saved'] } },
        frozenEvidence: null,
      })
      vi.mocked(listSourceEpisodes)
        .mockResolvedValueOnce({
          items: [
            {
              episodeIndex: 0,
              episodeId: '0',
              fromTimestamp: 0,
              toTimestamp: 3,
              instruction: 'Pick plate',
              fps: 30,
              length: 90,
              cameras: ['wrist', 'front'],
            },
          ],
          total: 2,
          nextCursor: 1,
        })
        .mockResolvedValue({
          items: [
            {
              episodeIndex: 1,
              episodeId: '1',
              fromTimestamp: 0,
              toTimestamp: 2,
              instruction: 'Place plate',
              fps: 30,
              length: 60,
              cameras: [],
            },
          ],
          total: 2,
          nextCursor: null,
        })
    })

    it('paginates episodes, switches cameras, and revokes media when selection changes', async () => {
      const user = userEvent.setup()
      vi.mocked(fetchSourceEpisodeMedia).mockResolvedValue(new Blob(['video']))
      const { unmount } = renderWithQuery(<ReleaseWorkspace datasetId="dataset-1" />)
      await user.click(await screen.findByRole('button', { name: /Release A/ }))
      await user.click(await screen.findByRole('button', { name: /Episode 0/ }))
      expect(await screen.findByLabelText('Camera wrist for episode 0')).toHaveAttribute(
        'src',
        'blob:camera',
      )
      expect(fetchSourceEpisodeMedia).toHaveBeenCalledWith('dataset-1', HANDLE, 0, 'wrist')
      await user.click(screen.getByRole('button', { name: 'front' }))
      expect(await screen.findByLabelText('Camera front for episode 0')).toBeVisible()
      expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:camera')
      await user.click(screen.getByRole('button', { name: 'Load more episodes' }))
      await user.click(await screen.findByRole('button', { name: /Episode 1/ }))
      expect(screen.queryByRole('group', { name: 'Cameras' })).not.toBeInTheDocument()
      expect(listSourceEpisodes).toHaveBeenLastCalledWith('dataset-1', HANDLE, {
        cursor: 1,
        limit: 50,
      })
      unmount()
      expect(URL.revokeObjectURL).toHaveBeenCalledTimes(2)
    })

    it('toggles release and episode panes and restores focus from close controls', async () => {
      const user = userEvent.setup()
      vi.mocked(fetchSourceEpisodeMedia).mockResolvedValue(new Blob(['video']))
      renderWorkspace()
      const releaseButton = await screen.findByRole('button', { name: /Release A/ })
      await user.click(releaseButton)
      const episodeButton = await screen.findByRole('button', { name: /Episode 0/ })
      await user.click(episodeButton)
      expect(await screen.findByLabelText('Camera wrist for episode 0')).toBeVisible()

      await user.click(episodeButton)
      expect(screen.queryByLabelText('Camera wrist for episode 0')).toBeNull()
      expect(episodeButton).toHaveAttribute('aria-expanded', 'false')
      expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:camera')
      await user.click(episodeButton)
      expect(await screen.findByLabelText('Camera wrist for episode 0')).toBeVisible()
      await user.click(screen.getByRole('button', { name: 'Close episode details' }))
      expect(episodeButton).toHaveFocus()
      expect(screen.queryByLabelText('Camera wrist for episode 0')).toBeNull()

      await user.click(releaseButton)
      expect(screen.queryByRole('heading', { name: 'Release details (read-only)' })).toBeNull()
      expect(releaseButton).toHaveAttribute('aria-expanded', 'false')
      await user.click(releaseButton)
      expect(
        await screen.findByRole('heading', { name: 'Release details (read-only)' }),
      ).toBeVisible()
      await user.click(screen.getByRole('button', { name: 'Close release details' }))
      expect(releaseButton).toHaveFocus()
      expect(screen.queryByRole('heading', { name: 'Release details (read-only)' })).toBeNull()
    })

    it('reports media failure without displaying provider details', async () => {
      vi.mocked(fetchSourceEpisodeMedia).mockRejectedValue(new Error('private media failure'))
      renderWorkspace()
      const user = userEvent.setup()
      await user.click(await screen.findByRole('button', { name: /Release A/ }))
      await user.click(await screen.findByRole('button', { name: /Episode 0/ }))
      expect(await screen.findByText('Media unavailable for this camera.')).toBeVisible()
      expect(screen.queryByText(/private media failure/)).not.toBeInTheDocument()
    })

    it('ignores a media response arriving after unmount', async () => {
      let finish!: (blob: Blob) => void
      vi.mocked(fetchSourceEpisodeMedia).mockReturnValue(
        new Promise((resolve) => {
          finish = resolve
        }),
      )
      const { unmount } = renderWithQuery(<ReleaseWorkspace datasetId="dataset-1" />)
      const user = userEvent.setup()
      await user.click(await screen.findByRole('button', { name: /Release A/ }))
      await user.click(await screen.findByRole('button', { name: /Episode 0/ }))
      expect(screen.getByText('Loading media...')).toBeVisible()
      unmount()
      finish(new Blob(['video']))
      await Promise.resolve()
      expect(URL.createObjectURL).not.toHaveBeenCalled()
    })
  })
})
