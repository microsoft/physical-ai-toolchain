import { Plus, Timer, Trash2, Undo2 } from 'lucide-react'
import { useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Separator } from '@/components/ui/separator'
import { useEditStore, useEpisodeStore } from '@/stores'

/**
 * Toolbar for frame removal operations including single frame toggle and range selection
 */
export function FrameRemovalToolbar() {
  const [rangeStart, setRangeStart] = useState('')
  const [rangeEnd, setRangeEnd] = useState('')
  const [freqStart, setFreqStart] = useState('')
  const [freqEnd, setFreqEnd] = useState('')
  const [frequency, setFrequency] = useState('2')

  const currentFrame = useEpisodeStore((state) => state.currentFrame)
  const currentEpisode = useEpisodeStore((state) => state.currentEpisode)
  const removedFrames = useEditStore((state) => state.removedFrames)
  const toggleFrameRemoval = useEditStore((state) => state.toggleFrameRemoval)
  const addFrameRange = useEditStore((state) => state.addFrameRange)
  const addFramesByFrequency = useEditStore((state) => state.addFramesByFrequency)
  const clearRemovedFrames = useEditStore((state) => state.clearRemovedFrames)

  const totalFrames = currentEpisode?.meta.length ?? 100

  const isCurrentFrameRemoved = removedFrames.has(currentFrame)

  const handleToggleCurrent = () => {
    toggleFrameRemoval(currentFrame)
  }

  const handleAddRange = () => {
    const start = parseInt(rangeStart, 10)
    const end = parseInt(rangeEnd, 10)
    if (!isNaN(start) && !isNaN(end) && start <= end && start >= 0) {
      addFrameRange(start, end)
      setRangeStart('')
      setRangeEnd('')
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleAddRange()
    }
  }

  const handleAddByFrequency = () => {
    const start = freqStart ? parseInt(freqStart, 10) : 0
    const end = freqEnd ? parseInt(freqEnd, 10) : totalFrames - 1
    const freq = parseInt(frequency, 10)
    if (!isNaN(start) && !isNaN(end) && !isNaN(freq) && freq >= 1 && start <= end && start >= 0) {
      addFramesByFrequency(start, end, freq)
      setFreqStart('')
      setFreqEnd('')
    }
  }

  const handleFrequencyKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleAddByFrequency()
    }
  }

  // Calculate how many frames would be removed with current frequency settings
  const getFrequencyPreview = () => {
    const start = freqStart ? parseInt(freqStart, 10) : 0
    const end = freqEnd ? parseInt(freqEnd, 10) : totalFrames - 1
    const freq = parseInt(frequency, 10)
    if (isNaN(start) || isNaN(end) || isNaN(freq) || freq < 1 || start > end) return 0
    return Math.floor((end - start) / freq) + 1
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h4 className="text-sm font-medium">Frame Removal</h4>
        {removedFrames.size > 0 && (
          <Badge variant="secondary">
            {removedFrames.size} frame{removedFrames.size !== 1 ? 's' : ''} removed
          </Badge>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant={isCurrentFrameRemoved ? 'destructive' : 'outline'}
          size="sm"
          onClick={handleToggleCurrent}
        >
          <Trash2 className="mr-1 h-4 w-4" />
          {isCurrentFrameRemoved ? 'Restore' : 'Remove'} Frame {currentFrame}
        </Button>

        {removedFrames.size > 0 && (
          <Button variant="ghost" size="sm" onClick={clearRemovedFrames}>
            <Undo2 className="mr-1 h-4 w-4" />
            Clear All
          </Button>
        )}
      </div>

      <div className="flex items-center gap-2">
        <Label className="shrink-0 text-xs">Range:</Label>
        <Input
          type="number"
          className="h-8 w-20"
          placeholder="Start"
          value={rangeStart}
          min={0}
          onChange={(e) => setRangeStart(e.target.value)}
          onKeyDown={handleKeyDown}
        />
        <span className="text-muted-foreground">to</span>
        <Input
          type="number"
          className="h-8 w-20"
          placeholder="End"
          value={rangeEnd}
          min={0}
          onChange={(e) => setRangeEnd(e.target.value)}
          onKeyDown={handleKeyDown}
        />
        <Button
          size="sm"
          variant="outline"
          onClick={handleAddRange}
          disabled={!rangeStart || !rangeEnd}
        >
          <Plus className="mr-1 h-4 w-4" />
          Add
        </Button>
      </div>

      <Separator className="my-3" />

      {/* Frequency-based removal */}
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <Timer className="text-muted-foreground h-4 w-4" />
          <Label className="text-xs font-medium">Remove by Frequency</Label>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Label className="shrink-0 text-xs">Every</Label>
          <Input
            type="number"
            className="h-8 w-16"
            value={frequency}
            min={1}
            onChange={(e) => setFrequency(e.target.value)}
            onKeyDown={handleFrequencyKeyDown}
          />
          <Label className="shrink-0 text-xs">frames, from</Label>
          <Input
            type="number"
            className="h-8 w-20"
            placeholder="0"
            value={freqStart}
            min={0}
            onChange={(e) => setFreqStart(e.target.value)}
            onKeyDown={handleFrequencyKeyDown}
          />
          <span className="text-muted-foreground">to</span>
          <Input
            type="number"
            className="h-8 w-20"
            placeholder={`${totalFrames - 1}`}
            value={freqEnd}
            min={0}
            onChange={(e) => setFreqEnd(e.target.value)}
            onKeyDown={handleFrequencyKeyDown}
          />
        </div>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={handleAddByFrequency}
            disabled={parseInt(frequency, 10) < 1}
          >
            <Plus className="mr-1 h-4 w-4" />
            Apply
          </Button>
          {getFrequencyPreview() > 0 && (
            <span className="text-muted-foreground text-xs">
              Will remove {getFrequencyPreview()} frames
            </span>
          )}
        </div>
      </div>
    </div>
  )
}
