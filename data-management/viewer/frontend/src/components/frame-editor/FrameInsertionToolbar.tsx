import { Layers, Plus, PlusCircle, Timer, Undo2 } from 'lucide-react'
import { useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Separator } from '@/components/ui/separator'
import { useEpisodeStore } from '@/stores'
import { useFrameInsertionState } from '@/stores'

/**
 * Toolbar for frame insertion operations including single frame, range, and frequency-based insertion
 */
export function FrameInsertionToolbar() {
  const [rangeStart, setRangeStart] = useState('')
  const [rangeEnd, setRangeEnd] = useState('')
  const [freqStart, setFreqStart] = useState('')
  const [freqEnd, setFreqEnd] = useState('')
  const [frequency, setFrequency] = useState('2')
  const [interpolationFactor, setInterpolationFactor] = useState('0.5')

  const currentFrame = useEpisodeStore((state) => state.currentFrame)
  const currentEpisode = useEpisodeStore((state) => state.currentEpisode)
  const { insertedFrames, insertFrame, removeInsertedFrame, clearInsertedFrames } =
    useFrameInsertionState()

  const totalFrames = currentEpisode?.meta.length ?? 100

  const isCurrentFrameInserted = insertedFrames.has(currentFrame)
  const factor = parseFloat(interpolationFactor) || 0.5

  const handleToggleCurrent = () => {
    if (isCurrentFrameInserted) {
      removeInsertedFrame(currentFrame)
    } else if (currentFrame < totalFrames - 1) {
      insertFrame(currentFrame, factor)
    }
  }

  const handleAddRange = () => {
    const start = parseInt(rangeStart, 10)
    const end = parseInt(rangeEnd, 10)
    if (!isNaN(start) && !isNaN(end) && start <= end && start >= 0 && end < totalFrames - 1) {
      for (let i = start; i <= end; i++) {
        insertFrame(i, factor)
      }
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
    const end = freqEnd ? parseInt(freqEnd, 10) : totalFrames - 2
    const freq = parseInt(frequency, 10)
    if (!isNaN(start) && !isNaN(end) && !isNaN(freq) && freq >= 1 && start <= end && start >= 0) {
      for (let i = start; i <= end; i += freq) {
        if (i < totalFrames - 1) {
          insertFrame(i, factor)
        }
      }
      setFreqStart('')
      setFreqEnd('')
    }
  }

  const handleFrequencyKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleAddByFrequency()
    }
  }

  // Calculate how many frames would be inserted with current frequency settings
  const getFrequencyPreview = () => {
    const start = freqStart ? parseInt(freqStart, 10) : 0
    const end = freqEnd ? parseInt(freqEnd, 10) : totalFrames - 2
    const freq = parseInt(frequency, 10)
    if (isNaN(start) || isNaN(end) || isNaN(freq) || freq < 1 || start > end) return 0
    return Math.floor((end - start) / freq) + 1
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h4 className="text-sm font-medium">Frame Insertion</h4>
        {insertedFrames.size > 0 && (
          <Badge variant="secondary" className="bg-blue-100 text-blue-700">
            {insertedFrames.size} frame{insertedFrames.size !== 1 ? 's' : ''} inserted
          </Badge>
        )}
      </div>

      {/* Interpolation factor control */}
      <div className="flex items-center gap-2">
        <Label className="shrink-0 text-xs">Blend Factor:</Label>
        <Input
          type="number"
          className="h-8 w-20"
          value={interpolationFactor}
          min={0}
          max={1}
          step={0.1}
          onChange={(e) => setInterpolationFactor(e.target.value)}
        />
        <span className="text-muted-foreground text-xs">(0=first frame, 1=second frame)</span>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant={isCurrentFrameInserted ? 'default' : 'outline'}
          size="sm"
          onClick={handleToggleCurrent}
          disabled={!isCurrentFrameInserted && currentFrame >= totalFrames - 1}
          className={isCurrentFrameInserted ? 'bg-blue-500 hover:bg-blue-600' : ''}
        >
          <PlusCircle className="mr-1 h-4 w-4" />
          {isCurrentFrameInserted ? 'Remove' : 'Insert After'} Frame {currentFrame}
        </Button>

        {insertedFrames.size > 0 && (
          <Button variant="ghost" size="sm" onClick={clearInsertedFrames}>
            <Undo2 className="mr-1 h-4 w-4" />
            Clear All
          </Button>
        )}
      </div>

      <Separator className="my-3" />

      {/* Range insertion */}
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <Layers className="text-muted-foreground h-4 w-4" />
          <Label className="text-xs font-medium">Insert After Range</Label>
        </div>
        <div className="flex items-center gap-2">
          <Label className="shrink-0 text-xs">From:</Label>
          <Input
            type="number"
            className="h-8 w-20"
            placeholder="Start"
            value={rangeStart}
            min={0}
            max={totalFrames - 2}
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
            max={totalFrames - 2}
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
      </div>

      <Separator className="my-3" />

      {/* Frequency-based insertion */}
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <Timer className="text-muted-foreground h-4 w-4" />
          <Label className="text-xs font-medium">Insert by Frequency</Label>
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
            placeholder={`${totalFrames - 2}`}
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
              Will insert {getFrequencyPreview()} frames
            </span>
          )}
        </div>
      </div>
    </div>
  )
}
