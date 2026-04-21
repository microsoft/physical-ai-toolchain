/**
 * Camera selector dropdown for multi-camera episode viewing.
 */

import { Camera, ChevronDown } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

interface CameraSelectorProps {
  /** Available camera names */
  cameras: string[]
  /** Currently selected camera */
  selectedCamera: string
  /** Callback when camera is selected */
  onSelectCamera: (camera: string) => void
}

/**
 * Dropdown for selecting which camera view to display.
 */
export function CameraSelector({ cameras, selectedCamera, onSelectCamera }: CameraSelectorProps) {
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
    <div className="relative" ref={dropdownRef}>
      <Button
        variant="outline"
        size="sm"
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center gap-2"
      >
        <Camera className="h-4 w-4" />
        <span>{formatCameraName(selectedCamera)}</span>
        <ChevronDown className={cn('h-4 w-4 transition-transform', isOpen && 'rotate-180')} />
      </Button>

      {isOpen && (
        <div className="bg-popover absolute top-full left-0 z-50 mt-1 min-w-[150px] rounded-md border shadow-lg">
          {cameras.map((camera) => (
            <button
              key={camera}
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
          ))}
        </div>
      )}
    </div>
  )
}
