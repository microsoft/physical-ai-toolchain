/**
 * Camera selector dropdown for multi-camera episode viewing.
 */

import { Box, Camera, Check, ChevronDown } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

interface CameraSelectorProps {
  /** Available camera names */
  cameras: string[]
  /** Currently selected camera */
  selectedCamera?: string
  /** Callback when camera is selected */
  onSelectCamera?: (camera: string) => void
  /** Cameras displayed together. Defaults to selectedCamera for legacy callers. */
  selectedCameras?: string[]
  /** Callback when the checked camera set changes. */
  onSelectionChange?: (cameras: string[]) => void
  /** Whether the end-effector spatial view is active. */
  endEffectorViewSelected?: boolean
  /** Toggle the end-effector spatial view independently of camera views. */
  onEndEffectorViewSelectionChange?: (selected: boolean) => void
}

/**
 * Dropdown for selecting which camera view to display.
 */
export function CameraSelector({
  cameras,
  selectedCamera,
  onSelectCamera,
  selectedCameras,
  onSelectionChange,
  endEffectorViewSelected = false,
  onEndEffectorViewSelectionChange,
}: CameraSelectorProps) {
  const [isOpen, setIsOpen] = useState(false)
  const dropdownRef = useRef<HTMLDivElement>(null)

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setIsOpen(false)
      }
    }

    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  // Format camera name for display
  const formatCameraName = (name: string) => {
    return name
      .replace(/^observation\.images\./, '')
      .replace(/_/g, ' ')
      .replace(/\b\w/g, (c) => c.toUpperCase())
  }

  if (cameras.length === 0 && !onEndEffectorViewSelectionChange) {
    return (
      <div className="text-muted-foreground flex items-center gap-2 text-sm">
        <Camera className="h-4 w-4" />
        <span>No cameras available</span>
      </div>
    )
  }

  if (cameras.length === 1 && !onEndEffectorViewSelectionChange) {
    return (
      <div className="flex items-center gap-2 text-sm">
        <Camera className="h-4 w-4" />
        <span>{formatCameraName(cameras[0])}</span>
      </div>
    )
  }

  const checkedCameras = selectedCameras?.length
    ? selectedCameras
    : selectedCamera
      ? [selectedCamera]
      : cameras.slice(0, 1)
  const isMultiSelect = Boolean(onSelectionChange)
  const cameraLabel = isMultiSelect
    ? `${checkedCameras.length} camera${checkedCameras.length === 1 ? '' : 's'}`
    : formatCameraName(selectedCamera ?? checkedCameras[0] ?? '')
  const buttonLabel = `${cameraLabel}${endEffectorViewSelected ? ' + 3D' : ''}`

  const toggleCamera = (camera: string) => {
    if (!onSelectionChange) {
      onSelectCamera?.(camera)
      setIsOpen(false)
      return
    }
    const selected = checkedCameras.includes(camera)
    if (selected && checkedCameras.length === 1) {
      setIsOpen(false)
      return
    }
    onSelectionChange(
      selected ? checkedCameras.filter((item) => item !== camera) : [...checkedCameras, camera],
    )
  }

  return (
    <div className="relative" ref={dropdownRef}>
      <Button
        variant="outline"
        size="sm"
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center gap-2"
      >
        <Camera className="h-4 w-4" />
        <span>{buttonLabel}</span>
        <ChevronDown className={cn('h-4 w-4 transition-transform', isOpen && 'rotate-180')} />
      </Button>

      {isOpen && (
        <div className="bg-popover absolute top-full left-0 z-50 mt-1 min-w-[150px] rounded-md border shadow-lg">
          {cameras.map((camera) => (
            <button
              key={camera}
              type="button"
              role={isMultiSelect ? 'menuitemcheckbox' : undefined}
              aria-checked={isMultiSelect ? checkedCameras.includes(camera) : undefined}
              onClick={() => toggleCamera(camera)}
              className={cn(
                'hover:bg-accent flex w-full items-center gap-2 px-3 py-2 text-left text-sm transition-colors',
                checkedCameras.includes(camera) && 'bg-accent',
              )}
            >
              {isMultiSelect && (
                <Check
                  className={cn(
                    'h-4 w-4',
                    checkedCameras.includes(camera) ? 'opacity-100' : 'opacity-0',
                  )}
                />
              )}
              <span>{formatCameraName(camera)}</span>
            </button>
          ))}
          {onEndEffectorViewSelectionChange && (
            <button
              type="button"
              role="menuitemcheckbox"
              aria-checked={endEffectorViewSelected}
              onClick={() => {
                onEndEffectorViewSelectionChange(!endEffectorViewSelected)
                setIsOpen(false)
              }}
              className={cn(
                'hover:bg-accent flex w-full items-center gap-2 border-t px-3 py-2 text-left text-sm transition-colors',
                endEffectorViewSelected && 'bg-accent',
              )}
            >
              {endEffectorViewSelected ? (
                <Check className="h-4 w-4" />
              ) : (
                <Box className="h-4 w-4" />
              )}
              <span>End effector 3D</span>
            </button>
          )}
        </div>
      )}
    </div>
  )
}
