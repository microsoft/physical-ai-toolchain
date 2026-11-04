import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { type ReleasePreview, ReleaseRequestError } from '@/api/releases'
import {
  ReleaseDialog,
  type ReleaseDialogProps,
  type ReleaseItem,
} from '@/components/release/ReleaseDialog'
import { useEpisodeReadiness } from '@/hooks/use-episode-readiness'
import { fetchPrincipalContext } from '@/lib/principal-context'
import { renderWithQuery } from '@/test-utils/render'

vi.mock('@/hooks/use-episode-readiness', () => ({
  useEpisodeReadiness: vi.fn(),
}))

vi.mock('@/lib/principal-context', () => ({
  fetchPrincipalContext: vi.fn(),
}))

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

const items: ReleaseItem[] = [
  {
    episodeIndex: 0,
    annotation: true,
    edits: false,
    labels: ['pick'],
    resources: [{ resourceId: 'cam-a', revisionNo: 1 }],
  },
  { episodeIndex: 1, annotation: false, edits: true, labels: null, resources: [] },
  { episodeIndex: 2, annotation: true, edits: false, labels: null, resources: null },
  { episodeIndex: 3, annotation: true, edits: true, labels: ['place'], resources: [] },
]

function makePreview(overrides: Partial<ReleasePreview['summary']> = {}): ReleasePreview {
  return {
    schema: 'v1',
    digest: 'digest-1',
    reservedBytes: 2048,
    summary: {
      processingDigest: 'proc-1',
      blocking: [],
      warnings: [],
      actions: ['encode'],
      destinationKind: 'local',
      ...overrides,
    },
  }
}

function renderDialog(overrides: Partial<ReleaseDialogProps> = {}) {
  const props: ReleaseDialogProps = {
    datasetId: 'dataset-1',
    open: true,
    onOpenChange: vi.fn(),
    items,
    inventoryReady: true,
    destinations: ['local-disk'],
    onPreview: vi.fn().mockResolvedValue(makePreview()),
    onSubmit: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  }
  renderWithQuery(<ReleaseDialog {...props} />)
  return props
}

function episodeList() {
  return screen.getByRole('list', { name: 'Episodes' })
}

async function fillRequiredFields(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText('Release name'), 'Release A')
}

describe('ReleaseDialog', () => {
  beforeEach(() => {
    vi.mocked(useEpisodeReadiness).mockReturnValue({ ready: true, reason: null })
    vi.mocked(fetchPrincipalContext).mockResolvedValue({ scopeId: 'scope-1', authMode: 'local' })
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('renders the fixed target and keeps release disabled until episodes are selected', () => {
    renderDialog()

    expect(screen.getByText(/Target: v3/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /submit release/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /preview release/i })).toBeDisabled()
  })

  it('preserves keyboard focus when selected-only rows disappear', async () => {
    const user = userEvent.setup()
    renderDialog()
    await user.click(screen.getByRole('checkbox', { name: 'Episode 0' }))
    await user.click(screen.getByRole('checkbox', { name: 'Episode 1' }))
    await user.click(screen.getByRole('checkbox', { name: 'Episode 3' }))
    await user.click(screen.getByRole('checkbox', { name: 'Selected only' }))
    screen.getByRole('checkbox', { name: 'Episode 1' }).focus()
    await user.keyboard(' ')
    expect(screen.getByRole('checkbox', { name: 'Episode 3' })).toHaveFocus()
    await user.keyboard(' ')
    expect(screen.getByRole('checkbox', { name: 'Episode 0' })).toHaveFocus()
    await user.keyboard(' ')
    expect(screen.getByRole('checkbox', { name: 'Selected only' })).toHaveFocus()
    expect(screen.getByText('No selected episodes match these filters.')).toBeVisible()
  })

  it('filters actual labels with checkboxes and reports selections hidden by filters', async () => {
    const user = userEvent.setup()
    const props = renderDialog()
    await fillRequiredFields(user)
    await user.click(screen.getByRole('checkbox', { name: 'Episode 1' }))
    await user.click(screen.getByRole('checkbox', { name: 'pick (1)' }))
    expect(within(episodeList()).getAllByRole('listitem')).toHaveLength(1)
    expect(
      screen.getByText('1 of 4 selected; 1 shown; 1 selected outside this view.'),
    ).toBeVisible()
    await user.click(screen.getByRole('checkbox', { name: 'Select shown' }))
    expect(
      screen.getByText('2 of 4 selected; 1 shown; 1 selected outside this view.'),
    ).toBeVisible()
    await user.click(screen.getByRole('checkbox', { name: 'place (1)' }))
    expect(within(episodeList()).getAllByRole('listitem')).toHaveLength(2)
    expect(screen.getByRole('checkbox', { name: 'Select shown' })).toBePartiallyChecked()
    await user.click(screen.getByRole('checkbox', { name: 'Select shown' }))
    await user.click(screen.getByRole('checkbox', { name: 'Select shown' }))
    await user.click(screen.getByRole('button', { name: /preview release/i }))
    await waitFor(() =>
      expect(props.onPreview).toHaveBeenCalledWith(
        expect.objectContaining({ episodeIndices: [1] }),
      ),
    )
  })

  it('makes selected-only and empty filter states explicit without changing selection', async () => {
    const user = userEvent.setup()
    renderDialog()
    await user.click(screen.getByRole('checkbox', { name: 'Selected only' }))
    expect(screen.getByText('No selected episodes match these filters.')).toBeVisible()
    await user.click(screen.getByRole('checkbox', { name: 'Selected only' }))
    await user.click(screen.getByRole('checkbox', { name: 'Episode 0' }))
    await user.type(screen.getByRole('searchbox', { name: 'Search episodes' }), 'place')
    await user.click(screen.getByRole('checkbox', { name: 'Selected only' }))
    expect(screen.getByText('No selected episodes match these filters.')).toBeVisible()
    expect(
      screen.getByText('1 of 4 selected; 0 shown; 1 selected outside this view.'),
    ).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Reset filters' }))
    expect(screen.getByRole('checkbox', { name: 'Episode 0' })).toBeChecked()
    expect(within(episodeList()).getAllByRole('listitem')).toHaveLength(1)
  })

  it('counts unique available episodes after inventory refresh and submits only those rows', async () => {
    const user = userEvent.setup()
    const props = {
      datasetId: 'dataset-1',
      open: true,
      onOpenChange: vi.fn(),
      items,
      inventoryReady: true,
      destinations: ['local'],
      onPreview: vi.fn().mockResolvedValue(makePreview()),
      onSubmit: vi.fn(),
    }
    const { rerender } = renderWithQuery(<ReleaseDialog {...props} />)
    await fillRequiredFields(user)
    await user.click(screen.getByRole('button', { name: 'Select all' }))
    rerender(<ReleaseDialog {...props} items={[items[0], items[0], items[3]]} />)
    expect(
      screen.getByText('2 of 2 selected; 2 shown; 0 selected outside this view.'),
    ).toBeVisible()
    expect(within(episodeList()).getAllByRole('listitem')).toHaveLength(2)
    await user.click(screen.getByRole('button', { name: /preview release/i }))
    await waitFor(() =>
      expect(props.onPreview).toHaveBeenCalledWith(
        expect.objectContaining({ episodeIndices: [0, 3] }),
      ),
    )
  })

  it('updates destination and description and clears a filtered selection', async () => {
    const user = userEvent.setup()
    const props = renderDialog({ destinations: ['local-disk', 'archive'] })
    await fillRequiredFields(user)
    await user.selectOptions(screen.getByLabelText('Destination'), 'archive')
    await user.type(screen.getByLabelText('Description'), ' Saved examples ')
    await user.selectOptions(screen.getByLabelText('Labels'), 'yes')
    await user.selectOptions(screen.getByLabelText('Resources'), 'yes')
    await user.click(screen.getByRole('button', { name: 'Select all' }))
    await user.click(screen.getByRole('button', { name: 'Clear selection' }))
    expect(screen.getByRole('button', { name: /preview release/i })).toBeDisabled()
    await user.click(screen.getByRole('checkbox', { name: 'Episode 0' }))
    await user.click(screen.getByRole('button', { name: /preview release/i }))
    await waitFor(() =>
      expect(props.onPreview).toHaveBeenCalledWith(
        expect.objectContaining({
          destination: 'archive',
          description: 'Saved examples',
          episodeIndices: [0],
        }),
      ),
    )
  })

  it('emphasizes the current action and announces the remaining submission requirement', async () => {
    const user = userEvent.setup()
    renderDialog({
      onPreview: vi.fn().mockResolvedValue(makePreview({ actions: [], destinationKind: 'blob' })),
    })
    await fillRequiredFields(user)
    await user.click(screen.getByRole('checkbox', { name: 'Episode 0' }))
    await user.click(screen.getByRole('checkbox', { name: /I declare/i }))
    const preview = screen.getByRole('button', { name: /preview release/i })
    const submit = screen.getByRole('button', { name: /submit release/i })
    expect(preview).toHaveClass('bg-primary')
    expect(submit).toBeDisabled()
    expect(screen.getByRole('status')).toHaveTextContent('Preview required before submission.')

    await user.click(preview)

    await waitFor(() => expect(submit).toBeEnabled())
    expect(submit).toHaveClass('bg-primary')
    expect(preview).not.toHaveClass('bg-primary')
    expect(screen.getByRole('status')).toHaveTextContent('Ready to submit.')
  })

  it.each([
    [new Error('private transport detail'), 'Release request failed.'],
    [
      new ReleaseRequestError('private provider detail', 'stale_preview', 409),
      'Release request failed: stale_preview.',
    ],
  ])('reports preview failure without exposing provider details', async (error, message) => {
    const user = userEvent.setup()
    renderDialog({ onPreview: vi.fn().mockRejectedValue(error) })
    await fillRequiredFields(user)
    await user.click(screen.getByRole('checkbox', { name: 'Episode 0' }))
    await user.click(screen.getByRole('button', { name: /preview release/i }))
    expect(await screen.findByText(message)).toBeVisible()
    expect(screen.queryByText(/private .* detail/)).toBeNull()
    expect(screen.getByRole('button', { name: /preview release/i })).toBeEnabled()
  })

  it('allows closing an idle dialog', async () => {
    const user = userEvent.setup()
    const props = renderDialog()
    await user.keyboard('{Escape}')
    expect(props.onOpenChange).toHaveBeenCalledWith(false)
    vi.mocked(props.onOpenChange).mockClear()
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(props.onOpenChange).toHaveBeenCalledWith(false)
  })

  it.each(['stale_preview', 'invalid_release_selection'])(
    'requires a fresh preview after definitive submission rejection: %s',
    async (code) => {
      const user = userEvent.setup()
      const props = renderDialog({
        onPreview: vi.fn().mockResolvedValue(makePreview({ actions: [], destinationKind: 'blob' })),
        onSubmit: vi.fn().mockRejectedValue(new ReleaseRequestError('private detail', code, 409)),
      })
      await fillRequiredFields(user)
      await user.click(screen.getByRole('checkbox', { name: 'Episode 0' }))
      await user.click(screen.getByRole('checkbox', { name: /I declare/i }))
      await user.click(screen.getByRole('button', { name: /preview release/i }))
      await screen.findByText('Reserved bytes')
      await user.click(screen.getByRole('button', { name: /submit release/i }))
      expect(await screen.findByText(`Release request failed: ${code}.`)).toBeVisible()
      expect(screen.queryByText('Reserved bytes')).toBeNull()
      expect(screen.queryByText('private detail')).toBeNull()
      expect(screen.queryByRole('button', { name: /retry submission/i })).toBeNull()
      expect(screen.getByRole('button', { name: /submit release/i })).toBeDisabled()
      expect(screen.getByRole('button', { name: /preview release/i })).toBeEnabled()
      expect(props.onOpenChange).not.toHaveBeenCalled()
    },
  )

  it('keeps an uncertain submission open until explicitly discarded', async () => {
    const user = userEvent.setup()
    const props = renderDialog({
      onPreview: vi
        .fn()
        .mockResolvedValue(
          makePreview({ actions: [], destinationKind: 'blob', availableBytes: -1 }),
        ),
      onSubmit: vi.fn().mockRejectedValue(new Error('connection lost')),
    })
    await fillRequiredFields(user)
    await user.click(screen.getByRole('checkbox', { name: 'Episode 0' }))
    await user.click(screen.getByRole('checkbox', { name: /I declare/i }))
    await user.click(screen.getByRole('button', { name: /preview release/i }))
    await screen.findByText('Reserved bytes')
    await user.click(screen.getByRole('button', { name: /submit release/i }))
    const discard = await screen.findByRole('button', { name: /discard and start over/i })
    await user.keyboard('{Escape}')
    expect(props.onOpenChange).not.toHaveBeenCalled()
    await user.click(discard)
    expect(screen.getByLabelText('Release name')).toHaveValue('')
    expect(screen.queryByRole('button', { name: /retry submission/i })).toBeNull()
  })

  it('combines filters and appends shown episodes in selection order', async () => {
    const user = userEvent.setup()
    renderDialog()

    await user.selectOptions(screen.getByLabelText('Annotation'), 'yes')
    await user.click(screen.getByRole('checkbox', { name: 'Select shown' }))
    await user.selectOptions(screen.getByLabelText('Annotation'), 'any')
    await user.selectOptions(screen.getByLabelText('Edits'), 'yes')
    await user.click(screen.getByRole('checkbox', { name: 'Select shown' }))

    expect(screen.getByRole('checkbox', { name: 'Episode 1' })).toBeChecked()
    expect(screen.getByText('#4')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Reset filters' }))
    await user.click(screen.getByRole('checkbox', { name: 'Selected only' }))
    const rows = within(episodeList()).getAllByRole('listitem')
    expect(rows).toHaveLength(4)
    expect(rows[0]).toHaveTextContent('Episode 0')
    expect(rows[3]).toHaveTextContent('Episode 1')
  })

  it('disables select all while the full inventory is not loaded', () => {
    renderDialog({ inventoryReady: false })

    expect(screen.getByRole('button', { name: 'Select all' })).toBeDisabled()
  })

  it('blocks preview when a selected episode is not ready', async () => {
    const user = userEvent.setup()
    vi.mocked(useEpisodeReadiness).mockReturnValue({
      ready: false,
      reason: 'Episode 2: unsaved edits',
    })
    renderDialog()
    await fillRequiredFields(user)

    await user.click(screen.getByRole('checkbox', { name: 'Episode 1' }))
    await user.click(screen.getByRole('checkbox', { name: 'Episode 2' }))

    expect(useEpisodeReadiness).toHaveBeenLastCalledWith('dataset-1', [1, 2], true)
    expect(screen.getByText('Episode 2: unsaved edits')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /preview release/i })).toBeDisabled()
  })

  it('invalidates the preview and acknowledgments when the selection changes', async () => {
    const user = userEvent.setup()
    renderDialog()
    await fillRequiredFields(user)
    await user.click(screen.getByRole('checkbox', { name: 'Episode 0' }))
    await user.click(screen.getByRole('button', { name: /preview release/i }))
    expect(await screen.findByText('Reserved bytes')).toBeInTheDocument()

    await user.click(screen.getByRole('checkbox', { name: 'Episode 1' }))

    expect(screen.queryByText('Reserved bytes')).toBeNull()
    expect(screen.getByRole('button', { name: /submit release/i })).toBeDisabled()
  })

  it('blocks submission when the preview reports blocking entries', async () => {
    const user = userEvent.setup()
    renderDialog({
      onPreview: vi.fn().mockResolvedValue(makePreview({ blocking: ['Missing frames'] })),
    })
    await fillRequiredFields(user)
    await user.click(screen.getByRole('checkbox', { name: 'Episode 0' }))
    await user.click(screen.getByRole('checkbox', { name: /I declare/i }))
    await user.click(screen.getByRole('button', { name: /preview release/i }))

    expect(await screen.findByText('Missing frames')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /submit release/i })).toBeDisabled()
  })

  it.each(['local', 'blob'])(
    'shows backend capacity and scopes disk acknowledgment for %s',
    async (destinationKind) => {
      const user = userEvent.setup()
      renderDialog({
        onPreview: vi
          .fn()
          .mockResolvedValue(makePreview({ destinationKind, availableBytes: 4096 })),
      })
      await fillRequiredFields(user)
      await user.click(screen.getByRole('checkbox', { name: 'Episode 0' }))
      await user.click(screen.getByRole('button', { name: /preview release/i }))
      await screen.findByText('Reserved bytes')
      if (destinationKind === 'local') {
        expect(screen.getByText('Backend available space')).toBeVisible()
        expect(screen.getByText('4.0 KiB')).toBeVisible()
        expect(screen.getByRole('checkbox', { name: /local disk/i })).toBeVisible()
      } else {
        expect(screen.queryByRole('checkbox', { name: /local disk/i })).toBeNull()
      }
    },
  )

  it('submits the previewed body with snake_case resources and retries with the same key', async () => {
    const user = userEvent.setup()
    const onSubmit = vi
      .fn()
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce(undefined)
    const props = renderDialog({ onSubmit })
    await fillRequiredFields(user)
    await user.click(screen.getByRole('checkbox', { name: 'Episode 0' }))
    await user.click(screen.getByRole('checkbox', { name: /I declare/i }))
    await user.click(screen.getByRole('button', { name: /preview release/i }))
    await user.click(await screen.findByRole('checkbox', { name: /processing action/i }))
    await user.click(screen.getByRole('checkbox', { name: /local disk/i }))
    await user.click(screen.getByRole('button', { name: /submit release/i }))

    const retry = await screen.findByRole('button', { name: /retry submission/i })
    expect(screen.getByRole('button', { name: /discard and start over/i })).toBeInTheDocument()
    await user.click(retry)

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(2))
    const [firstRequest, firstSubmission] = onSubmit.mock.calls[0]
    const [secondRequest, secondSubmission] = onSubmit.mock.calls[1]
    expect(firstRequest).toEqual(secondRequest)
    expect(firstRequest).toEqual(
      expect.objectContaining({
        datasetId: 'dataset-1',
        episodeIndices: [0],
        datasetName: 'Release A',
        finalized: true,
        resources: [{ resource_id: 'cam-a', revision_no: 1 }],
      }),
    )
    expect(firstSubmission).toEqual({
      previewDigest: 'digest-1',
      processingDigest: 'proc-1',
      localDiskAcknowledged: true,
      idempotencyKey: expect.stringMatching(UUID_PATTERN),
    })
    expect(secondSubmission.idempotencyKey).toBe(firstSubmission.idempotencyKey)
    expect(props.onOpenChange).toHaveBeenCalledWith(false)
  })
})

describe('ReleaseDialog selection evidence', () => {
  const evidenceItems: ReleaseItem[] = [
    {
      episodeIndex: 10,
      annotation: true,
      edits: false,
      labels: null,
      resources: [],
      provenance: { origins: ['camera'] },
      judge: { status: 'saved_analysis', freshness: 'unknown', confidence: 0.4, outcome: 'fail' },
    },
    {
      episodeIndex: 11,
      annotation: false,
      edits: false,
      labels: ['pick'],
      resources: [],
      provenance: { origins: ['teleop', 'camera'] },
      judge: null,
    },
    {
      episodeIndex: 12,
      annotation: false,
      edits: false,
      labels: null,
      resources: null,
      provenance: null,
      judge: null,
    },
  ]

  beforeEach(() => {
    vi.mocked(useEpisodeReadiness).mockReturnValue({ ready: true, reason: null })
    vi.mocked(fetchPrincipalContext).mockResolvedValue({ scopeId: 'scope-1', authMode: 'local' })
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('shows provenance origins and judge freshness as reported on each row', () => {
    renderDialog({ items: evidenceItems })

    expect(screen.getByRole('checkbox', { name: 'Episode 10' }).closest('li')).toHaveTextContent(
      'origin: camera',
    )
    expect(screen.getByRole('checkbox', { name: 'Episode 10' }).closest('li')).toHaveTextContent(
      'judge saved_analysis, freshness unknown',
    )
    expect(screen.getByRole('checkbox', { name: 'Episode 11' }).closest('li')).toHaveTextContent(
      'origin: teleop, camera',
    )
    expect(
      screen.getByRole('checkbox', { name: 'Episode 12' }).closest('li'),
    ).not.toHaveTextContent('origin')
  })

  it('summarizes selected evidence without inferring judge freshness', async () => {
    const user = userEvent.setup()
    renderDialog({ items: evidenceItems })

    await user.click(screen.getByRole('checkbox', { name: 'Episode 10' }))
    await user.click(screen.getByRole('checkbox', { name: 'Episode 11' }))

    expect(screen.getByText('Provenance origins: camera 2, teleop 1')).toBeInTheDocument()
    expect(screen.getByText('Judge analyses: 1; freshness unknown: 1')).toBeInTheDocument()
  })

  it('filters by provenance origin across the full population', async () => {
    const user = userEvent.setup()
    renderDialog({ items: evidenceItems })

    await user.selectOptions(screen.getByLabelText('Provenance origin'), 'teleop')

    const rows = within(episodeList()).getAllByRole('listitem')
    expect(rows).toHaveLength(1)
    expect(rows[0]).toHaveTextContent('Episode 11')
  })

  it('filters by judge analysis presence', async () => {
    const user = userEvent.setup()
    renderDialog({ items: evidenceItems })

    await user.selectOptions(screen.getByLabelText('Judge analysis'), 'yes')

    const rows = within(episodeList()).getAllByRole('listitem')
    expect(rows).toHaveLength(1)
    expect(rows[0]).toHaveTextContent('Episode 10')
  })

  it('does not gate selection or preview on an adverse judge outcome', async () => {
    const user = userEvent.setup()
    renderDialog({ items: evidenceItems })
    await user.type(screen.getByLabelText('Release name'), 'Release B')

    await user.click(screen.getByRole('checkbox', { name: 'Episode 10' }))

    expect(screen.getByRole('checkbox', { name: 'Episode 10' })).toBeChecked()
    expect(screen.getByRole('button', { name: /preview release/i })).toBeEnabled()
  })
})
