import { type ReactNode } from 'react'

import { cn } from '@/lib/utils'

interface PlaybackControlStripProps {
  currentFrame: number
  totalFrames: number
  controls: ReactNode
  slider: ReactNode
  className?: string
}

export function PlaybackControlStrip({
  currentFrame,
  totalFrames,
  controls,
  slider,
  className,
}: PlaybackControlStripProps) {
  const frameDigits = Math.max(totalFrames, currentFrame + 1).toString().length
  const counterWidthCh = frameDigits * 2 + 3

  return (
    <div className={cn('bg-muted flex flex-wrap items-center gap-3 rounded-lg p-3', className)}>
      <div className="flex shrink-0 flex-wrap items-center gap-3">{controls}</div>
      <div className="flex min-w-0 flex-1 basis-full items-center gap-3 md:basis-auto">
        <div className="min-w-0 flex-1">{slider}</div>
        <span
          className="text-muted-foreground shrink-0 text-right text-sm [font-variant-numeric:tabular-nums]"
          style={{ minWidth: `${counterWidthCh}ch` }}
        >
          {currentFrame + 1} / {totalFrames}
        </span>
      </div>
    </div>
  )
}
