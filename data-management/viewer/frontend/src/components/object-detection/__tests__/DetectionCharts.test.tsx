import { render, screen, within } from '@testing-library/react'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'

import type { EpisodeDetectionSummary } from '@/types/detection'

import { DetectionCharts } from '../DetectionCharts'

vi.mock('recharts', () => {
  type ChartChildren = { children?: ReactNode }
  type DataProps<T> = ChartChildren & { data?: T[] }
  type PieDataItem = { name: string; count: number }
  type PieProps = ChartChildren & {
    data?: PieDataItem[]
    label?: (entry: { name: string; percent: number }) => string
  }
  type CellProps = { fill?: string }
  type TooltipProps = {
    formatter?: (value: number) => [string, string]
  }

  return {
    ResponsiveContainer: ({ children }: ChartChildren) => (
      <div data-testid="responsive-container">{children}</div>
    ),
    PieChart: ({ children }: ChartChildren) => <div data-testid="pie-chart">{children}</div>,
    Pie: ({ children, data, label }: PieProps) => {
      const items = data ?? []
      const total = items.reduce((sum, item) => sum + item.count, 0)
      return (
        <div data-testid="pie" data-count={items.length}>
          {items.map((item) => (
            <span key={item.name} data-testid="pie-label">
              {label?.({ name: item.name, percent: total > 0 ? item.count / total : 0 })}
            </span>
          ))}
          {children}
        </div>
      )
    },
    Cell: ({ fill }: CellProps) => <div data-testid="cell" data-fill={fill} />,
    LineChart: ({ children, data }: DataProps<unknown>) => (
      <div data-testid="line-chart" data-points={data?.length ?? 0}>
        {children}
      </div>
    ),
    Line: () => <div data-testid="line" />,
    BarChart: ({ children, data }: DataProps<{ range: string; count: number }>) => (
      <div
        data-testid="bar-chart"
        data-bars={data?.length ?? 0}
        data-buckets={JSON.stringify(data ?? [])}
      >
        {children}
      </div>
    ),
    Bar: () => <div data-testid="bar" />,
    XAxis: () => <div data-testid="x-axis" />,
    YAxis: () => <div data-testid="y-axis" />,
    Tooltip: ({ formatter }: TooltipProps) => (
      <div
        data-testid="tooltip"
        data-formatted={formatter ? JSON.stringify(formatter(7)) : undefined}
      />
    ),
  }
})

const buildSummary = (
  overrides: Partial<EpisodeDetectionSummary> = {},
): EpisodeDetectionSummary => ({
  total_frames: 5,
  processed_frames: 3,
  total_detections: 7,
  class_summary: {
    person: { count: 4, avg_confidence: 0.85 },
    car: { count: 2, avg_confidence: 0.72 },
    dog: { count: 1, avg_confidence: 0.93 },
  },
  detections_by_frame: [
    {
      frame: 0,
      detections: [
        { class_id: 0, class_name: 'person', confidence: 0.95, bbox: [0, 0, 10, 10] },
        { class_id: 0, class_name: 'person', confidence: 0.55, bbox: [0, 0, 10, 10] },
      ],
      processing_time_ms: 12,
    },
    {
      frame: 1,
      detections: [
        { class_id: 0, class_name: 'person', confidence: 0.15, bbox: [0, 0, 10, 10] },
        { class_id: 1, class_name: 'car', confidence: 0.65, bbox: [0, 0, 10, 10] },
      ],
      processing_time_ms: 11,
    },
    {
      frame: 2,
      detections: [
        { class_id: 0, class_name: 'person', confidence: 0.35, bbox: [0, 0, 10, 10] },
        { class_id: 1, class_name: 'car', confidence: 0.75, bbox: [0, 0, 10, 10] },
        { class_id: 2, class_name: 'dog', confidence: 0.92, bbox: [0, 0, 10, 10] },
      ],
      processing_time_ms: 13,
    },
  ],
  ...overrides,
})

describe('DetectionCharts', () => {
  it('renders the three section headings and summary stats', () => {
    render(<DetectionCharts summary={buildSummary()} />)

    expect(screen.getByText('Class Distribution')).toBeInTheDocument()
    expect(screen.getByText('Detections Over Time')).toBeInTheDocument()
    expect(screen.getByText('Confidence Distribution')).toBeInTheDocument()
    expect(screen.getByText('Class Breakdown')).toBeInTheDocument()

    const totalTile = screen.getByText('Total Detections').parentElement!
    expect(within(totalTile).getByText('7')).toBeInTheDocument()
    const uniqueTile = screen.getByText('Unique Classes').parentElement!
    expect(within(uniqueTile).getByText('3')).toBeInTheDocument()
    const framesTile = screen.getByText('Frames Processed').parentElement!
    expect(within(framesTile).getByText('3')).toBeInTheDocument()
  })

  it('renders the pie chart with one cell per class sorted by count desc', () => {
    render(<DetectionCharts summary={buildSummary()} />)

    const pie = screen.getByTestId('pie')
    expect(pie).toHaveAttribute('data-count', '3')

    const cells = screen.getAllByTestId('cell')
    expect(cells).toHaveLength(3)
    expect(cells[0]).toHaveAttribute('data-fill', '#FF6B6B')
    expect(cells[1]).toHaveAttribute('data-fill', '#4ECDC4')
    expect(cells[2]).toHaveAttribute('data-fill', '#45B7D1')

    const labels = screen.getAllByTestId('pie-label').map((el) => el.textContent)
    expect(labels).toEqual(['person 57%', 'car 29%', 'dog 14%'])
  })

  it('formats the pie tooltip with count and percentage of total', () => {
    render(<DetectionCharts summary={buildSummary()} />)

    const tooltip = screen.getAllByTestId('tooltip')[0]
    expect(tooltip).toHaveAttribute('data-formatted', JSON.stringify(['7 (100.0%)', 'Count']))
  })

  it('renders the class breakdown table with formatted percentages and confidences', () => {
    render(<DetectionCharts summary={buildSummary()} />)

    const table = screen.getByRole('table')
    const rows = within(table).getAllByRole('row')
    // 1 header row + 3 data rows
    expect(rows).toHaveLength(4)

    const personRow = within(table).getByText('person').closest('tr')!
    expect(within(personRow).getByText('4')).toBeInTheDocument()
    expect(within(personRow).getByText('57.1%')).toBeInTheDocument()
    expect(within(personRow).getByText('85%')).toBeInTheDocument()

    const carRow = within(table).getByText('car').closest('tr')!
    expect(within(carRow).getByText('2')).toBeInTheDocument()
    expect(within(carRow).getByText('28.6%')).toBeInTheDocument()
    expect(within(carRow).getByText('72%')).toBeInTheDocument()

    const dogRow = within(table).getByText('dog').closest('tr')!
    expect(within(dogRow).getByText('1')).toBeInTheDocument()
    expect(within(dogRow).getByText('14.3%')).toBeInTheDocument()
    expect(within(dogRow).getByText('93%')).toBeInTheDocument()
  })

  it('limits the breakdown table to the top 8 classes', () => {
    const class_summary: Record<string, { count: number; avg_confidence: number }> = {}
    for (let i = 0; i < 12; i += 1) {
      class_summary[`class_${i}`] = { count: 12 - i, avg_confidence: 0.5 }
    }

    render(
      <DetectionCharts
        summary={buildSummary({
          total_detections: 78,
          class_summary,
          detections_by_frame: [],
        })}
      />,
    )

    const table = screen.getByRole('table')
    const rows = within(table).getAllByRole('row')
    // 1 header + 8 data rows
    expect(rows).toHaveLength(9)
    expect(within(table).getByText('class_0')).toBeInTheDocument()
    expect(within(table).getByText('class_7')).toBeInTheDocument()
    expect(within(table).queryByText('class_8')).not.toBeInTheDocument()
    expect(within(table).queryByText('class_11')).not.toBeInTheDocument()
  })

  it('shows the empty state and hides the pie chart and breakdown table when no classes exist', () => {
    render(
      <DetectionCharts
        summary={buildSummary({
          total_detections: 0,
          class_summary: {},
          detections_by_frame: [],
        })}
      />,
    )

    expect(screen.getByText('No detections to display')).toBeInTheDocument()
    expect(screen.queryByTestId('pie-chart')).not.toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    expect(screen.queryByText('Class Breakdown')).not.toBeInTheDocument()
  })

  it('still renders the time series and histogram sections when class data is empty', () => {
    render(
      <DetectionCharts
        summary={buildSummary({
          total_detections: 0,
          class_summary: {},
          detections_by_frame: [],
        })}
      />,
    )

    expect(screen.getByTestId('line-chart')).toHaveAttribute('data-points', '0')
    const bar = screen.getByTestId('bar-chart')
    expect(bar).toHaveAttribute('data-bars', '5')
  })

  it('builds the confidence histogram with five buckets and correct counts', () => {
    render(<DetectionCharts summary={buildSummary()} />)

    const bar = screen.getByTestId('bar-chart')
    expect(bar).toHaveAttribute('data-bars', '5')

    const buckets = JSON.parse(bar.getAttribute('data-buckets') ?? '[]') as Array<{
      range: string
      count: number
    }>
    expect(buckets.map((b) => b.range)).toEqual(['0-20%', '20-40%', '40-60%', '60-80%', '80-100%'])
    // From the fixture's 7 detections:
    // 0.95 -> bucket 4, 0.55 -> 2, 0.15 -> 0, 0.65 -> 3, 0.35 -> 1, 0.75 -> 3, 0.92 -> 4
    expect(buckets.map((b) => b.count)).toEqual([1, 1, 1, 2, 2])
  })

  it('samples the time series and uses the original frame numbers', () => {
    const detections_by_frame = Array.from({ length: 4 }, (_, frame) => ({
      frame,
      detections: [
        {
          class_id: 0,
          class_name: 'person',
          confidence: 0.5,
          bbox: [0, 0, 1, 1] as [number, number, number, number],
        },
      ],
      processing_time_ms: 10,
    }))

    render(
      <DetectionCharts
        summary={buildSummary({
          processed_frames: 4,
          total_detections: 4,
          class_summary: { person: { count: 4, avg_confidence: 0.5 } },
          detections_by_frame,
        })}
      />,
    )

    const line = screen.getByTestId('line-chart')
    // length < 50 means step is Math.max(1, 0) = 1, so all frames pass the filter
    expect(line).toHaveAttribute('data-points', '4')
  })
})
