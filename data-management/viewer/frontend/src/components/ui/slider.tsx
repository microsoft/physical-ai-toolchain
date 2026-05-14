import * as SliderPrimitive from '@radix-ui/react-slider'
import * as React from 'react'

import { cn } from '@/lib/utils'

function Slider({ className, ref, ...props }: React.ComponentProps<typeof SliderPrimitive.Root>) {
  return (
    <SliderPrimitive.Root
      ref={ref}
      className={cn('relative flex w-full touch-none items-center select-none', className)}
      {...props}
    />
  )
}

function SliderTrack({
  className,
  ref,
  ...props
}: React.ComponentProps<typeof SliderPrimitive.Track>) {
  return (
    <SliderPrimitive.Track
      ref={ref}
      className={cn('bg-muted relative grow overflow-hidden rounded-full', className)}
      {...props}
    />
  )
}

function SliderRange({
  className,
  ref,
  ...props
}: React.ComponentProps<typeof SliderPrimitive.Range>) {
  return (
    <SliderPrimitive.Range
      ref={ref}
      className={cn('bg-primary absolute h-full', className)}
      {...props}
    />
  )
}

function SliderThumb({
  className,
  ref,
  ...props
}: React.ComponentProps<typeof SliderPrimitive.Thumb>) {
  return (
    <SliderPrimitive.Thumb
      ref={ref}
      className={cn(
        'border-primary bg-background focus-visible:ring-ring block h-5 w-5 rounded-full border-2 shadow-sm transition-colors focus-visible:ring-1 focus-visible:outline-hidden disabled:pointer-events-none disabled:opacity-50',
        className,
      )}
      {...props}
    />
  )
}

export { Slider, SliderRange, SliderThumb, SliderTrack }
