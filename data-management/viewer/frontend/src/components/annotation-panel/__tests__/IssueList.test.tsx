import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { DataQualityIssue } from '@/types/annotations'

import { IssueList } from '../IssueList'

const baseIssue: DataQualityIssue = {
  type: 'frame-drop',
  severity: 'critical',
  affectedFrames: [10, 20],
  notes: 'camera dropped frames',
}

describe('IssueList', () => {
  it('renders the empty state when there are no issues', () => {
    render(<IssueList issues={[]} onRemove={vi.fn()} />)
    expect(screen.getByText(/no issues reported/i)).toBeInTheDocument()
  })

  it('renders an issue with type label, notes, and frame range', () => {
    render(<IssueList issues={[baseIssue]} onRemove={vi.fn()} onSeek={vi.fn()} />)
    expect(screen.getByText(/frame drop/i)).toBeInTheDocument()
    expect(screen.getByText(/camera dropped frames/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /frames 10-20/i })).toBeInTheDocument()
  })

  it('calls onSeek with the first affected frame when the frame button is clicked', async () => {
    const user = userEvent.setup()
    const onSeek = vi.fn()
    render(<IssueList issues={[baseIssue]} onRemove={vi.fn()} onSeek={onSeek} />)
    await user.click(screen.getByRole('button', { name: /frames 10-20/i }))
    expect(onSeek).toHaveBeenCalledWith(10)
  })

  it('calls onRemove with the issue index when delete is clicked', async () => {
    const user = userEvent.setup()
    const onRemove = vi.fn()
    render(<IssueList issues={[baseIssue]} onRemove={onRemove} />)
    // The trash button has no accessible name; it is the only button when onSeek is omitted
    const buttons = screen.getAllByRole('button')
    await user.click(buttons[buttons.length - 1])
    expect(onRemove).toHaveBeenCalledWith(0)
  })

  it('renders all three severity variants', () => {
    const issues: DataQualityIssue[] = [
      { type: 'frame-drop', severity: 'critical', affectedFrames: [1, 2] },
      { type: 'sync-issue', severity: 'major', affectedFrames: [3, 4] },
      { type: 'occlusion', severity: 'minor', affectedFrames: [5, 6] },
    ]
    render(<IssueList issues={issues} onRemove={vi.fn()} />)
    expect(screen.getByText(/critical/i)).toBeInTheDocument()
    expect(screen.getByText(/major/i)).toBeInTheDocument()
    expect(screen.getByText(/minor/i)).toBeInTheDocument()
  })
})
