import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { Anomaly } from '@/types/annotations'

import { AnomalyList } from '../AnomalyList'

const baseAnomaly: Anomaly = {
  id: 'a1',
  type: 'unexpected-stop',
  severity: 'high',
  frameRange: [10, 20],
  timestamp: [10 / 30, 20 / 30],
  description: 'robot halted unexpectedly',
  autoDetected: true,
  verified: false,
}

describe('AnomalyList', () => {
  it('renders the empty state when there are no anomalies', () => {
    render(<AnomalyList anomalies={[]} onRemove={vi.fn()} onToggleVerified={vi.fn()} />)
    expect(screen.getByText(/no anomalies detected/i)).toBeInTheDocument()
  })

  it('renders an anomaly with type, description, and frame button', () => {
    render(<AnomalyList anomalies={[baseAnomaly]} onRemove={vi.fn()} onToggleVerified={vi.fn()} />)
    expect(screen.getByText(/unexpected-stop/i)).toBeInTheDocument()
    expect(screen.getByText(/robot halted unexpectedly/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /frames 10-20/i })).toBeInTheDocument()
  })

  it('shows the auto badge when autoDetected and the verified badge when verified', () => {
    render(
      <AnomalyList
        anomalies={[{ ...baseAnomaly, verified: true }]}
        onRemove={vi.fn()}
        onToggleVerified={vi.fn()}
      />,
    )
    expect(screen.getByText(/auto/i)).toBeInTheDocument()
    expect(screen.getByText(/verified/i)).toBeInTheDocument()
  })

  it('calls onSeek with the first frame in range when the frame button is clicked', async () => {
    const user = userEvent.setup()
    const onSeek = vi.fn()
    render(
      <AnomalyList
        anomalies={[baseAnomaly]}
        onRemove={vi.fn()}
        onToggleVerified={vi.fn()}
        onSeek={onSeek}
      />,
    )
    await user.click(screen.getByRole('button', { name: /frames 10-20/i }))
    expect(onSeek).toHaveBeenCalledWith(10)
  })

  it('renders the verify button only for auto-detected anomalies and toggles verification', async () => {
    const user = userEvent.setup()
    const onToggleVerified = vi.fn()
    const { rerender } = render(
      <AnomalyList
        anomalies={[baseAnomaly]}
        onRemove={vi.fn()}
        onToggleVerified={onToggleVerified}
      />,
    )
    const verifyButton = screen.getByRole('button', { name: /mark verified/i })
    await user.click(verifyButton)
    expect(onToggleVerified).toHaveBeenCalledWith('a1')

    rerender(
      <AnomalyList
        anomalies={[{ ...baseAnomaly, autoDetected: false }]}
        onRemove={vi.fn()}
        onToggleVerified={onToggleVerified}
      />,
    )
    expect(screen.queryByRole('button', { name: /mark verified/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /mark unverified/i })).not.toBeInTheDocument()
  })

  it('shows the unverify title once an auto-detected anomaly is verified', () => {
    render(
      <AnomalyList
        anomalies={[{ ...baseAnomaly, verified: true }]}
        onRemove={vi.fn()}
        onToggleVerified={vi.fn()}
      />,
    )
    expect(screen.getByRole('button', { name: /mark unverified/i })).toBeInTheDocument()
  })

  it('calls onRemove with the anomaly id when the trash button is clicked', async () => {
    const user = userEvent.setup()
    const onRemove = vi.fn()
    render(
      <AnomalyList
        anomalies={[{ ...baseAnomaly, autoDetected: false }]}
        onRemove={onRemove}
        onToggleVerified={vi.fn()}
      />,
    )
    // Only the frame button and trash button render; trash is last.
    const buttons = screen.getAllByRole('button')
    await user.click(buttons[buttons.length - 1])
    expect(onRemove).toHaveBeenCalledWith('a1')
  })
})
