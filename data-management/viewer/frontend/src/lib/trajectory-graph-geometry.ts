export interface TrajectoryPlotArea {
  left: number
  width: number
}

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(value, max))
}

export function resolveTrajectoryPlotArea(surface: HTMLElement | null): TrajectoryPlotArea | null {
  if (!surface) {
    return null
  }

  const surfaceWidth = surface.getBoundingClientRect().width

  if (!Number.isFinite(surfaceWidth) || surfaceWidth <= 0) {
    return null
  }

  const clipRect = surface.parentElement?.querySelector('svg defs clipPath rect')
  const rawLeft = clipRect ? Number(clipRect.getAttribute('x') ?? 'NaN') : Number.NaN
  const rawWidth = clipRect ? Number(clipRect.getAttribute('width') ?? 'NaN') : Number.NaN

  if (!Number.isFinite(rawLeft) || !Number.isFinite(rawWidth) || rawWidth <= 0) {
    return { left: 0, width: surfaceWidth }
  }

  const left = clamp(rawLeft, 0, surfaceWidth)
  const width = clamp(rawWidth, 0, Math.max(surfaceWidth - left, 0))

  if (width <= 0) {
    return { left: 0, width: surfaceWidth }
  }

  return { left, width }
}

export function resolveSurfaceFrame(
  surfaceX: number,
  totalFrames: number,
  plotArea: TrajectoryPlotArea | null,
): number {
  const maxFrame = Math.max(totalFrames - 1, 0)

  if (maxFrame === 0 || !plotArea || plotArea.width <= 0) {
    return 0
  }

  const relativeX = clamp(surfaceX - plotArea.left, 0, plotArea.width)
  const ratio = relativeX / plotArea.width

  return Math.round(ratio * maxFrame)
}

export function resolveSelectionHighlightStyle(
  range: [number, number],
  totalFrames: number,
  plotArea: TrajectoryPlotArea | null,
): { left: number; width: number } | null {
  const maxFrame = Math.max(totalFrames - 1, 0)

  if (!plotArea || plotArea.width <= 0 || maxFrame <= 0) {
    return null
  }

  const [start, end] = range[0] <= range[1] ? range : [range[1], range[0]]
  const clampedStart = clamp(start, 0, maxFrame)
  const clampedEnd = clamp(end, clampedStart, maxFrame)
  const frameStep = plotArea.width / maxFrame
  const startX = plotArea.left + (clampedStart / maxFrame) * plotArea.width
  const endX = plotArea.left + (clampedEnd / maxFrame) * plotArea.width
  const left = Math.max(plotArea.left, startX - frameStep / 2)
  const right = Math.min(plotArea.left + plotArea.width, endX + frameStep / 2)

  return {
    left,
    width: Math.max(right - left, Math.min(frameStep, plotArea.width)),
  }
}
