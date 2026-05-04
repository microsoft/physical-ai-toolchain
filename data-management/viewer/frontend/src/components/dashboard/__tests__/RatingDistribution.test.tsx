import { cleanup, render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('recharts', () => {
  const Passthrough = ({ children }: { children?: ReactNode }) => <div>{children}</div>
  const ResponsiveContainer = ({ children }: { children: ReactNode }) => (
    <div style={{ width: 800, height: 400 }}>{children}</div>
  )
  const BarChart = ({ children }: { children?: ReactNode }) => (
    <div data-testid="bar-chart">{children}</div>
  )
  const Bar = ({ children }: { children?: ReactNode }) => <div data-testid="bar">{children}</div>
  const Cell = ({ fill }: { fill: string }) => <div data-testid="cell" data-fill={fill} />
  const Tooltip = ({
    content,
  }: {
    content: (props: {
      active: boolean
      payload: Array<{ payload: { rating: string; count: number; label: string } }>
    }) => ReactNode
  }) => (
    <div data-testid="tooltip">
      {content({
        active: true,
        payload: [{ payload: { rating: '5', count: 4, label: '5 Stars' } }],
      })}
    </div>
  )
  return {
    Bar,
    BarChart,
    CartesianGrid: Passthrough,
    Cell,
    ResponsiveContainer,
    Tooltip,
    XAxis: () => null,
    YAxis: () => null,
  }
})

import { RatingDistribution } from '@/components/dashboard/RatingDistribution'

afterEach(() => {
  cleanup()
})

describe('RatingDistribution', () => {
  it('renders the default title and a zero total when distribution is empty', () => {
    render(<RatingDistribution distribution={{}} />)
    expect(screen.getByText('Rating Distribution')).toBeInTheDocument()
    expect(screen.getByText('0 total')).toBeInTheDocument()
  })

  it('renders one Cell per rating bucket (1-5)', () => {
    render(<RatingDistribution distribution={{ '5': 4, '4': 3, '3': 2, '2': 1, '1': 0 }} />)
    expect(screen.getAllByTestId('cell')).toHaveLength(5)
  })

  it('formats the total with locale separators in the header', () => {
    render(<RatingDistribution distribution={{ '5': 1500 }} />)
    expect(screen.getByText('1,500 total')).toBeInTheDocument()
  })

  it('renders custom Tooltip content with label, count and percent', () => {
    render(<RatingDistribution distribution={{ '5': 4 }} />)
    expect(screen.getByText('5 Stars')).toBeInTheDocument()
    expect(screen.getByText('4 episodes (100%)')).toBeInTheDocument()
  })

  it('uses the quality color scheme when requested', () => {
    render(
      <RatingDistribution
        distribution={{ '5': 1 }}
        title="Trajectory Quality"
        colorScheme="quality"
      />,
    )
    expect(screen.getByText('Trajectory Quality')).toBeInTheDocument()
    const cells = screen.getAllByTestId('cell')
    // Quality scheme uses #16a34a for rating 5
    expect(cells[4]).toHaveAttribute('data-fill', '#16a34a')
  })

  it('uses the neutral color scheme when requested', () => {
    render(<RatingDistribution distribution={{ '5': 1 }} colorScheme="neutral" />)
    const cells = screen.getAllByTestId('cell')
    expect(cells[0]).toHaveAttribute('data-fill', '#6b7280')
    expect(cells[4]).toHaveAttribute('data-fill', '#6b7280')
  })
})
