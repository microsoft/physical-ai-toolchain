import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children, ...props }: { children: React.ReactNode } & Record<string, unknown>) => (
    <div data-testid="responsive-container">
      <pre data-testid="responsive-container-props">{JSON.stringify(props)}</pre>
      {children}
    </div>
  ),
  LineChart: ({ children, data }: { children: React.ReactNode; data: unknown }) => (
    <div data-testid="line-chart">
      <pre data-testid="line-chart-data">{JSON.stringify(data)}</pre>
      {children}
    </div>
  ),
  CartesianGrid: () => null,
  Line: ({ dataKey }: { dataKey: string }) => <div data-testid={`line-${dataKey}`} />,
  ReferenceLine: () => null,
  Tooltip: (props: Record<string, unknown>) => {
    const serializable = Object.fromEntries(
      Object.entries(props).map(([k, v]) => [k, typeof v === 'object' && v !== null && !Array.isArray(v) && '$$typeof' in v ? '<ReactElement>' : v]),
    )
    return <div data-testid="trajectory-tooltip-props">{JSON.stringify(serializable)}</div>
  },
  XAxis: () => null,
  YAxis: () => null,
}))

import { TrajectoryPlot } from '@/components/episode-viewer/TrajectoryPlot'
import { useEditStore, useEpisodeStore } from '@/stores'
import { useJointConfigStore } from '@/stores/joint-config-store'

vi.mock('@/hooks/use-joint-config', () => ({
  useJointConfigDefaults: () => ({ data: undefined }),
  useSaveJointConfig: () => ({ save: vi.fn() }),
  useSaveJointConfigDefaults: () => ({ mutate: vi.fn(), isPending: false }),
}))

afterEach(cleanup)

beforeEach(() => {
  useEpisodeStore.getState().reset()
  useEditStore.getState().clear()
  useJointConfigStore.getState().reset()

  useEpisodeStore.getState().setCurrentEpisode({
    meta: { index: 0, length: 3, taskIndex: 0, hasAnnotations: false },
    videoUrls: {},
    cameras: [],
    trajectoryData: [
      {
        frame: 0,
        timestamp: 0,
        jointPositions: Array.from({ length: 17 }, (_, index) => index),
        jointVelocities: Array.from({ length: 17 }, (_, index) => index / 10),
        endEffectorPose: [],
        gripperState: 0,
      },
      {
        frame: 1,
        timestamp: 0.1,
        jointPositions: Array.from({ length: 17 }, (_, index) => index + 1),
        jointVelocities: Array.from({ length: 17 }, (_, index) => (index + 1) / 10),
        endEffectorPose: [],
        gripperState: 1,
      },
    ],
  })
})

describe('TrajectoryPlot', () => {
  it('auto-selects the most significant joint groups for the episode telemetry', () => {
    useEpisodeStore.getState().setCurrentEpisode({
      meta: { index: 0, length: 4, taskIndex: 0, hasAnnotations: false },
      videoUrls: {},
      cameras: [],
      trajectoryData: [
        {
          frame: 0,
          timestamp: 0,
          jointPositions: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0],
          jointVelocities: Array.from({ length: 17 }, () => 0),
          endEffectorPose: [],
          gripperState: 0,
        },
        {
          frame: 1,
          timestamp: 0.1,
          jointPositions: [0, 0, 0, 0, 0, 0, 0, 0, 0.8, 1.2, 0.9, 0.2, 0.4, 0.3, 0.8, 0.6, 0],
          jointVelocities: Array.from({ length: 17 }, () => 0),
          endEffectorPose: [],
          gripperState: 0.6,
        },
        {
          frame: 2,
          timestamp: 0.2,
          jointPositions: [0, 0, 0, 0, 0, 0, 0, 0, 1.4, 2.1, 1.5, 0.5, 0.9, 0.7, 0.1, 1, 0],
          jointVelocities: Array.from({ length: 17 }, () => 0),
          endEffectorPose: [],
          gripperState: 1,
        },
        {
          frame: 3,
          timestamp: 0.3,
          jointPositions: [0, 0, 0, 0, 0, 0, 0, 0, 1.8, 2.8, 2.2, 0.9, 1.2, 1.1, -0.4, 0.2, 0],
          jointVelocities: Array.from({ length: 17 }, () => 0),
          endEffectorPose: [],
          gripperState: 0.2,
        },
      ],
    })

    render(
      <div style={{ width: 600, height: 300 }}>
        <TrajectoryPlot className="h-full" />
      </div>,
    )

    expect(screen.getByTestId('line-joint_8')).toBeInTheDocument()
    expect(screen.getByTestId('line-joint_11')).toBeInTheDocument()
    expect(screen.getByTestId('line-joint_15')).toBeInTheDocument()
    expect(screen.queryByTestId('line-joint_0')).not.toBeInTheDocument()
    expect(screen.queryByTestId('line-joint_7')).not.toBeInTheDocument()
  })

  it('renders the joint selector inside a dedicated scroll region', () => {
    render(
      <div style={{ width: 600, height: 300 }}>
        <TrajectoryPlot className="h-full" />
      </div>,
    )

    const scrollRegion = screen.getByTestId('trajectory-joint-selector-scroll')

    expect(scrollRegion).toBeInTheDocument()
    expect(scrollRegion).toHaveClass('overflow-y-auto')
    expect(scrollRegion).toHaveClass('max-h-40')
    expect(screen.getByRole('button', { name: 'Position' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Velocity' })).toBeInTheDocument()
  })

  it('defaults normalization on and lets the chart switch back to raw position values', () => {
    render(
      <div style={{ width: 600, height: 300 }}>
        <TrajectoryPlot className="h-full" />
      </div>,
    )

    const normalizeButton = screen.getByRole('button', { name: 'Normalize' })

    expect(normalizeButton).toHaveAttribute('aria-pressed', 'true')

    const normalizedData = JSON.parse(screen.getByTestId('line-chart-data').textContent ?? '[]') as Array<Record<string, number>>

    expect(normalizedData[0]?.joint_0).toBe(0)
    expect(normalizedData[0]?.joint_1).toBe(0)
    expect(normalizedData[1]?.joint_0).toBe(1)
    expect(normalizedData[1]?.joint_1).toBe(1)

    fireEvent.click(normalizeButton)

    const rawData = JSON.parse(screen.getByTestId('line-chart-data').textContent ?? '[]') as Array<Record<string, number>>

    expect(rawData[0]?.joint_0).toBe(0)
    expect(rawData[0]?.joint_1).toBe(1)
    expect(rawData[1]?.joint_0).toBe(1)
    expect(rawData[1]?.joint_1).toBe(2)
  })

  it('keeps velocity mode raw and disables the normalize control while active', () => {
    render(
      <div style={{ width: 600, height: 300 }}>
        <TrajectoryPlot className="h-full" />
      </div>,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Velocity' }))

    const normalizeButton = screen.getByRole('button', { name: 'Normalize' })
    const velocityData = JSON.parse(screen.getByTestId('line-chart-data').textContent ?? '[]') as Array<Record<string, number>>

    expect(normalizeButton).toBeDisabled()
    expect(normalizeButton).toHaveAttribute('aria-disabled', 'true')
    expect(velocityData[0]?.joint_0).toBe(0)
    expect(velocityData[0]?.joint_1).toBe(0.1)
    expect(velocityData[1]?.joint_0).toBe(0.1)
    expect(velocityData[1]?.joint_1).toBe(0.2)
  })

  it('renders the trajectory tooltip as a portal to escape overflow clipping', () => {
    render(
      <div style={{ width: 600, height: 300 }}>
        <TrajectoryPlot className="h-full" />
      </div>,
    )

    const tooltipProps = JSON.parse(screen.getByTestId('trajectory-tooltip-props').textContent ?? '{}') as {
      isAnimationActive?: boolean
      content?: unknown
      wrapperStyle?: { display?: string }
    }

    expect(tooltipProps.isAnimationActive).toBe(false)
    expect(tooltipProps.content).toBeDefined()
    expect(tooltipProps.wrapperStyle).toEqual({ display: 'none' })
  })

  it('provides a positive startup size for the responsive chart container', () => {
    render(
      <div style={{ width: 600, height: 300 }}>
        <TrajectoryPlot className="h-full" />
      </div>,
    )

    const containerProps = JSON.parse(screen.getByTestId('responsive-container-props').textContent ?? '{}') as {
      minHeight?: number
      initialDimension?: { width?: number; height?: number }
    }

    expect(containerProps.minHeight).toBe(60)
    expect(containerProps.initialDimension).toEqual({ width: 320, height: 60 })
  })

  it('supports dragging on the graph to select a frame range', () => {
    const handleRangeSelectionChange = vi.fn()
    const handleSelectionStart = vi.fn()
    const handleSelectionComplete = vi.fn()

    useEpisodeStore.getState().setCurrentEpisode({
      meta: { index: 0, length: 10, taskIndex: 0, hasAnnotations: false },
      videoUrls: {},
      cameras: [],
      trajectoryData: Array.from({ length: 10 }, (_, frame) => ({
        frame,
        timestamp: frame / 10,
        jointPositions: Array.from({ length: 17 }, (_, index) => index + frame),
        jointVelocities: Array.from({ length: 17 }, (_, index) => (index + frame) / 10),
        endEffectorPose: [],
        gripperState: 0,
      })),
    })

    render(
      <div style={{ width: 600, height: 300 }}>
        <TrajectoryPlot
          className="h-full"
          selectedRange={null}
          onSelectedRangeChange={handleRangeSelectionChange}
          onSelectionStart={handleSelectionStart}
          onSelectionComplete={handleSelectionComplete}
        />
      </div>,
    )

    const selectionSurface = screen.getByTestId('trajectory-selection-surface')

    Object.defineProperty(selectionSurface, 'getBoundingClientRect', {
      configurable: true,
      value: () => ({ left: 0, width: 300, top: 0, height: 120, right: 300, bottom: 120 }),
    })

    fireEvent.pointerDown(selectionSurface, { button: 0, clientX: 30, clientY: 20 })

    expect(handleSelectionStart).not.toHaveBeenCalled()

    fireEvent.pointerMove(selectionSurface, { clientX: 210, clientY: 20 })

    expect(handleSelectionStart).toHaveBeenCalledTimes(1)

    fireEvent.pointerUp(selectionSurface, { clientX: 210, clientY: 20 })

    expect(handleRangeSelectionChange).toHaveBeenLastCalledWith([1, 6])
    expect(handleSelectionComplete).toHaveBeenCalledWith([1, 6])
  })

  it('seeks to the clicked frame without starting subgroup selection mode', () => {
    const handleRangeSelectionChange = vi.fn()
    const handleSelectionStart = vi.fn()
    const handleSelectionComplete = vi.fn()
    const handleSeekFrame = vi.fn()

    useEpisodeStore.getState().setCurrentEpisode({
      meta: { index: 0, length: 10, taskIndex: 0, hasAnnotations: false },
      videoUrls: {},
      cameras: [],
      trajectoryData: Array.from({ length: 10 }, (_, frame) => ({
        frame,
        timestamp: frame / 10,
        jointPositions: Array.from({ length: 17 }, (_, index) => index + frame),
        jointVelocities: Array.from({ length: 17 }, (_, index) => (index + frame) / 10),
        endEffectorPose: [],
        gripperState: 0,
      })),
    })

    render(
      <div style={{ width: 600, height: 300 }}>
        <TrajectoryPlot
          className="h-full"
          selectedRange={null}
          onSelectedRangeChange={handleRangeSelectionChange}
          onSeekFrame={handleSeekFrame}
          onSelectionStart={handleSelectionStart}
          onSelectionComplete={handleSelectionComplete}
        />
      </div>,
    )

    const selectionSurface = screen.getByTestId('trajectory-selection-surface')

    Object.defineProperty(selectionSurface, 'getBoundingClientRect', {
      configurable: true,
      value: () => ({ left: 0, width: 300, top: 0, height: 120, right: 300, bottom: 120 }),
    })

    fireEvent.pointerDown(selectionSurface, { button: 0, clientX: 150, clientY: 20 })
    fireEvent.pointerUp(selectionSurface, { clientX: 150, clientY: 20 })

    expect(handleSeekFrame).toHaveBeenCalledWith(5)
    expect(useEpisodeStore.getState().currentFrame).toBe(5)
    expect(handleSelectionStart).not.toHaveBeenCalled()
    expect(handleSelectionComplete).not.toHaveBeenCalled()
    expect(handleRangeSelectionChange).not.toHaveBeenCalled()
  })

  it('offers a create subtask action from the selected graph range context menu', () => {
    const handleCreateSubtask = vi.fn()

    useEpisodeStore.getState().setCurrentEpisode({
      meta: { index: 0, length: 10, taskIndex: 0, hasAnnotations: false },
      videoUrls: {},
      cameras: [],
      trajectoryData: Array.from({ length: 10 }, (_, frame) => ({
        frame,
        timestamp: frame / 10,
        jointPositions: Array.from({ length: 17 }, (_, index) => index + frame),
        jointVelocities: Array.from({ length: 17 }, (_, index) => (index + frame) / 10),
        endEffectorPose: [],
        gripperState: 0,
      })),
    })

    render(
      <div style={{ width: 600, height: 300 }}>
        <TrajectoryPlot
          className="h-full"
          selectedRange={[2, 6]}
          onSelectedRangeChange={vi.fn()}
          onCreateSubtaskFromRange={handleCreateSubtask}
        />
      </div>,
    )

    const selectionSurface = screen.getByTestId('trajectory-selection-surface')

    Object.defineProperty(selectionSurface, 'getBoundingClientRect', {
      configurable: true,
      value: () => ({ left: 0, width: 300, top: 0, height: 120, right: 300, bottom: 120 }),
    })

    fireEvent.contextMenu(selectionSurface, { clientX: 120, clientY: 24 })
    fireEvent.click(screen.getByRole('button', { name: /create subtask/i }))

    expect(handleCreateSubtask).toHaveBeenCalledWith([2, 6])
  })

  it('clears the current graph selection when Escape is pressed', () => {
    const handleRangeSelectionChange = vi.fn()

    render(
      <div style={{ width: 600, height: 300 }}>
        <TrajectoryPlot
          className="h-full"
          selectedRange={[2, 6]}
          onSelectedRangeChange={handleRangeSelectionChange}
        />
      </div>,
    )

    fireEvent.keyDown(window, { key: 'Escape' })

    expect(handleRangeSelectionChange).toHaveBeenCalledWith(null)
  })

  it('does not clear a draft graph selection when the user clicks outside the graph', () => {
    const handleRangeSelectionChange = vi.fn()

    render(
      <div>
        <button type="button">Outside</button>
        <div style={{ width: 600, height: 300 }}>
          <TrajectoryPlot
            className="h-full"
            selectedRange={[2, 6]}
            onSelectedRangeChange={handleRangeSelectionChange}
          />
        </div>
      </div>,
    )

    fireEvent.pointerDown(screen.getByRole('button', { name: 'Outside' }))

    expect(handleRangeSelectionChange).not.toHaveBeenCalled()
  })

  it('keeps graph pointer handlers from swallowing a context-menu create click', () => {
    const handleCreateSubtask = vi.fn()

    useEpisodeStore.getState().setCurrentEpisode({
      meta: { index: 0, length: 10, taskIndex: 0, hasAnnotations: false },
      videoUrls: {},
      cameras: [],
      trajectoryData: Array.from({ length: 10 }, (_, frame) => ({
        frame,
        timestamp: frame / 10,
        jointPositions: Array.from({ length: 17 }, (_, index) => index + frame),
        jointVelocities: Array.from({ length: 17 }, (_, index) => (index + frame) / 10),
        endEffectorPose: [],
        gripperState: 0,
      })),
    })

    render(
      <div style={{ width: 600, height: 300 }}>
        <TrajectoryPlot
          className="h-full"
          selectedRange={[2, 6]}
          onSelectedRangeChange={vi.fn()}
          onCreateSubtaskFromRange={handleCreateSubtask}
        />
      </div>,
    )

    const selectionSurface = screen.getByTestId('trajectory-selection-surface')

    Object.defineProperty(selectionSurface, 'getBoundingClientRect', {
      configurable: true,
      value: () => ({ left: 0, width: 300, top: 0, height: 120, right: 300, bottom: 120 }),
    })

    fireEvent.contextMenu(selectionSurface, { clientX: 120, clientY: 24 })

    const createButton = screen.getByRole('button', { name: /create subtask/i })

    fireEvent.pointerDown(createButton, { button: 0, clientX: 120, clientY: 24 })
    fireEvent.pointerUp(createButton, { button: 0, clientX: 120, clientY: 24 })
    fireEvent.click(createButton, { button: 0, clientX: 120, clientY: 24 })

    expect(handleCreateSubtask).toHaveBeenCalledTimes(1)
    expect(handleCreateSubtask).toHaveBeenCalledWith([2, 6])
  })
})
