import { useEffect, useRef, useState } from 'react'

interface UseVideoFrameCacheOptions {
  videoSrc: string | null
  totalFrames: number
  fps: number
  onRecordEvent: (channel: string, type: string, data?: Record<string, unknown>) => void
}

interface UseVideoFrameCacheResult {
  frames: Map<number, ImageBitmap>
  isReady: boolean
  isDecoding: boolean
  progress: number
}

const EMPTY_CACHE: UseVideoFrameCacheResult = {
  frames: new Map(),
  isReady: false,
  isDecoding: false,
  progress: 0,
}

// Persistent cache across episode switches — keyed by videoSrc
const persistentCache = new Map<string, Map<number, ImageBitmap>>()
export const MAX_PERSISTENT_ENTRIES = 10

export function clearPersistentFrameCache(): void {
  for (const cache of persistentCache.values()) {
    for (const bitmap of cache.values()) bitmap.close()
  }
  persistentCache.clear()
}

export function persistentCacheSize(): number {
  return persistentCache.size
}

function evictOldestEntry() {
  const oldest = persistentCache.keys().next().value
  if (oldest === undefined) return
  const cache = persistentCache.get(oldest)
  if (cache) {
    for (const bitmap of cache.values()) bitmap.close()
  }
  persistentCache.delete(oldest)
}

function closeCacheFrames(cache: Map<number, ImageBitmap>) {
  for (const bitmap of cache.values()) bitmap.close()
}

export function useVideoFrameCache({
  videoSrc,
  totalFrames,
  fps,
  onRecordEvent,
}: UseVideoFrameCacheOptions): UseVideoFrameCacheResult {
  const [state, setState] = useState<UseVideoFrameCacheResult>(EMPTY_CACHE)
  const onRecordEventRef = useRef(onRecordEvent)
  onRecordEventRef.current = onRecordEvent

  useEffect(() => {
    if (!videoSrc || totalFrames <= 0 || fps <= 0) {
      setState(EMPTY_CACHE)
      return
    }

    // Check persistent cache first
    const existing = persistentCache.get(videoSrc)
    if (existing && existing.size === totalFrames) {
      setState({ frames: existing, isReady: true, isDecoding: false, progress: 1 })
      onRecordEventRef.current('playback', 'frame-cache-hit', { totalFrames, videoSrc })
      return
    }

    const controller = new AbortController()
    let inProgressCache: Map<number, ImageBitmap> | null = null
    setState({ frames: new Map(), isReady: false, isDecoding: false, progress: 0 })

    const video = document.createElement('video')
    video.crossOrigin = 'anonymous'
    video.preload = 'auto'
    video.src = videoSrc

    const decodeFrames = async () => {
      if (controller.signal.aborted) return

      setState(prev => ({ ...prev, isDecoding: true }))
      onRecordEventRef.current('playback', 'frame-cache-start', { totalFrames, videoSrc })

      const cache = new Map<number, ImageBitmap>()
      inProgressCache = cache

      for (let i = 0; i < totalFrames; i++) {
        if (controller.signal.aborted) {
          closeCacheFrames(cache)
          return
        }

        video.currentTime = i / fps

        try {
          await new Promise<void>((resolve, reject) => {
            const onSeeked = () => {
              video.removeEventListener('seeked', onSeeked)
              controller.signal.removeEventListener('abort', onAbort)
              resolve()
            }
            const onAbort = () => {
              video.removeEventListener('seeked', onSeeked)
              reject(new DOMException('Aborted', 'AbortError'))
            }
            video.addEventListener('seeked', onSeeked)
            controller.signal.addEventListener('abort', onAbort)
          })
        } catch {
          closeCacheFrames(cache)
          return
        }

        if (controller.signal.aborted) {
          closeCacheFrames(cache)
          return
        }

        const bitmap = await createImageBitmap(video)
        if (controller.signal.aborted) {
          bitmap.close()
          closeCacheFrames(cache)
          return
        }

        cache.set(i, bitmap)
        setState(prev => ({ ...prev, progress: (i + 1) / totalFrames }))
      }

      if (!controller.signal.aborted) {
        inProgressCache = null
        if (persistentCache.size >= MAX_PERSISTENT_ENTRIES) {
          evictOldestEntry()
        }
        persistentCache.set(videoSrc, cache)
        setState({ frames: cache, isReady: true, isDecoding: false, progress: 1 })
        onRecordEventRef.current('playback', 'frame-cache-complete', { totalFrames, videoSrc })
      }
    }

    video.addEventListener('loadeddata', decodeFrames, { once: true })

    return () => {
      controller.abort()
      if (inProgressCache) {
        closeCacheFrames(inProgressCache)
        inProgressCache = null
      }
      video.removeEventListener('loadeddata', decodeFrames)
      video.pause()
      video.removeAttribute('src')
      video.load()
    }
  }, [videoSrc, totalFrames, fps])

  return state
}
