import { render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="responsive-container">{children}</div>
  ),
  LineChart: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  CartesianGrid: () => null,
  Line: (props: Record<string, unknown>) => (
    <div
      data-testid={'line-' + String(props.dataKey)}
      data-dash={String(props.strokeDasharray ?? '')}
    />
  ),
  ReferenceLine: () => null,
  Tooltip: ({ content }: { content: React.ReactElement<Record<string, unknown>> }) =>
    React.cloneElement(content, {
      active: true,
      label: 0,
      payload: [
        { name: 'shoulder_pan.pos', value: 0.1, color: '#111', dataKey: 'series_0' },
        { name: 'shoulder_pan.pos', value: 0.2, color: '#222', dataKey: 'series_6' },
      ],
    }),
  XAxis: () => null,
  YAxis: () => null,
}))

import React from 'react'

import { TrajectoryPlotChart } from '@/components/episode-viewer/TrajectoryPlotChart'

const defaultProps = {
  chartData: [{ frame: 0, series_0: 0.1, series_6: 0.2 }],
  currentFrame: 0,
  selectedJoints: [0, 6],
  resolveLabel: () => 'shoulder_pan.pos',
  resolveDataKey: (index: number) => `series_${index}`,
  trajectoryAdjustments: new Map<number, unknown>(),
  showVelocity: false,
  showNormalized: true,
  selectedRange: null,
  selectionHighlight: null,
  contextMenuPosition: null,
  onChartClick: vi.fn(),
  onSelectionContextMenu: vi.fn(),
  onSelectionPointerDown: vi.fn(),
  onSelectionPointerMove: vi.fn(),
  onSelectionPointerUp: vi.fn(),
  onSelectionPointerCancel: vi.fn(),
  onDismissContextMenu: vi.fn(),
  selectionSurfaceRef: { current: null },
}

describe('TrajectoryPlotChart', () => {
  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders tooltip rows with duplicate labels without React key warnings', () => {
    render(<TrajectoryPlotChart {...defaultProps} />)

    expect(screen.getAllByText(/shoulder_pan\.pos/).length).toBeGreaterThanOrEqual(2)
    expect(console.error).not.toHaveBeenCalledWith(
      expect.stringContaining('Encountered two children with the same key'),
      expect.anything(),
    )
  })
  it('provides a named trajectory data table and non-color series distinctions', () => {
    render(<TrajectoryPlotChart {...defaultProps} />)

    expect(screen.getByRole('table', { name: 'Trajectory data' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Frame' })).toBeInTheDocument()
    expect(screen.getAllByRole('columnheader', { name: 'shoulder_pan.pos' })).toHaveLength(2)
    expect(screen.getByTestId('line-series_0')).toHaveAttribute('data-dash', '')
    expect(screen.getByTestId('line-series_6')).not.toHaveAttribute('data-dash', '')
  })
})
