import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { CurriculumGenerator } from '@/components/curriculum/CurriculumGenerator'
import type { EpisodePreviewItem } from '@/components/curriculum/CurriculumPreview'
import type { FilterCondition } from '@/components/curriculum/FilterBuilder'

const toastMock = vi.fn()

vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: toastMock, toasts: [], dismiss: vi.fn() }),
}))

vi.mock('@/components/curriculum/FilterBuilder', () => ({
  FilterBuilder: ({
    conditions,
    onChange,
  }: {
    conditions: FilterCondition[]
    onChange: (next: FilterCondition[]) => void
  }) => {
    const add = (c: FilterCondition) => onChange([...conditions, c])
    return (
      <div data-testid="filter-builder">
        <span data-testid="filter-count">{conditions.length}</span>
        <button
          onClick={() =>
            add({ id: 'eq', field: 'task_completion_rating', operator: 'equals', value: 5 })
          }
        >
          add-eq-rating-5
        </button>
        <button
          onClick={() =>
            add({ id: 'ne', field: 'task_completion_rating', operator: 'not_equals', value: 5 })
          }
        >
          add-ne-rating-5
        </button>
        <button
          onClick={() =>
            add({
              id: 'gt',
              field: 'trajectory_quality_score',
              operator: 'greater_than',
              value: 0.5,
            })
          }
        >
          add-gt-quality-0.5
        </button>
        <button
          onClick={() =>
            add({
              id: 'lt',
              field: 'trajectory_quality_score',
              operator: 'less_than',
              value: 0.5,
            })
          }
        >
          add-lt-quality-0.5
        </button>
        <button
          onClick={() =>
            add({
              id: 'ge',
              field: 'trajectory_quality_score',
              operator: 'greater_or_equal',
              value: 0.5,
            })
          }
        >
          add-ge-quality-0.5
        </button>
        <button
          onClick={() =>
            add({
              id: 'le',
              field: 'trajectory_quality_score',
              operator: 'less_or_equal',
              value: 0.5,
            })
          }
        >
          add-le-quality-0.5
        </button>
        <button
          onClick={() =>
            add({ id: 'true', field: 'has_anomalies', operator: 'is_true', value: true })
          }
        >
          add-anomalies-true
        </button>
        <button
          onClick={() =>
            add({ id: 'false', field: 'has_issues', operator: 'is_false', value: false })
          }
        >
          add-issues-false
        </button>
        <button
          onClick={() =>
            add({
              id: 'unknown-field',
              field: 'nonexistent_field' as unknown as FilterCondition['field'],
              operator: 'equals',
              value: 1,
            })
          }
        >
          add-unknown-field
        </button>
        <button
          onClick={() =>
            add({
              id: 'unknown-op',
              field: 'task_completion_rating',
              operator: 'fuzzy_match' as unknown as FilterCondition['operator'],
              value: 5,
            })
          }
        >
          add-unknown-operator
        </button>
        <button
          onClick={() =>
            add({
              id: 'gt-bool',
              field: 'has_anomalies',
              operator: 'greater_than',
              value: 1,
            })
          }
        >
          add-gt-against-bool
        </button>
      </div>
    )
  },
}))

vi.mock('@/components/curriculum/CurriculumPreview', () => ({
  CurriculumPreview: ({
    episodes,
    isLoading,
    totalCount,
    previewLimit,
  }: {
    episodes: EpisodePreviewItem[]
    isLoading?: boolean
    totalCount?: number
    previewLimit?: number
  }) => (
    <div data-testid="preview-panel">
      <span data-testid="preview-count">{episodes.length}</span>
      <span data-testid="preview-loading">{String(Boolean(isLoading))}</span>
      <span data-testid="preview-total">{String(totalCount)}</span>
      <span data-testid="preview-limit">{String(previewLimit)}</span>
    </div>
  ),
}))

vi.mock('@/components/curriculum/ExportPanel', () => ({
  ExportPanel: ({
    episodeCount,
    onExport,
    isExporting,
    disabled,
  }: {
    episodeCount: number
    onExport: (options: { format: string; filename: string }) => Promise<void>
    isExporting?: boolean
    disabled?: boolean
  }) => (
    <div data-testid="export-panel">
      <span data-testid="export-count">{episodeCount}</span>
      <span data-testid="export-disabled">{String(Boolean(disabled))}</span>
      <span data-testid="export-isexporting">{String(Boolean(isExporting))}</span>
      <button onClick={() => void onExport({ format: 'json', filename: 'curriculum' })}>
        trigger-export
      </button>
    </div>
  ),
}))

const sampleEpisodes: EpisodePreviewItem[] = [
  {
    id: 'ep-1',
    episode_id: '0',
    task_completion_rating: 5,
    trajectory_quality_score: 0.9,
    has_anomalies: false,
    has_issues: false,
  },
  {
    id: 'ep-2',
    episode_id: '1',
    task_completion_rating: 3,
    trajectory_quality_score: 0.4,
    has_anomalies: true,
    has_issues: false,
  },
  {
    id: 'ep-3',
    episode_id: '2',
    task_completion_rating: 5,
    trajectory_quality_score: 0.5,
    has_anomalies: false,
    has_issues: true,
  },
]

const baseProps = () => ({
  datasetId: 'test-dataset',
  episodes: sampleEpisodes,
  isLoading: false,
  onSavePreset: vi.fn(),
  onExport: vi.fn().mockResolvedValue(undefined),
})

describe('CurriculumGenerator', () => {
  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
    toastMock.mockReset()
  })

  it('renders header and main panels', () => {
    render(<CurriculumGenerator {...baseProps()} />)
    expect(screen.getByText('Curriculum Generator')).toBeInTheDocument()
    expect(screen.getByTestId('filter-builder')).toBeInTheDocument()
    expect(screen.getByTestId('export-panel')).toBeInTheDocument()
    expect(screen.getByText('Selection Summary')).toBeInTheDocument()
  })

  it('renders default state with no conditions and shows all episodes', () => {
    render(<CurriculumGenerator {...baseProps()} />)
    expect(screen.getByTestId('filter-count')).toHaveTextContent('0')
    expect(screen.getByTestId('export-count')).toHaveTextContent('3')
    expect(screen.getByText('Preview (3)')).toBeInTheDocument()
  })

  it('renders Save Preset button when onSavePreset provided', () => {
    render(<CurriculumGenerator {...baseProps()} />)
    expect(screen.getByRole('button', { name: /save preset/i })).toBeInTheDocument()
  })

  it('does not render Save Preset button when onSavePreset is undefined', () => {
    const props = baseProps()
    render(<CurriculumGenerator {...props} onSavePreset={undefined} />)
    expect(screen.queryByRole('button', { name: /save preset/i })).not.toBeInTheDocument()
  })

  it('disables Save Preset when no conditions', () => {
    render(<CurriculumGenerator {...baseProps()} />)
    const button = screen.getByRole('button', { name: /save preset/i })
    expect(button).toBeDisabled()
  })

  it('enables Save Preset after a condition is added', () => {
    render(<CurriculumGenerator {...baseProps()} />)
    fireEvent.click(screen.getByText('add-eq-rating-5'))
    expect(screen.getByRole('button', { name: /save preset/i })).not.toBeDisabled()
  })

  it('clears all conditions when Clear Filters is clicked', () => {
    render(<CurriculumGenerator {...baseProps()} />)
    fireEvent.click(screen.getByText('add-eq-rating-5'))
    fireEvent.click(screen.getByText('add-gt-quality-0.5'))
    expect(screen.getByTestId('filter-count')).toHaveTextContent('2')

    fireEvent.click(screen.getByRole('button', { name: /clear filters/i }))
    expect(screen.getByTestId('filter-count')).toHaveTextContent('0')
  })

  it('saves preset when prompt returns a name', () => {
    const props = baseProps()
    const promptMock = vi.fn().mockReturnValue('My Preset')
    vi.stubGlobal('prompt', promptMock)
    render(<CurriculumGenerator {...props} />)

    fireEvent.click(screen.getByText('add-eq-rating-5'))
    fireEvent.click(screen.getByRole('button', { name: /save preset/i }))

    expect(promptMock).toHaveBeenCalledWith('Enter preset name:')
    expect(props.onSavePreset).toHaveBeenCalledWith(
      'My Preset',
      expect.arrayContaining([expect.objectContaining({ id: 'eq' })]),
    )
    expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({ title: 'Preset saved' }))
  })

  it('does not save preset when prompt is cancelled', () => {
    const props = baseProps()
    vi.stubGlobal('prompt', vi.fn().mockReturnValue(null))
    render(<CurriculumGenerator {...props} />)

    fireEvent.click(screen.getByText('add-eq-rating-5'))
    fireEvent.click(screen.getByRole('button', { name: /save preset/i }))

    expect(props.onSavePreset).not.toHaveBeenCalled()
    expect(toastMock).not.toHaveBeenCalled()
  })

  it('does not save preset when prompt returns empty string', () => {
    const props = baseProps()
    vi.stubGlobal('prompt', vi.fn().mockReturnValue(''))
    render(<CurriculumGenerator {...props} />)

    fireEvent.click(screen.getByText('add-eq-rating-5'))
    fireEvent.click(screen.getByRole('button', { name: /save preset/i }))

    expect(props.onSavePreset).not.toHaveBeenCalled()
  })

  it('handleExport invokes onExport with filtered episode ids and shows success toast', async () => {
    const props = baseProps()
    render(<CurriculumGenerator {...props} />)

    fireEvent.click(screen.getByText('trigger-export'))
    await Promise.resolve()
    await Promise.resolve()

    expect(props.onExport).toHaveBeenCalledWith(['ep-1', 'ep-2', 'ep-3'], {
      format: 'json',
      filename: 'curriculum',
    })
    expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({ title: 'Export complete' }))
  })

  it('handleExport shows destructive toast when onExport rejects with Error', async () => {
    const props = baseProps()
    props.onExport = vi.fn().mockRejectedValue(new Error('boom'))
    render(<CurriculumGenerator {...props} />)

    fireEvent.click(screen.getByText('trigger-export'))
    await Promise.resolve()
    await Promise.resolve()
    await Promise.resolve()

    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: 'Export failed',
        description: 'boom',
        variant: 'destructive',
      }),
    )
  })

  it('handleExport shows generic error message when onExport rejects with non-Error', async () => {
    const props = baseProps()
    props.onExport = vi.fn().mockRejectedValue('string error')
    render(<CurriculumGenerator {...props} />)

    fireEvent.click(screen.getByText('trigger-export'))
    await Promise.resolve()
    await Promise.resolve()
    await Promise.resolve()

    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: 'Export failed',
        description: 'Unknown error',
        variant: 'destructive',
      }),
    )
  })

  it('handleExport early-returns when onExport prop is undefined', () => {
    const props = baseProps()
    render(<CurriculumGenerator {...props} onExport={undefined} />)

    fireEvent.click(screen.getByText('trigger-export'))

    expect(toastMock).not.toHaveBeenCalled()
  })

  it('filters by equals operator', () => {
    render(<CurriculumGenerator {...baseProps()} />)
    fireEvent.click(screen.getByText('add-eq-rating-5'))
    expect(screen.getByTestId('export-count')).toHaveTextContent('2')
  })

  it('filters by not_equals operator', () => {
    render(<CurriculumGenerator {...baseProps()} />)
    fireEvent.click(screen.getByText('add-ne-rating-5'))
    expect(screen.getByTestId('export-count')).toHaveTextContent('1')
  })

  it('filters by greater_than operator', () => {
    render(<CurriculumGenerator {...baseProps()} />)
    fireEvent.click(screen.getByText('add-gt-quality-0.5'))
    expect(screen.getByTestId('export-count')).toHaveTextContent('1')
  })

  it('filters by less_than operator', () => {
    render(<CurriculumGenerator {...baseProps()} />)
    fireEvent.click(screen.getByText('add-lt-quality-0.5'))
    expect(screen.getByTestId('export-count')).toHaveTextContent('1')
  })

  it('filters by greater_or_equal operator', () => {
    render(<CurriculumGenerator {...baseProps()} />)
    fireEvent.click(screen.getByText('add-ge-quality-0.5'))
    expect(screen.getByTestId('export-count')).toHaveTextContent('2')
  })

  it('filters by less_or_equal operator', () => {
    render(<CurriculumGenerator {...baseProps()} />)
    fireEvent.click(screen.getByText('add-le-quality-0.5'))
    expect(screen.getByTestId('export-count')).toHaveTextContent('2')
  })

  it('filters by is_true operator', () => {
    render(<CurriculumGenerator {...baseProps()} />)
    fireEvent.click(screen.getByText('add-anomalies-true'))
    expect(screen.getByTestId('export-count')).toHaveTextContent('1')
  })

  it('filters by is_false operator', () => {
    render(<CurriculumGenerator {...baseProps()} />)
    fireEvent.click(screen.getByText('add-issues-false'))
    expect(screen.getByTestId('export-count')).toHaveTextContent('2')
  })

  it('skips conditions on unknown field (returns true and keeps all episodes)', () => {
    render(<CurriculumGenerator {...baseProps()} />)
    fireEvent.click(screen.getByText('add-unknown-field'))
    expect(screen.getByTestId('export-count')).toHaveTextContent('3')
  })

  it('returns all episodes for unknown operator (default branch)', () => {
    render(<CurriculumGenerator {...baseProps()} />)
    fireEvent.click(screen.getByText('add-unknown-operator'))
    expect(screen.getByTestId('export-count')).toHaveTextContent('3')
  })

  it('greater_than against boolean field returns no matches', () => {
    render(<CurriculumGenerator {...baseProps()} />)
    fireEvent.click(screen.getByText('add-gt-against-bool'))
    expect(screen.getByTestId('export-count')).toHaveTextContent('0')
  })

  it('combines multiple conditions with AND semantics', () => {
    render(<CurriculumGenerator {...baseProps()} />)
    fireEvent.click(screen.getByText('add-eq-rating-5'))
    fireEvent.click(screen.getByText('add-issues-false'))
    expect(screen.getByTestId('export-count')).toHaveTextContent('1')
  })

  it('shows selection summary stats with selection rate', () => {
    render(<CurriculumGenerator {...baseProps()} />)
    expect(screen.getByText('Total episodes:')).toBeInTheDocument()
    expect(screen.getByText('Filtered:')).toBeInTheDocument()
    expect(screen.getByText('Active filters:')).toBeInTheDocument()
    expect(screen.getByText('Selection rate:')).toBeInTheDocument()
    expect(screen.getByText('100%')).toBeInTheDocument()
  })

  it('shows 0% selection rate when episodes list is empty', () => {
    render(<CurriculumGenerator {...baseProps()} episodes={[]} />)
    expect(screen.getByText('0%')).toBeInTheDocument()
  })

  it('updates selection rate after filtering', () => {
    render(<CurriculumGenerator {...baseProps()} />)
    fireEvent.click(screen.getByText('add-eq-rating-5'))
    expect(screen.getByText('67%')).toBeInTheDocument()
  })

  it('disables export when filtered episodes is empty', () => {
    render(<CurriculumGenerator {...baseProps()} episodes={[]} />)
    expect(screen.getByTestId('export-disabled')).toHaveTextContent('true')
  })

  it('passes isLoading to CurriculumPreview', async () => {
    const user = userEvent.setup()
    render(<CurriculumGenerator {...baseProps()} isLoading />)
    await user.click(screen.getByRole('tab', { name: /preview/i }))
    expect(screen.getByTestId('preview-loading')).toHaveTextContent('true')
  })

  it('switches to Preview tab when clicked', async () => {
    const user = userEvent.setup()
    render(<CurriculumGenerator {...baseProps()} />)
    await user.click(screen.getByRole('tab', { name: /preview/i }))
    expect(screen.getByTestId('preview-panel')).toBeInTheDocument()
    expect(screen.getByTestId('preview-count')).toHaveTextContent('3')
  })

  it('applies custom className', () => {
    const { container } = render(<CurriculumGenerator {...baseProps()} className="custom-class" />)
    expect(container.firstChild).toHaveClass('custom-class')
  })

  it('reflects active filter count in summary after adding conditions', () => {
    render(<CurriculumGenerator {...baseProps()} />)
    fireEvent.click(screen.getByText('add-eq-rating-5'))
    fireEvent.click(screen.getByText('add-gt-quality-0.5'))
    const summary = screen.getByText('Active filters:').parentElement
    expect(summary).toHaveTextContent('2')
  })
})
