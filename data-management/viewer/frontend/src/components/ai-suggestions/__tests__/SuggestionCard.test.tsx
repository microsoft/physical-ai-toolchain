import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { AnnotationSuggestion, DetectedAnomaly } from '@/api/ai-analysis'

import { SuggestionCard } from '../SuggestionCard'

const buildAnomaly = (overrides: Partial<DetectedAnomaly> = {}): DetectedAnomaly => ({
  id: 'a-1',
  type: 'sudden_stop',
  severity: 'high',
  frame_start: 10,
  frame_end: 20,
  description: 'Trajectory stops abruptly',
  confidence: 0.95,
  auto_detected: true,
  ...overrides,
})

const buildSuggestion = (overrides: Partial<AnnotationSuggestion> = {}): AnnotationSuggestion => ({
  task_completion_rating: 4,
  trajectory_quality_score: 3,
  suggested_flags: ['needs_review', 'partial_success'],
  detected_anomalies: [buildAnomaly()],
  confidence: 0.82,
  reasoning: 'Trajectory shows smooth motion with one anomaly.',
  ...overrides,
})

describe('SuggestionCard', () => {
  it('renders confidence percentage rounded to nearest integer', () => {
    render(<SuggestionCard suggestion={buildSuggestion({ confidence: 0.826 })} />)
    expect(screen.getByText('83% confidence')).toBeInTheDocument()
  })

  it('renders task completion and trajectory quality sections', () => {
    render(<SuggestionCard suggestion={buildSuggestion()} />)
    expect(screen.getByText('Task Completion')).toBeInTheDocument()
    expect(screen.getByText('Trajectory Quality')).toBeInTheDocument()
  })

  it('renders suggested flag chips with underscores replaced by spaces', () => {
    render(<SuggestionCard suggestion={buildSuggestion()} />)
    expect(screen.getByText('needs review')).toBeInTheDocument()
    expect(screen.getByText('partial success')).toBeInTheDocument()
  })

  it('hides flags section when no flags are suggested', () => {
    render(<SuggestionCard suggestion={buildSuggestion({ suggested_flags: [] })} />)
    expect(screen.queryByText('Suggested Flags')).not.toBeInTheDocument()
  })

  it('hides anomalies section when no anomalies are detected', () => {
    render(<SuggestionCard suggestion={buildSuggestion({ detected_anomalies: [] })} />)
    expect(screen.queryByText(/Detected Anomalies/)).not.toBeInTheDocument()
  })

  it('shows anomaly count header collapsed by default', () => {
    render(<SuggestionCard suggestion={buildSuggestion()} />)
    expect(screen.getByText('Detected Anomalies (1)')).toBeInTheDocument()
    expect(screen.queryByText('Trajectory stops abruptly')).not.toBeInTheDocument()
  })

  it('expands anomaly details when chevron is clicked', () => {
    render(<SuggestionCard suggestion={buildSuggestion()} />)
    const expandButtons = screen.getAllByRole('button')
    const chevronButton = expandButtons.find((b) => b.querySelector('svg.lucide-chevron-down'))
    expect(chevronButton).toBeDefined()
    fireEvent.click(chevronButton!)
    expect(screen.getByText('Trajectory stops abruptly')).toBeInTheDocument()
    expect(screen.getByText('Frames 10 - 20')).toBeInTheDocument()
  })

  it('renders reasoning text', () => {
    render(<SuggestionCard suggestion={buildSuggestion()} />)
    expect(screen.getByText('Trajectory shows smooth motion with one anomaly.')).toBeInTheDocument()
  })

  it('renders Apply All button when no onPartialAccept is provided', () => {
    render(<SuggestionCard suggestion={buildSuggestion()} onAccept={vi.fn()} />)
    expect(screen.getByRole('button', { name: /Apply All/ })).toBeInTheDocument()
  })

  it('invokes onAccept when Apply All is clicked', () => {
    const onAccept = vi.fn()
    render(<SuggestionCard suggestion={buildSuggestion()} onAccept={onAccept} />)
    fireEvent.click(screen.getByRole('button', { name: /Apply All/ }))
    expect(onAccept).toHaveBeenCalledTimes(1)
  })

  it('invokes onReject when Reject is clicked', () => {
    const onReject = vi.fn()
    render(<SuggestionCard suggestion={buildSuggestion()} onReject={onReject} />)
    fireEvent.click(screen.getByRole('button', { name: /Reject/ }))
    expect(onReject).toHaveBeenCalledTimes(1)
  })

  it('disables Reject and Apply buttons when isApplying is true', () => {
    render(
      <SuggestionCard
        suggestion={buildSuggestion()}
        onAccept={vi.fn()}
        onReject={vi.fn()}
        isApplying
      />,
    )
    expect(screen.getByRole('button', { name: /Reject/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: /Apply All/ })).toBeDisabled()
  })

  it('renders Applied status when isAccepted is true and hides action buttons', () => {
    render(
      <SuggestionCard
        suggestion={buildSuggestion()}
        isAccepted
        onAccept={vi.fn()}
        onReject={vi.fn()}
      />,
    )
    expect(screen.getByText('Applied')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Apply All/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Reject/ })).not.toBeInTheDocument()
  })

  it('renders Rejected status when isRejected is true', () => {
    render(<SuggestionCard suggestion={buildSuggestion()} isRejected />)
    expect(screen.getByText('Rejected')).toBeInTheDocument()
  })

  it('renders partial-accept toggles when onPartialAccept is provided', () => {
    render(<SuggestionCard suggestion={buildSuggestion()} onPartialAccept={vi.fn()} />)
    expect(screen.getByLabelText('Task Completion')).toBeInTheDocument()
    expect(screen.getByLabelText('Trajectory Quality')).toBeInTheDocument()
  })

  it('switches to Apply Selected button when a field is deselected', () => {
    render(
      <SuggestionCard
        suggestion={buildSuggestion()}
        onAccept={vi.fn()}
        onPartialAccept={vi.fn()}
      />,
    )
    expect(screen.getByRole('button', { name: /Apply All/ })).toBeInTheDocument()
    fireEvent.click(screen.getByLabelText('Task Completion'))
    expect(screen.getByRole('button', { name: /Apply Selected \(3\)/ })).toBeInTheDocument()
  })

  it('invokes onPartialAccept with selected fields when Apply Selected is clicked', () => {
    const onPartialAccept = vi.fn()
    render(
      <SuggestionCard
        suggestion={buildSuggestion()}
        onAccept={vi.fn()}
        onPartialAccept={onPartialAccept}
      />,
    )
    fireEvent.click(screen.getByLabelText('Suggested Flags'))
    fireEvent.click(screen.getByLabelText('Detected Anomalies (1)'))
    fireEvent.click(screen.getByRole('button', { name: /Apply Selected \(2\)/ }))
    expect(onPartialAccept).toHaveBeenCalledTimes(1)
    const fields = onPartialAccept.mock.calls[0][0] as string[]
    expect(fields).toContain('task_completion')
    expect(fields).toContain('trajectory_quality')
    expect(fields).not.toContain('flags')
    expect(fields).not.toContain('anomalies')
  })

  it('disables Apply Selected button when no fields are selected', () => {
    render(
      <SuggestionCard
        suggestion={buildSuggestion()}
        onAccept={vi.fn()}
        onPartialAccept={vi.fn()}
      />,
    )
    fireEvent.click(screen.getByLabelText('Task Completion'))
    fireEvent.click(screen.getByLabelText('Trajectory Quality'))
    fireEvent.click(screen.getByLabelText('Suggested Flags'))
    fireEvent.click(screen.getByLabelText('Detected Anomalies (1)'))
    expect(screen.getByRole('button', { name: /Apply Selected \(0\)/ })).toBeDisabled()
  })
})
