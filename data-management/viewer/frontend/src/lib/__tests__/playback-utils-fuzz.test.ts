/**
 * Fuzz harness for playback synchronization utilities.
 *
 * Complements playback-utils.property.test.ts with adversarial inputs:
 * NaN, Infinity, negative values, extreme magnitudes, and boundary
 * conditions that exercise crash-resistance under arbitrary data.
 */
import fc from 'fast-check'
import { describe, expect, it } from 'vitest'

import {
  clampFrameToPlaybackRange,
  computeEffectiveFps,
  computePlaybackTarget,
  computeSyncAction,
  needsSeekBeforePlay,
  type PlaybackRange,
  resolvePlaybackRange,
  resolvePlaybackTick,
  shouldLoopActivePlaybackRange,
  shouldRecoverPlaybackAfterDesync,
  shouldRecoverStalledPlayback,
  shouldRestartPlaybackAfterLoop,
} from '../playback-utils'

// --- Adversarial arbitraries ---

/** Arbitrary that includes NaN, ±Infinity, ±0, and extreme doubles. */
const adversarialNumber = fc.oneof(
  fc.constant(NaN),
  fc.constant(Infinity),
  fc.constant(-Infinity),
  fc.constant(0),
  fc.constant(-0),
  fc.constant(Number.MAX_SAFE_INTEGER),
  fc.constant(Number.MIN_SAFE_INTEGER),
  fc.constant(Number.MAX_VALUE),
  fc.constant(Number.MIN_VALUE),
  fc.constant(-Number.MAX_VALUE),
  fc.double(),
  fc.integer(),
)

/** Arbitrary producing any integer, including negatives and extreme values. */
const anyInt = fc.oneof(
  fc.integer(),
  fc.constant(0),
  fc.constant(-1),
  fc.constant(Number.MAX_SAFE_INTEGER),
  fc.constant(Number.MIN_SAFE_INTEGER),
)

/** Arbitrary for playback ranges with adversarial endpoints. */
const adversarialRange = fc.oneof(
  fc.tuple(anyInt, anyInt).map(([a, b]): PlaybackRange => [a, b]),
  fc.constant(null),
)

/** Fps values including zero, negative, NaN, Infinity. */
const adversarialFps = fc.oneof(
  fc.constant(0),
  fc.constant(-1),
  fc.constant(NaN),
  fc.constant(Infinity),
  fc.constant(-Infinity),
  fc.constant(0.001),
  fc.constant(1e15),
  fc.double({ min: 0.001, max: 1000, noNaN: true }),
)

// --- Tests ---

describe('resolvePlaybackRange fuzz', () => {
  it('never throws on adversarial totalFrames and range', () => {
    fc.assert(
      fc.property(anyInt, adversarialRange, (totalFrames, range) => {
        expect(() => resolvePlaybackRange(totalFrames, range)).not.toThrow()
      }),
    )
  })

  it('always returns a two-element array', () => {
    fc.assert(
      fc.property(anyInt, adversarialRange, (totalFrames, range) => {
        const result = resolvePlaybackRange(totalFrames, range)
        expect(result).toHaveLength(2)
        expect(typeof result[0]).toBe('number')
        expect(typeof result[1]).toBe('number')
      }),
    )
  })
})

describe('clampFrameToPlaybackRange fuzz', () => {
  it('never throws on adversarial inputs', () => {
    fc.assert(
      fc.property(anyInt, anyInt, adversarialRange, (frame, totalFrames, range) => {
        expect(() => clampFrameToPlaybackRange(frame, totalFrames, range)).not.toThrow()
      }),
    )
  })

  it('always returns a number', () => {
    fc.assert(
      fc.property(anyInt, anyInt, adversarialRange, (frame, totalFrames, range) => {
        expect(typeof clampFrameToPlaybackRange(frame, totalFrames, range)).toBe('number')
      }),
    )
  })
})

describe('resolvePlaybackTick fuzz', () => {
  it('never throws on adversarial inputs', () => {
    fc.assert(
      fc.property(
        anyInt,
        anyInt,
        adversarialRange,
        fc.boolean(),
        (frame, totalFrames, range, autoLoop) => {
          expect(() => resolvePlaybackTick(frame, totalFrames, range, autoLoop)).not.toThrow()
        },
      ),
    )
  })

  it('always returns an object with frame and shouldStop', () => {
    fc.assert(
      fc.property(
        anyInt,
        anyInt,
        adversarialRange,
        fc.boolean(),
        (frame, totalFrames, range, autoLoop) => {
          const result = resolvePlaybackTick(frame, totalFrames, range, autoLoop)
          expect(typeof result.frame).toBe('number')
          expect(typeof result.shouldStop).toBe('boolean')
        },
      ),
    )
  })
})

describe('shouldRestartPlaybackAfterLoop fuzz', () => {
  it('never throws on adversarial inputs', () => {
    fc.assert(
      fc.property(
        adversarialNumber,
        adversarialNumber,
        adversarialRange,
        fc.boolean(),
        (reportedFrame, resolvedFrame, range, autoLoop) => {
          expect(() =>
            shouldRestartPlaybackAfterLoop(reportedFrame, resolvedFrame, range, autoLoop),
          ).not.toThrow()
        },
      ),
    )
  })

  it('always returns a boolean', () => {
    fc.assert(
      fc.property(
        adversarialNumber,
        adversarialNumber,
        adversarialRange,
        fc.boolean(),
        (reportedFrame, resolvedFrame, range, autoLoop) => {
          expect(
            typeof shouldRestartPlaybackAfterLoop(reportedFrame, resolvedFrame, range, autoLoop),
          ).toBe('boolean')
        },
      ),
    )
  })

  it('returns false when autoLoop is false regardless of inputs', () => {
    fc.assert(
      fc.property(
        adversarialNumber,
        adversarialNumber,
        adversarialRange,
        (reportedFrame, resolvedFrame, range) => {
          expect(shouldRestartPlaybackAfterLoop(reportedFrame, resolvedFrame, range, false)).toBe(
            false,
          )
        },
      ),
    )
  })

  it('returns false when range is null regardless of inputs', () => {
    fc.assert(
      fc.property(
        adversarialNumber,
        adversarialNumber,
        fc.boolean(),
        (reportedFrame, resolvedFrame, autoLoop) => {
          expect(shouldRestartPlaybackAfterLoop(reportedFrame, resolvedFrame, null, autoLoop)).toBe(
            false,
          )
        },
      ),
    )
  })
})

describe('shouldLoopActivePlaybackRange fuzz', () => {
  it('equals autoLoop for any range', () => {
    fc.assert(
      fc.property(adversarialRange, fc.boolean(), (range, autoLoop) => {
        expect(shouldLoopActivePlaybackRange(range, autoLoop)).toBe(autoLoop)
      }),
    )
  })
})

describe('shouldRecoverPlaybackAfterDesync fuzz', () => {
  it('never throws on adversarial timing values', () => {
    fc.assert(
      fc.property(
        fc.boolean(),
        fc.boolean(),
        adversarialNumber,
        adversarialNumber,
        (isPlaying, videoPaused, elapsed, cooldown) => {
          expect(() =>
            shouldRecoverPlaybackAfterDesync(isPlaying, videoPaused, elapsed, cooldown),
          ).not.toThrow()
        },
      ),
    )
  })

  it('always returns a boolean', () => {
    fc.assert(
      fc.property(
        fc.boolean(),
        fc.boolean(),
        adversarialNumber,
        adversarialNumber,
        (isPlaying, videoPaused, elapsed, cooldown) => {
          expect(
            typeof shouldRecoverPlaybackAfterDesync(isPlaying, videoPaused, elapsed, cooldown),
          ).toBe('boolean')
        },
      ),
    )
  })

  it('returns false when not playing', () => {
    fc.assert(
      fc.property(
        fc.boolean(),
        adversarialNumber,
        adversarialNumber,
        (videoPaused, elapsed, cooldown) => {
          expect(shouldRecoverPlaybackAfterDesync(false, videoPaused, elapsed, cooldown)).toBe(
            false,
          )
        },
      ),
    )
  })

  it('returns false when video is not paused', () => {
    fc.assert(
      fc.property(adversarialNumber, adversarialNumber, (elapsed, cooldown) => {
        expect(shouldRecoverPlaybackAfterDesync(true, false, elapsed, cooldown)).toBe(false)
      }),
    )
  })
})

describe('shouldRecoverStalledPlayback fuzz', () => {
  it('never throws on adversarial inputs', () => {
    fc.assert(
      fc.property(
        fc.boolean(),
        fc.boolean(),
        adversarialNumber,
        adversarialNumber,
        adversarialNumber,
        adversarialNumber,
        (isPlaying, videoPaused, videoCurrentTime, lastAdvTime, elapsedMs, stallMs) => {
          expect(() =>
            shouldRecoverStalledPlayback(
              isPlaying,
              videoPaused,
              videoCurrentTime,
              lastAdvTime,
              elapsedMs,
              stallMs,
            ),
          ).not.toThrow()
        },
      ),
    )
  })

  it('returns false when not playing', () => {
    fc.assert(
      fc.property(
        fc.boolean(),
        adversarialNumber,
        adversarialNumber,
        adversarialNumber,
        adversarialNumber,
        (videoPaused, videoCurrentTime, lastAdvTime, elapsedMs, stallMs) => {
          expect(
            shouldRecoverStalledPlayback(
              false,
              videoPaused,
              videoCurrentTime,
              lastAdvTime,
              elapsedMs,
              stallMs,
            ),
          ).toBe(false)
        },
      ),
    )
  })

  it('returns false when video is paused', () => {
    fc.assert(
      fc.property(
        adversarialNumber,
        adversarialNumber,
        adversarialNumber,
        adversarialNumber,
        (videoCurrentTime, lastAdvTime, elapsedMs, stallMs) => {
          expect(
            shouldRecoverStalledPlayback(
              true,
              true,
              videoCurrentTime,
              lastAdvTime,
              elapsedMs,
              stallMs,
            ),
          ).toBe(false)
        },
      ),
    )
  })

  it('returns false when current time differs from last advancing time', () => {
    fc.assert(
      fc.property(
        fc.double({ min: 0, max: 1000, noNaN: true }),
        fc.double({ min: 0, max: 1000, noNaN: true }),
        adversarialNumber,
        adversarialNumber,
        (a, b, elapsedMs, stallMs) => {
          fc.pre(a !== b)
          expect(shouldRecoverStalledPlayback(true, false, a, b, elapsedMs, stallMs)).toBe(false)
        },
      ),
    )
  })
})

describe('computeEffectiveFps fuzz', () => {
  it('never throws on adversarial inputs', () => {
    fc.assert(
      fc.property(
        adversarialNumber,
        adversarialNumber,
        adversarialNumber,
        (totalFrames, duration, fps) => {
          expect(() => computeEffectiveFps(totalFrames, duration, fps)).not.toThrow()
        },
      ),
    )
  })

  it('always returns a number', () => {
    fc.assert(
      fc.property(
        adversarialNumber,
        adversarialNumber,
        adversarialNumber,
        (totalFrames, duration, fps) => {
          expect(typeof computeEffectiveFps(totalFrames, duration, fps)).toBe('number')
        },
      ),
    )
  })
})

describe('computeSyncAction fuzz', () => {
  it('never throws on adversarial inputs', () => {
    fc.assert(
      fc.property(
        fc.boolean(),
        adversarialNumber,
        anyInt,
        anyInt,
        fc.option(anyInt, { nil: null }),
        adversarialFps,
        adversarialNumber,
        (isPlaying, speed, currentFrame, totalFrames, originalFrame, fps, videoTime) => {
          expect(() =>
            computeSyncAction(
              isPlaying,
              speed,
              currentFrame,
              totalFrames,
              originalFrame,
              fps,
              videoTime,
            ),
          ).not.toThrow()
        },
      ),
    )
  })

  it('returns pause when not playing', () => {
    fc.assert(
      fc.property(
        adversarialNumber,
        anyInt,
        anyInt,
        fc.option(anyInt, { nil: null }),
        adversarialFps,
        adversarialNumber,
        (speed, currentFrame, totalFrames, originalFrame, fps, videoTime) => {
          const result = computeSyncAction(
            false,
            speed,
            currentFrame,
            totalFrames,
            originalFrame,
            fps,
            videoTime,
          )
          expect(result.kind).toBe('pause')
        },
      ),
    )
  })

  it('action kind is always a valid variant', () => {
    fc.assert(
      fc.property(
        fc.boolean(),
        adversarialNumber,
        anyInt,
        anyInt,
        fc.option(anyInt, { nil: null }),
        adversarialFps,
        adversarialNumber,
        (isPlaying, speed, currentFrame, totalFrames, originalFrame, fps, videoTime) => {
          const result = computeSyncAction(
            isPlaying,
            speed,
            currentFrame,
            totalFrames,
            originalFrame,
            fps,
            videoTime,
          )
          expect(['restart', 'seek-and-play', 'play', 'pause']).toContain(result.kind)
        },
      ),
    )
  })

  it('playbackRate is set on non-pause actions', () => {
    fc.assert(
      fc.property(
        adversarialNumber,
        anyInt,
        anyInt,
        fc.option(anyInt, { nil: null }),
        adversarialFps,
        adversarialNumber,
        (speed, currentFrame, totalFrames, originalFrame, fps, videoTime) => {
          const result = computeSyncAction(
            true,
            speed,
            currentFrame,
            totalFrames,
            originalFrame,
            fps,
            videoTime,
          )
          if (result.kind !== 'pause') {
            expect(typeof result.playbackRate).toBe('number')
          }
        },
      ),
    )
  })

  it('seek-and-play always includes a seekTo number', () => {
    fc.assert(
      fc.property(
        adversarialNumber,
        anyInt,
        anyInt,
        fc.option(anyInt, { nil: null }),
        adversarialFps,
        adversarialNumber,
        (speed, currentFrame, totalFrames, originalFrame, fps, videoTime) => {
          const result = computeSyncAction(
            true,
            speed,
            currentFrame,
            totalFrames,
            originalFrame,
            fps,
            videoTime,
          )
          if (result.kind === 'seek-and-play') {
            expect(typeof result.seekTo).toBe('number')
          }
        },
      ),
    )
  })
})

describe('computePlaybackTarget fuzz', () => {
  it('never throws on adversarial inputs', () => {
    fc.assert(
      fc.property(
        anyInt,
        anyInt,
        fc.option(anyInt, { nil: null }),
        adversarialFps,
        (currentFrame, totalFrames, originalFrame, fps) => {
          expect(() =>
            computePlaybackTarget(currentFrame, totalFrames, originalFrame, fps),
          ).not.toThrow()
        },
      ),
    )
  })

  it('always returns targetTime number and shouldRestart boolean', () => {
    fc.assert(
      fc.property(
        anyInt,
        anyInt,
        fc.option(anyInt, { nil: null }),
        adversarialFps,
        (currentFrame, totalFrames, originalFrame, fps) => {
          const result = computePlaybackTarget(currentFrame, totalFrames, originalFrame, fps)
          expect(typeof result.targetTime).toBe('number')
          expect(typeof result.shouldRestart).toBe('boolean')
        },
      ),
    )
  })
})

describe('needsSeekBeforePlay fuzz', () => {
  it('never throws on adversarial inputs', () => {
    fc.assert(
      fc.property(adversarialNumber, adversarialNumber, adversarialFps, (a, b, fps) => {
        expect(() => needsSeekBeforePlay(a, b, fps)).not.toThrow()
      }),
    )
  })

  it('always returns a boolean', () => {
    fc.assert(
      fc.property(adversarialNumber, adversarialNumber, adversarialFps, (a, b, fps) => {
        expect(typeof needsSeekBeforePlay(a, b, fps)).toBe('boolean')
      }),
    )
  })
})
