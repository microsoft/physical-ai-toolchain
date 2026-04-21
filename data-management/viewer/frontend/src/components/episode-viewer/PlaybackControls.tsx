/**
 * Custom playback controls for the video player.
 */

import { ChevronLeft, ChevronRight, Pause, Play, SkipBack, SkipForward } from 'lucide-react'

import { SpeedControl } from '@/components/playback/SpeedControl'
import { Button } from '@/components/ui/button'
import { usePlaybackControls } from '@/stores'

interface PlaybackControlsProps {
  /** Current frame number */
  currentFrame: number
  /** Total frames in the video */
  totalFrames: number
  /** Duration in seconds */
  duration: number
  /** Frames per second */
  fps: number
}

/**
 * Playback control bar with play/pause, frame stepping, and speed control.
 */
export function PlaybackControls({
  currentFrame,
  totalFrames,
  duration,
  fps,
}: PlaybackControlsProps) {
  const { isPlaying, playbackSpeed, setCurrentFrame, togglePlayback, setPlaybackSpeed } =
    usePlaybackControls()

  // Format time as mm:ss
  const formatTime = (seconds: number) => {
    const mins = Math.floor(seconds / 60)
    const secs = Math.floor(seconds % 60)
    return `${mins}:${secs.toString().padStart(2, '0')}`
  }

  const currentTime = currentFrame / fps

  return (
    <div className="bg-muted flex items-center gap-4 rounded-lg p-2">
      {/* Frame navigation */}
      <div className="flex items-center gap-1">
        <Button variant="ghost" size="icon" onClick={() => setCurrentFrame(0)} title="Go to start">
          <SkipBack className="h-4 w-4" />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          onClick={() => setCurrentFrame(Math.max(0, currentFrame - 1))}
          title="Previous frame (←)"
        >
          <ChevronLeft className="h-4 w-4" />
        </Button>
      </div>

      {/* Play/Pause */}
      <Button
        variant="default"
        size="icon"
        onClick={togglePlayback}
        title={isPlaying ? 'Pause (Space)' : 'Play (Space)'}
      >
        {isPlaying ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
      </Button>

      {/* Forward navigation */}
      <div className="flex items-center gap-1">
        <Button
          variant="ghost"
          size="icon"
          onClick={() => setCurrentFrame(currentFrame + 1)}
          title="Next frame (→)"
        >
          <ChevronRight className="h-4 w-4" />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          onClick={() => setCurrentFrame(totalFrames - 1)}
          title="Go to end"
        >
          <SkipForward className="h-4 w-4" />
        </Button>
      </div>

      {/* Separator */}
      <div className="bg-border h-6 w-px" />

      {/* Time display */}
      <div className="min-w-[100px] font-mono text-sm">
        {formatTime(currentTime)} / {formatTime(duration)}
      </div>

      {/* Spacer */}
      <div className="flex-1" />

      {/* Speed control */}
      <SpeedControl speed={playbackSpeed} onSpeedChange={setPlaybackSpeed} />
    </div>
  )
}
