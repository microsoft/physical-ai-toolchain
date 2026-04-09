import fc from 'fast-check'
import { describe, expect, it } from 'vitest'

import {
  clampFrameToPlaybackRange,
  computeEffectiveFps,
  computePlaybackTarget,
  needsSeekBeforePlay,
  type PlaybackRange,
  resolvePlaybackRange,
  resolvePlaybackTick,
} from '../playback-utils'

const positiveInt = fc.integer({ min: 1, max: 100_000 })
const nonNegativeInt = fc.integer({ min: 0, max: 100_000 })
const playbackRange = fc
  .tuple(nonNegativeInt, nonNegativeInt)
  .map(([a, b]): PlaybackRange => [a, b])

describe('resolvePlaybackRange', () => {
  it('always produces start <= end', () => {
    fc.assert(
      fc.property(positiveInt, fc.option(playbackRange, { nil: null }), (totalFrames, range) => {
        const [start, end] = resolvePlaybackRange(totalFrames, range)
        expect(start).toBeLessThanOrEqual(end)
      }),
    )
  })

  it('bounds output within [0, totalFrames-1]', () => {
    fc.assert(
      fc.property(positiveInt, fc.option(playbackRange, { nil: null }), (totalFrames, range) => {
        const [start, end] = resolvePlaybackRange(totalFrames, range)
        expect(start).toBeGreaterThanOrEqual(0)
        expect(end).toBeLessThanOrEqual(Math.max(totalFrames - 1, 0))
      }),
    )
  })

  it('returns full range when range is null', () => {
    fc.assert(
      fc.property(positiveInt, (totalFrames) => {
        const [start, end] = resolvePlaybackRange(totalFrames, null)
        expect(start).toBe(0)
        expect(end).toBe(totalFrames - 1)
      }),
    )
  })
})

describe('clampFrameToPlaybackRange', () => {
  it('result is always within the resolved range', () => {
    fc.assert(
      fc.property(
        nonNegativeInt,
        positiveInt,
        fc.option(playbackRange, { nil: null }),
        (frame, totalFrames, range) => {
          const clamped = clampFrameToPlaybackRange(frame, totalFrames, range)
          const [start, end] = resolvePlaybackRange(totalFrames, range)
          expect(clamped).toBeGreaterThanOrEqual(start)
          expect(clamped).toBeLessThanOrEqual(end)
        },
      ),
    )
  })

  it('is idempotent', () => {
    fc.assert(
      fc.property(
        nonNegativeInt,
        positiveInt,
        fc.option(playbackRange, { nil: null }),
        (frame, totalFrames, range) => {
          const once = clampFrameToPlaybackRange(frame, totalFrames, range)
          const twice = clampFrameToPlaybackRange(once, totalFrames, range)
          expect(twice).toBe(once)
        },
      ),
    )
  })
})

describe('resolvePlaybackTick', () => {
  it('frame is always within the resolved range', () => {
    fc.assert(
      fc.property(
        nonNegativeInt,
        positiveInt,
        fc.option(playbackRange, { nil: null }),
        fc.boolean(),
        (frame, totalFrames, range, autoLoop) => {
          const result = resolvePlaybackTick(frame, totalFrames, range, autoLoop)
          const [start, end] = resolvePlaybackRange(totalFrames, range)
          expect(result.frame).toBeGreaterThanOrEqual(start)
          expect(result.frame).toBeLessThanOrEqual(end)
        },
      ),
    )
  })

  it('never stops when frame is within range', () => {
    fc.assert(
      fc.property(positiveInt, fc.boolean(), (totalFrames, autoLoop) => {
        const [start, end] = resolvePlaybackRange(totalFrames, null)
        const mid = Math.floor((start + end) / 2)
        const result = resolvePlaybackTick(mid, totalFrames, null, autoLoop)
        expect(result.shouldStop).toBe(false)
      }),
    )
  })
})

describe('computeEffectiveFps', () => {
  it('returns positive fps for valid inputs', () => {
    fc.assert(
      fc.property(
        positiveInt,
        fc.double({ min: 0.01, max: 10_000, noNaN: true }),
        fc.double({ min: 0.01, max: 1000, noNaN: true }),
        (totalFrames, videoDuration, datasetFps) => {
          const fps = computeEffectiveFps(totalFrames, videoDuration, datasetFps)
          expect(fps).toBeGreaterThan(0)
        },
      ),
    )
  })

  it('equals totalFrames / videoDuration when both are positive', () => {
    fc.assert(
      fc.property(
        positiveInt,
        fc.double({ min: 0.01, max: 10_000, noNaN: true }),
        fc.double({ min: 0.01, max: 1000, noNaN: true }),
        (totalFrames, videoDuration, datasetFps) => {
          const fps = computeEffectiveFps(totalFrames, videoDuration, datasetFps)
          expect(fps).toBeCloseTo(totalFrames / videoDuration, 5)
        },
      ),
    )
  })
})

describe('computePlaybackTarget', () => {
  it('targetTime is non-negative', () => {
    fc.assert(
      fc.property(
        nonNegativeInt,
        positiveInt,
        fc.double({ min: 1, max: 1000, noNaN: true }),
        (currentFrame, totalFrames, fps) => {
          const result = computePlaybackTarget(currentFrame, totalFrames, null, fps)
          expect(result.targetTime).toBeGreaterThanOrEqual(0)
        },
      ),
    )
  })

  it('shouldRestart when at or past the range end', () => {
    fc.assert(
      fc.property(
        positiveInt,
        fc.double({ min: 1, max: 1000, noNaN: true }),
        (totalFrames, fps) => {
          const result = computePlaybackTarget(totalFrames - 1, totalFrames, null, fps)
          expect(result.shouldRestart).toBe(true)
        },
      ),
    )
  })
})

describe('needsSeekBeforePlay', () => {
  it('returns false when times are equal', () => {
    fc.assert(
      fc.property(
        fc.double({ min: 0, max: 10_000, noNaN: true }),
        fc.double({ min: 1, max: 1000, noNaN: true }),
        (time, fps) => {
          expect(needsSeekBeforePlay(time, time, fps)).toBe(false)
        },
      ),
    )
  })

  it('is symmetric in time difference direction', () => {
    fc.assert(
      fc.property(
        fc.double({ min: 0, max: 10_000, noNaN: true }),
        fc.double({ min: 0, max: 10_000, noNaN: true }),
        fc.double({ min: 1, max: 1000, noNaN: true }),
        (a, b, fps) => {
          expect(needsSeekBeforePlay(a, b, fps)).toBe(needsSeekBeforePlay(b, a, fps))
        },
      ),
    )
  })
})
