/**
 * Playback synchronization utilities.
 *
 * Pure functions for frame↔time conversion and playback seek logic,
 * extracted from AnnotationWorkspace for testability.
 */

export type PlaybackRange = [number, number]

export function resolvePlaybackRange(
  totalFrames: number,
  range: PlaybackRange | null,
): PlaybackRange {
  const episodeEnd = Math.max(totalFrames - 1, 0)

  if (!range) {
    return [0, episodeEnd]
  }

  const [rawStart, rawEnd] = range[0] <= range[1] ? range : [range[1], range[0]]
  const start = Math.max(0, Math.min(rawStart, episodeEnd))
  const end = Math.max(start, Math.min(rawEnd, episodeEnd))

  return [start, end]
}

export function clampFrameToPlaybackRange(
  frame: number,
  totalFrames: number,
  range: PlaybackRange | null,
): number {
  const [start, end] = resolvePlaybackRange(totalFrames, range)

  return Math.max(start, Math.min(frame, end))
}

export function resolvePlaybackTick(
  frame: number,
  totalFrames: number,
  range: PlaybackRange | null,
  autoLoop: boolean,
): { frame: number; shouldStop: boolean } {
  const [start, end] = resolvePlaybackRange(totalFrames, range)

  if (frame <= end) {
    return {
      frame: Math.max(start, frame),
      shouldStop: false,
    }
  }

  if (autoLoop) {
    return { frame: start, shouldStop: false }
  }

  return { frame: end, shouldStop: true }
}

export function shouldRestartPlaybackAfterLoop(
  reportedFrame: number,
  resolvedFrame: number,
  range: PlaybackRange | null,
  autoLoop: boolean,
): boolean {
  if (!range || !autoLoop) {
    return false
  }

  const [start, end] = range[0] <= range[1] ? range : [range[1], range[0]]

  return reportedFrame > end && resolvedFrame === start
}

export function shouldLoopActivePlaybackRange(
  _range: PlaybackRange | null,
  autoLoop: boolean,
): boolean {
  return autoLoop
}

export function shouldRecoverPlaybackAfterDesync(
  isPlaying: boolean,
  videoPaused: boolean,
  elapsedSinceLastRecoveryMs: number,
  recoveryCooldownMs: number,
): boolean {
  return (
    isPlaying &&
    videoPaused &&
    (elapsedSinceLastRecoveryMs <= 0 || elapsedSinceLastRecoveryMs >= recoveryCooldownMs)
  )
}

export function shouldRecoverStalledPlayback(
  isPlaying: boolean,
  videoPaused: boolean,
  videoCurrentTime: number,
  lastAdvancingTime: number,
  elapsedSinceLastAdvanceMs: number,
  stallThresholdMs: number,
): boolean {
  if (!isPlaying || videoPaused) return false
  if (videoCurrentTime !== lastAdvancingTime) return false
  return elapsedSinceLastAdvanceMs >= stallThresholdMs
}

/**
 * Derive effective fps from the video element's actual duration.
 *
 * When the video duration is available, uses `totalFrames / videoDuration`
 * to handle mismatches between dataset metadata fps and video encoding fps.
 * Falls back to the dataset metadata fps when duration is unavailable.
 */
export function computeEffectiveFps(
  totalFrames: number,
  videoDuration: number,
  datasetFps: number,
): number {
  if (videoDuration <= 0 || totalFrames <= 0) return datasetFps
  const computedFps = totalFrames / videoDuration
  // When the video contains multiple episodes (concatenated), the computed
  // fps will be far too low. Use datasetFps whenever the video is clearly
  // longer than this episode.
  if (computedFps < datasetFps * 0.5) return datasetFps
  return computedFps
}

/**
 * Determine the seek target and action when playback is toggled on.
 *
 * Returns the target time in seconds and whether the video should restart
 * from the beginning (when at the last frame).
 */
export function computePlaybackTarget(
  currentFrame: number,
  totalFrames: number,
  originalFrameIndex: number | null,
  fps: number,
  playbackRangeStart = 0,
  playbackRangeEnd = totalFrames - 1,
): { targetTime: number; shouldRestart: boolean } {
  if (currentFrame >= playbackRangeEnd) {
    return { targetTime: playbackRangeStart / fps, shouldRestart: true }
  }

  const frameForTime = originalFrameIndex ?? Math.max(playbackRangeStart, currentFrame)
  return { targetTime: frameForTime / fps, shouldRestart: false }
}

/**
 * Determine if the video element needs a seek before playback.
 *
 * Returns true when the video's current position differs from the
 * target by more than half a frame duration.
 */
export function needsSeekBeforePlay(
  videoCurrentTime: number,
  targetTime: number,
  fps: number,
): boolean {
  return Math.abs(videoCurrentTime - targetTime) > 0.5 / fps
}

/** Action the sync effect should take on the video element. */
export type SyncAction =
  | { kind: 'restart'; playbackRate: number }
  | { kind: 'seek-and-play'; seekTo: number; playbackRate: number }
  | { kind: 'play'; playbackRate: number }
  | { kind: 'pause' }

/**
 * Determine what the play/pause sync effect should do.
 *
 * Encapsulates the full decision tree so it can be tested in isolation
 * without a video element or React effects.
 */
export function computeSyncAction(
  isPlaying: boolean,
  playbackSpeed: number,
  currentFrame: number,
  totalFrames: number,
  originalFrameIndex: number | null,
  fps: number,
  videoCurrentTime: number,
  playbackRangeStart = 0,
  playbackRangeEnd = totalFrames - 1,
): SyncAction {
  if (!isPlaying) return { kind: 'pause' }

  const { targetTime, shouldRestart } = computePlaybackTarget(
    currentFrame,
    totalFrames,
    originalFrameIndex,
    fps,
    playbackRangeStart,
    playbackRangeEnd,
  )

  if (shouldRestart) {
    return { kind: 'restart', playbackRate: playbackSpeed }
  }

  if (needsSeekBeforePlay(videoCurrentTime, targetTime, fps)) {
    return { kind: 'seek-and-play', seekTo: targetTime, playbackRate: playbackSpeed }
  }

  return { kind: 'play', playbackRate: playbackSpeed }
}
