import type React from 'react'
import { useCallback, useState } from 'react'
import { createPortal } from 'react-dom'
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { JOINT_COLORS } from './joint-constants'
import { TrajectoryPlotSelectionOverlay } from './TrajectoryPlotSelectionOverlay'

const TRAJECTORY_CHART_MIN_HEIGHT = 60
const TRAJECTORY_CHART_INITIAL_DIMENSION = { width: 320, height: TRAJECTORY_CHART_MIN_HEIGHT }
const TOOLTIP_OFFSET = 12

function TrajectoryTooltipPortal({
  active,
  payload,
  label,
  mousePosition,
}: {
  active?: boolean
  payload?: Array<{ name: string; value: number; color: string }>
  label?: number
  mousePosition: { x: number; y: number }
}) {
  const tooltipRef = useCallback(
    (node: HTMLDivElement | null) => {
      if (!node) return
      const rect = node.getBoundingClientRect()
      const viewportH = window.innerHeight
      const viewportW = window.innerWidth

      let top = mousePosition.y + TOOLTIP_OFFSET
      let left = mousePosition.x + TOOLTIP_OFFSET

      if (top + rect.height > viewportH) {
        top = mousePosition.y - rect.height - TOOLTIP_OFFSET
      }
      if (left + rect.width > viewportW) {
        left = mousePosition.x - rect.width - TOOLTIP_OFFSET
      }

      node.style.top = `${top}px`
      node.style.left = `${left}px`
    },
    [mousePosition],
  )

  if (!active || !payload?.length) return null

  return createPortal(
    <div
      ref={tooltipRef}
      className="pointer-events-none fixed z-50 rounded-md border bg-popover p-2.5 text-sm shadow-md"
      style={{ left: mousePosition.x + TOOLTIP_OFFSET, top: mousePosition.y + TOOLTIP_OFFSET }}
    >
      <p className="mb-1 font-medium">{label}</p>
      {payload.map((entry) => (
        <p key={entry.name} style={{ color: entry.color }}>
          {entry.name} : {entry.value}
        </p>
      ))}
    </div>,
    document.body,
  )
}

interface TrajectoryPlotChartProps {
  chartData: Array<Record<string, number | boolean>>
  currentFrame: number
  selectedJoints: number[]
  resolveLabel: (index: number) => string
  trajectoryAdjustments: Map<number, unknown>
  showVelocity: boolean
  showNormalized: boolean
  selectedRange: [number, number] | null
  selectionHighlight: { left: string; width: string } | null
  contextMenuPosition: { x: number; y: number } | null
  onChartClick: (data: unknown) => void
  onSelectionContextMenu: (event: React.MouseEvent<HTMLDivElement>) => void
  onSelectionPointerDown: (event: React.PointerEvent<HTMLDivElement>) => void
  onSelectionPointerMove: (event: React.PointerEvent<HTMLDivElement>) => void
  onSelectionPointerUp: (event: React.PointerEvent<HTMLDivElement>) => void
  onCreateSubtaskFromRange?: (range: [number, number]) => void
  onDismissContextMenu: () => void
  selectionSurfaceRef: React.RefObject<HTMLDivElement>
}

export function TrajectoryPlotChart({
  chartData,
  currentFrame,
  selectedJoints,
  resolveLabel,
  trajectoryAdjustments,
  showVelocity,
  showNormalized,
  selectedRange,
  selectionHighlight,
  contextMenuPosition,
  onChartClick,
  onSelectionContextMenu,
  onSelectionPointerDown,
  onSelectionPointerMove,
  onSelectionPointerUp,
  onCreateSubtaskFromRange,
  onDismissContextMenu,
  selectionSurfaceRef,
}: TrajectoryPlotChartProps) {
  const [mousePos, setMousePos] = useState({ x: 0, y: 0 })

  const handleMouseMove = useCallback((event: React.MouseEvent<HTMLDivElement>) => {
    setMousePos({ x: event.clientX, y: event.clientY })
  }, [])

  return (
    <div
      ref={selectionSurfaceRef}
      data-testid="trajectory-selection-surface"
      className="relative min-h-0 flex-1 cursor-crosshair"
      onContextMenu={onSelectionContextMenu}
      onPointerDown={onSelectionPointerDown}
      onPointerMove={onSelectionPointerMove}
      onPointerUp={onSelectionPointerUp}
      onMouseMove={handleMouseMove}
    >
      <ResponsiveContainer
        width="100%"
        height="100%"
        minHeight={TRAJECTORY_CHART_MIN_HEIGHT}
        initialDimension={TRAJECTORY_CHART_INITIAL_DIMENSION}
      >
        <LineChart
          data={chartData}
          onClick={onChartClick}
          margin={{ top: 5, right: 20, left: 0, bottom: 5 }}
        >
          <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
          <XAxis dataKey="frame" stroke="hsl(var(--muted-foreground))" fontSize={12} />
          <YAxis
            stroke="hsl(var(--muted-foreground))"
            fontSize={12}
            domain={showNormalized && !showVelocity ? [0, 1] : ['auto', 'auto']}
          />
          <Tooltip
            isAnimationActive={false}
            content={<TrajectoryTooltipPortal mousePosition={mousePos} />}
            wrapperStyle={{ display: 'none' }}
          />

          {Array.from(trajectoryAdjustments.keys()).map((frameIdx) => (
            <ReferenceLine
              key={`adj-${frameIdx}`}
              x={frameIdx}
              stroke="#f97316"
              strokeWidth={2}
              strokeOpacity={0.6}
            />
          ))}

          <ReferenceLine
            x={currentFrame}
            stroke="hsl(var(--primary))"
            strokeWidth={2}
            strokeDasharray="4 4"
          />

          {selectedJoints.map((jointIdx) => (
            <Line
              key={jointIdx}
              type="monotone"
              dataKey={`joint_${jointIdx}`}
              name={resolveLabel(jointIdx)}
              stroke={JOINT_COLORS[jointIdx % JOINT_COLORS.length]}
              dot={false}
              strokeWidth={1.5}
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
      <TrajectoryPlotSelectionOverlay
        selectedRange={selectedRange}
        selectionHighlight={selectionHighlight}
        contextMenuPosition={contextMenuPosition}
        onCreateSubtaskFromRange={onCreateSubtaskFromRange}
        onDismissContextMenu={onDismissContextMenu}
      />
    </div>
  )
}
