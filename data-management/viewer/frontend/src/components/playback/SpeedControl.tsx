import { ChevronDown } from 'lucide-react'
import { useCallback, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'

const SPEED_PRESETS = [0.25, 0.5, 0.75, 1, 1.5, 2, 3, 5] as const

const MIN_SPEED = 0.1
const MAX_SPEED = 10

interface SpeedControlProps {
  speed: number
  onSpeedChange: (speed: number) => void
  compact?: boolean
}

function formatSpeed(speed: number): string {
  return speed % 1 === 0 ? `${speed}x` : `${speed}x`
}

function clampSpeed(value: number): number {
  return Math.round(Math.max(MIN_SPEED, Math.min(MAX_SPEED, value)) * 100) / 100
}

export function SpeedControl({ speed, onSpeedChange, compact = false }: SpeedControlProps) {
  const [open, setOpen] = useState(false)
  const [customValue, setCustomValue] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  const handlePresetClick = useCallback(
    (preset: number) => {
      onSpeedChange(preset)
      setOpen(false)
    },
    [onSpeedChange],
  )

  const handleCustomSubmit = useCallback(() => {
    const parsed = Number.parseFloat(customValue)
    if (!Number.isNaN(parsed) && parsed > 0) {
      onSpeedChange(clampSpeed(parsed))
      setCustomValue('')
      setOpen(false)
    }
  }, [customValue, onSpeedChange])

  const isPreset = SPEED_PRESETS.includes(speed as (typeof SPEED_PRESETS)[number])

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          size="sm"
          variant={isPreset ? 'outline' : 'secondary'}
          aria-label={`Playback speed: ${formatSpeed(speed)}`}
          className={compact ? 'h-8 gap-0.5 px-2 text-xs' : 'gap-1 px-2'}
        >
          {formatSpeed(speed)}
          <ChevronDown className="h-3 w-3 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-52 p-3" align="start">
        <div className="space-y-3">
          <div className="grid grid-cols-4 gap-1.5">
            {SPEED_PRESETS.map((preset) => (
              <Button
                key={preset}
                size="sm"
                variant={speed === preset ? 'default' : 'outline'}
                onClick={() => handlePresetClick(preset)}
                className="h-7 px-1 text-xs"
              >
                {formatSpeed(preset)}
              </Button>
            ))}
          </div>
          <div className="flex items-center gap-2">
            <Input
              ref={inputRef}
              type="number"
              min={MIN_SPEED}
              max={MAX_SPEED}
              step={0.25}
              placeholder="Custom"
              value={customValue}
              onChange={(e) => setCustomValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleCustomSubmit()
              }}
              className="h-7 text-xs"
              aria-label="Custom playback speed"
            />
            <Button
              size="sm"
              variant="outline"
              onClick={handleCustomSubmit}
              disabled={!customValue || Number.isNaN(Number.parseFloat(customValue))}
              className="h-7 shrink-0 px-2 text-xs"
            >
              Set
            </Button>
          </div>
          <p className="text-[0.65rem] text-muted-foreground">
            {MIN_SPEED}x – {MAX_SPEED}x
          </p>
        </div>
      </PopoverContent>
    </Popover>
  )
}
