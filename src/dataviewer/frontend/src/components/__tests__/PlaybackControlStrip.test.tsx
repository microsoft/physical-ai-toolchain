import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { PlaybackControlStrip } from '@/components/playback/PlaybackControlStrip'

describe('PlaybackControlStrip', () => {
  it('keeps the timeline shrink-safe and reserves stable width for the frame counter', () => {
    render(
      <PlaybackControlStrip
        currentFrame={7}
        totalFrames={385}
        controls={
          <>
            <button type="button">Play</button>
            <button type="button">Loop</button>
          </>
        }
        slider={<input type="range" aria-label="Frame position" />}
      />,
    )

    const slider = screen.getByRole('slider', { name: 'Frame position' })
    const counter = screen.getByText('8 / 385')

    expect(slider.parentElement).toHaveClass('min-w-0', 'flex-1')
    expect(counter).toHaveClass('shrink-0', 'text-right', '[font-variant-numeric:tabular-nums]')
    expect(counter).toHaveStyle({ minWidth: '9ch' })
  })

  it('wraps control groups before the strip can overflow a narrow container', () => {
    const { container } = render(
      <PlaybackControlStrip
        currentFrame={337}
        totalFrames={385}
        controls={
          <>
            <button type="button">Pause</button>
            <button type="button">Auto</button>
            <button type="button">Loop</button>
          </>
        }
        slider={<input type="range" aria-label="Frame position" />}
      />,
    )

    expect(container.firstChild).toHaveClass('flex-wrap')
  })
})