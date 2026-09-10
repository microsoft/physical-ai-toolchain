import { useEffect, useMemo, useRef } from 'react'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'

import { cn } from '@/lib/utils'

import { type EndEffectorTrajectory, getEndEffectorViewBounds } from './end-effector-trajectories'

interface EndEffectorTrajectoryPlotProps {
  trajectories: readonly EndEffectorTrajectory[]
  currentSampleIndex?: number
  emptyMessage?: string
  showHeader?: boolean
  className?: string
  frameClassName?: string
}

export function EndEffectorTrajectoryPlot({
  trajectories,
  currentSampleIndex,
  emptyMessage = 'No end-effector trajectory data available',
  showHeader = true,
  className,
  frameClassName,
}: EndEffectorTrajectoryPlotProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const currentMarkersRef = useRef(new Map<string, THREE.Mesh>())
  const renderedTrajectories = useMemo(
    () =>
      trajectories.map((trajectory) => ({
        ...trajectory,
        points: trajectory.points.map(([x, y, z]) => new THREE.Vector3(x, y, z)),
      })),
    [trajectories],
  )
  const viewBounds = useMemo(() => getEndEffectorViewBounds(trajectories), [trajectories])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || (!canvas.getContext('webgl2') && !canvas.getContext('webgl'))) return

    const scene = new THREE.Scene()
    const renderer = new THREE.WebGLRenderer({
      canvas,
      antialias: true,
      alpha: true,
      preserveDrawingBuffer: true,
    })
    renderer.setPixelRatio(Math.min(globalThis.devicePixelRatio, 2))
    const maxSpan = Math.max(viewBounds?.maxSpan ?? 0.3, 0.18)
    const center = new THREE.Vector3(...(viewBounds?.center ?? [0, 0.14, 0]))
    const halfVerticalFov = (38 * Math.PI) / 360
    const cameraDistance = (maxSpan / 2 / Math.tan(halfVerticalFov)) * 1.8
    const camera = new THREE.PerspectiveCamera(
      38,
      1,
      Math.max(cameraDistance / 1_000, 0.001),
      Math.max(cameraDistance * 10, 10),
    )
    camera.position
      .copy(center)
      .addScaledVector(new THREE.Vector3(1, 0.75, 1).normalize(), cameraDistance)
    const controls = new OrbitControls(camera, canvas)
    controls.enableDamping = true
    controls.target.copy(center)
    controls.minDistance = maxSpan * 0.2
    controls.maxDistance = Math.max(cameraDistance * 4, 1.5)

    const grid = new THREE.GridHelper(Math.max(maxSpan * 2, 0.8), 16, 0x64748b, 0x334155)
    grid.position.set(center.x, 0, center.z)
    scene.add(grid)
    scene.add(new THREE.AxesHelper(0.18))

    for (const trajectory of renderedTrajectories) {
      if (trajectory.points.length === 0) continue
      const geometry = new THREE.BufferGeometry().setFromPoints(trajectory.points)
      scene.add(
        new THREE.Line(geometry, new THREE.LineBasicMaterial({ color: trajectory.lineColor })),
      )
      const current = new THREE.Mesh(
        new THREE.SphereGeometry(0.009, 20, 20),
        new THREE.MeshBasicMaterial({ color: trajectory.markerColor }),
      )
      current.position.copy(trajectory.points[0])
      currentMarkersRef.current.set(trajectory.id, current)
      scene.add(current)
    }

    let animationFrame = 0
    const resize = () => {
      const width = Math.max(canvas.clientWidth, 1)
      const height = Math.max(canvas.clientHeight, 1)
      renderer.setSize(width, height, false)
      camera.aspect = width / height
      camera.updateProjectionMatrix()
    }
    const render = () => {
      controls.update()
      renderer.render(scene, camera)
      animationFrame = requestAnimationFrame(render)
    }
    resize()
    globalThis.addEventListener('resize', resize)
    render()
    return () => {
      cancelAnimationFrame(animationFrame)
      globalThis.removeEventListener('resize', resize)
      currentMarkersRef.current.clear()
      controls.dispose()
      scene.traverse((object) => {
        if (object instanceof THREE.Mesh || object instanceof THREE.Line) {
          object.geometry.dispose()
          const materials = Array.isArray(object.material) ? object.material : [object.material]
          materials.forEach((material) => material.dispose())
        }
      })
      renderer.dispose()
    }
  }, [renderedTrajectories, viewBounds])

  useEffect(() => {
    for (const trajectory of renderedTrajectories) {
      const marker = currentMarkersRef.current.get(trajectory.id)
      if (!marker || trajectory.points.length === 0) continue
      const selectedIndex = Math.max(
        0,
        Math.min(currentSampleIndex ?? trajectory.points.length - 1, trajectory.points.length - 1),
      )
      marker.position.copy(trajectory.points[selectedIndex])
    }
  }, [currentSampleIndex, renderedTrajectories])

  const hasPoints = trajectories.some((trajectory) => trajectory.points.length > 0)

  return (
    <section aria-label="End-effector trajectory" className={cn('min-w-0 space-y-2', className)}>
      {showHeader && (
        <div className="flex items-baseline justify-between gap-3">
          <h3 className="text-sm font-semibold">End-effector trajectory</h3>
          <span className="text-muted-foreground text-xs">metres</span>
        </div>
      )}
      <div
        aria-label="Trajectory plot frame"
        className={cn(
          'bg-muted/30 relative aspect-4/3 w-full overflow-hidden border',
          frameClassName,
        )}
      >
        <canvas ref={canvasRef} className="h-full w-full" aria-label="3D trajectory plot" />
        {!hasPoints && (
          <div className="text-muted-foreground pointer-events-none absolute inset-0 flex items-center justify-center text-sm">
            {emptyMessage}
          </div>
        )}
        {trajectories.length > 1 && (
          <div className="bg-background/85 pointer-events-none absolute bottom-2 left-2 space-y-1 border px-2 py-1 text-[10px]">
            {trajectories.map((trajectory) => (
              <div key={trajectory.id} className="flex items-center gap-1.5">
                <span
                  className="h-2 w-2 rounded-full"
                  style={{ backgroundColor: trajectory.markerColor }}
                />
                {trajectory.label}
              </div>
            ))}
          </div>
        )}
        <div className="bg-background/85 pointer-events-none absolute right-2 bottom-2 border px-2 py-1 text-[10px]">
          X red · Y green · Z blue
        </div>
      </div>
    </section>
  )
}
