/**
 * Camera selector dropdown for multi-camera episode viewing.
 */

import { Camera, ChevronDown } from 'lucide-react'
import { type KeyboardEvent, useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

interface CameraSelectorProps {
  /** Available camera names */
  cameras: string[]
  /** Currently selected camera */
  selectedCamera: string
  /** Callback when camera is selected */
  onSelectCamera: (camera: string) => void
  selectedCameras?: string[]
  onSelectionChange?: (cameras: string[]) => void
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
}: CameraSelectorProps) {
  const [isOpen, setIsOpen] = useState(false)
  const dropdownRef = useRef<HTMLDivElement>(null)
  const checkedCameras = selectedCameras?.length ? selectedCameras : [selectedCamera]
  const closeOnEscape = (event: KeyboardEvent<HTMLInputElement | HTMLButtonElement>) => {
    if (event.key === 'Escape') {
      setIsOpen(false)
      dropdownRef.current?.querySelector('button')?.focus()
    }
  }

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

  if (cameras.length === 0) {
    return (
      <div className="text-muted-foreground flex items-center gap-2 text-sm">
        <Camera className="h-4 w-4" />
        <span>No cameras available</span>
      </div>
    )
  }

  if (cameras.length === 1) {
    return (
      <div className="flex items-center gap-2 text-sm">
        <Camera className="h-4 w-4" />
        <span>{formatCameraName(cameras[0])}</span>
      </div>
    )
  }

  return (
    <div role="group" aria-label="Camera selection" className="relative" ref={dropdownRef}>
      <Button
        variant="outline"
        size="sm"
        onClick={() => setIsOpen(!isOpen)}
        onKeyDown={closeOnEscape}
        aria-expanded={isOpen}
        className="flex items-center gap-2"
      >
        <Camera className="h-4 w-4" />
        <span>
          {onSelectionChange
            ? `${checkedCameras.length} ${checkedCameras.length === 1 ? 'camera' : 'cameras'}`
            : formatCameraName(selectedCamera)}
        </span>
        <ChevronDown className={cn('h-4 w-4 transition-transform', isOpen && 'rotate-180')} />
      </Button>

      {isOpen && (
        <div className="bg-popover absolute top-full left-0 z-50 mt-1 min-w-[150px] rounded-md border shadow-lg">
          {cameras.map((camera) =>
            onSelectionChange ? (
              <label
                key={camera}
                className="hover:bg-accent flex items-center gap-2 px-3 py-2 text-sm"
              >
                <input
                  type="checkbox"
                  checked={checkedCameras.includes(camera)}
                  onKeyDown={closeOnEscape}
                  disabled={checkedCameras.includes(camera) && checkedCameras.length === 1}
                  onChange={() =>
                    onSelectionChange(
                      checkedCameras.includes(camera)
                        ? checkedCameras.filter((selected) => selected !== camera)
                        : [...checkedCameras, camera],
                    )
                  }
                />
                {formatCameraName(camera)}
              </label>
            ) : (
              <button
                key={camera}
                onKeyDown={closeOnEscape}
                onClick={() => {
                  onSelectCamera(camera)
                  setIsOpen(false)
                }}
                className={cn(
                  'hover:bg-accent w-full px-3 py-2 text-left text-sm transition-colors',
                  camera === selectedCamera && 'bg-accent',
                )}
              >
                {formatCameraName(camera)}
              </button>
            ),
          )}
        </div>
      )}
    </div>
  )
}
