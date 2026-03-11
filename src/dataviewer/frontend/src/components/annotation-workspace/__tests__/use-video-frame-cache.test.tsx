import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { clearPersistentFrameCache, MAX_PERSISTENT_ENTRIES, persistentCacheSize, useVideoFrameCache } from '@/components/annotation-workspace/useVideoFrameCache'

function createMockVideo(frameCount: number, fps: number) {
  const duration = frameCount / fps
  const video = document.createElement('video')
  Object.defineProperty(video, 'duration', { configurable: true, value: duration })
  Object.defineProperty(video, 'videoWidth', { configurable: true, value: 640 })
  Object.defineProperty(video, 'videoHeight', { configurable: true, value: 480 })
  Object.defineProperty(video, 'readyState', { configurable: true, writable: true, value: 0 })

  let currentTime = 0
  Object.defineProperty(video, 'currentTime', {
    configurable: true,
    get: () => currentTime,
    set: (val: number) => {
      currentTime = val
      Object.defineProperty(video, 'readyState', { configurable: true, writable: true, value: 4 })
      queueMicrotask(() => video.dispatchEvent(new Event('seeked')))
    },
  })

  return video
}

describe('useVideoFrameCache', () => {
  let createElementSpy: ReturnType<typeof vi.spyOn>
  let createImageBitmapSpy: ReturnType<typeof vi.fn>
  const originalCreateElement = document.createElement.bind(document)

  beforeEach(() => {
    vi.useFakeTimers()
    globalThis.createImageBitmap = vi.fn()
    createImageBitmapSpy = vi.mocked(globalThis.createImageBitmap).mockImplementation(
      () => Promise.resolve({ close: vi.fn(), width: 640, height: 480 } as unknown as ImageBitmap),
    )
  })

  afterEach(() => {
    vi.useRealTimers()
    createElementSpy?.mockRestore()
    clearPersistentFrameCache()
    delete (globalThis as Record<string, unknown>).createImageBitmap
  })

  it('returns empty cache when videoSrc is null', () => {
    const { result } = renderHook(() => useVideoFrameCache({
      videoSrc: null,
      totalFrames: 100,
      fps: 30,
      onRecordEvent: vi.fn(),
    }))

    expect(result.current.isReady).toBe(false)
    expect(result.current.isDecoding).toBe(false)
    expect(result.current.progress).toBe(0)
    expect(result.current.frames.size).toBe(0)
  })

  it('starts decoding when videoSrc is provided', async () => {
    const mockVideo = createMockVideo(10, 30)
    createElementSpy = vi.spyOn(document, 'createElement').mockImplementation((tag: string) => {
      if (tag === 'video') return mockVideo
      return originalCreateElement(tag)
    })

    const { result } = renderHook(() => useVideoFrameCache({
      videoSrc: '/videos/test.mp4',
      totalFrames: 10,
      fps: 30,
      onRecordEvent: vi.fn(),
    }))

    // Trigger loadeddata
    act(() => {
      mockVideo.dispatchEvent(new Event('loadeddata'))
    })

    expect(result.current.isDecoding).toBe(true)

    // Process all seeks (each frame triggers seeked → createImageBitmap)
    for (let i = 0; i < 10; i++) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1)
      })
    }

    expect(result.current.isReady).toBe(true)
    expect(result.current.frames.size).toBe(10)
    expect(result.current.progress).toBe(1)
  })

  it('aborts decode and closes bitmaps when videoSrc changes mid-decode', async () => {
    const closeMocks: Array<ReturnType<typeof vi.fn>> = []
    // Block createImageBitmap so each frame decode requires explicit resolution
    let resolveBitmap: ((bitmap: ImageBitmap) => void) | null = null
    createImageBitmapSpy.mockImplementation(
      () => new Promise<ImageBitmap>((resolve) => {
        resolveBitmap = (b: ImageBitmap) => resolve(b)
      }),
    )

    const mockVideo = createMockVideo(100, 30)
    createElementSpy = vi.spyOn(document, 'createElement').mockImplementation((tag: string) => {
      if (tag === 'video') return mockVideo
      return originalCreateElement(tag)
    })

    const { result, rerender } = renderHook(
      (props) => useVideoFrameCache(props),
      {
        initialProps: {
          videoSrc: '/videos/ep1.mp4' as string | null,
          totalFrames: 100,
          fps: 30,
          onRecordEvent: vi.fn(),
        },
      },
    )

    // Start decode
    act(() => { mockVideo.dispatchEvent(new Event('loadeddata')) })

    // Allow seeked microtask to fire for frame 0
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })

    // Resolve one bitmap
    const closeFn = vi.fn()
    closeMocks.push(closeFn)
    await act(async () => {
      resolveBitmap!({ close: closeFn, width: 640, height: 480 } as unknown as ImageBitmap)
      await vi.advanceTimersByTimeAsync(0)
    })

    expect(result.current.progress).toBeGreaterThan(0)
    expect(closeFn).not.toHaveBeenCalled()

    // Switch episode — cleanup aborts and closes in-progress bitmaps
    act(() => {
      rerender({
        videoSrc: '/videos/ep2.mp4',
        totalFrames: 3,
        fps: 30,
        onRecordEvent: vi.fn(),
      })
    })

    // Old bitmap should be closed by abort cleanup
    expect(closeFn).toHaveBeenCalled()

    // New decode starts fresh
    expect(result.current.frames.size).toBe(0)
    expect(result.current.isReady).toBe(false)
  })

  it('returns cached frames instantly on episode revisit', async () => {
    const mockVideo = createMockVideo(5, 30)
    createElementSpy = vi.spyOn(document, 'createElement').mockImplementation((tag: string) => {
      if (tag === 'video') return mockVideo
      return originalCreateElement(tag)
    })
    const onRecordEvent = vi.fn()

    const { result, rerender } = renderHook(
      (props) => useVideoFrameCache(props),
      {
        initialProps: {
          videoSrc: '/videos/ep1.mp4' as string | null,
          totalFrames: 5,
          fps: 30,
          onRecordEvent,
        },
      },
    )

    // Complete full decode
    act(() => { mockVideo.dispatchEvent(new Event('loadeddata')) })
    for (let i = 0; i < 5; i++) {
      await act(async () => { await vi.advanceTimersByTimeAsync(1) })
    }

    expect(result.current.isReady).toBe(true)
    expect(result.current.frames.size).toBe(5)

    // Switch away
    rerender({ videoSrc: null, totalFrames: 0, fps: 30, onRecordEvent })

    expect(result.current.isReady).toBe(false)

    // Switch back — should get cached frames instantly
    rerender({ videoSrc: '/videos/ep1.mp4', totalFrames: 5, fps: 30, onRecordEvent })

    expect(result.current.isReady).toBe(true)
    expect(result.current.frames.size).toBe(5)
    expect(onRecordEvent).toHaveBeenCalledWith('playback', 'frame-cache-hit', expect.objectContaining({ videoSrc: '/videos/ep1.mp4' }))
  })

  it('evicts oldest cache entry when max persistent entries is exceeded', async () => {
    const onRecordEvent = vi.fn()
    const frameCount = 3

    const { rerender } = renderHook(
      (props) => useVideoFrameCache(props),
      {
        initialProps: {
          videoSrc: '/videos/ep-0.mp4' as string | null,
          totalFrames: frameCount,
          fps: 30,
          onRecordEvent,
        },
      },
    )

    // Fill the persistent cache to MAX_PERSISTENT_ENTRIES + 1
    for (let ep = 0; ep <= MAX_PERSISTENT_ENTRIES; ep++) {
      const mockVideo = createMockVideo(frameCount, 30)
      createElementSpy = vi.spyOn(document, 'createElement').mockImplementation((tag: string) => {
        if (tag === 'video') return mockVideo
        return originalCreateElement(tag)
      })

      rerender({ videoSrc: `/videos/ep-${ep}.mp4`, totalFrames: frameCount, fps: 30, onRecordEvent })

      act(() => { mockVideo.dispatchEvent(new Event('loadeddata')) })
      for (let i = 0; i < frameCount; i++) {
        await act(async () => { await vi.advanceTimersByTimeAsync(1) })
      }

      createElementSpy.mockRestore()
    }

    // Should cap at MAX_PERSISTENT_ENTRIES, having evicted the oldest
    expect(persistentCacheSize()).toBe(MAX_PERSISTENT_ENTRIES)

    // The first entry should have been evicted; revisiting it should not be a cache hit
    const hitEvents = onRecordEvent.mock.calls.filter(
      (args) => args[1] === 'frame-cache-hit',
    )
    const hitSrcs = hitEvents.map((args) => (args[2] as Record<string, unknown> | undefined)?.videoSrc)
    expect(hitSrcs).not.toContain('/videos/ep-0.mp4')
  })
})
