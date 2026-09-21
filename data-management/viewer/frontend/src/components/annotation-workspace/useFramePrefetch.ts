import { useEffect, useRef } from 'react'

interface UseFramePrefetchOptions {
  datasetId: string | null
  episodeIndex: number | null
  cameraName: string | null
  currentFrame: number
  totalFrames: number
  isPlaying: boolean
  videoSrc: string | null
  lookahead?: number
}

/**
 * Prefetches upcoming frame images during frame-only playback by creating
 * offscreen Image objects. The browser HTTP cache serves these instantly
 * when the visible <img> element later requests the same URLs.
 */
export function useFramePrefetch({
  datasetId,
  episodeIndex,
  cameraName,
  currentFrame,
  totalFrames,
  isPlaying,
  videoSrc,
  lookahead = 5,
}: UseFramePrefetchOptions): void {
  const prefetchedRef = useRef(new Set<string>())
  const imagesRef = useRef<HTMLImageElement[]>([])

  useEffect(() => {
    prefetchedRef.current.clear()
    imagesRef.current = []
  }, [datasetId, episodeIndex, cameraName])

  useEffect(() => {
    if (videoSrc || !isPlaying || !datasetId || episodeIndex == null || !cameraName) {
      return
    }

    const encodedCamera = encodeURIComponent(cameraName)
    const end = Math.min(currentFrame + lookahead, totalFrames - 1)
    const newImages: HTMLImageElement[] = []

    for (let i = currentFrame + 1; i <= end; i++) {
      const url = `/api/datasets/${datasetId}/episodes/${episodeIndex}/frames/${i}?camera=${encodedCamera}`
      if (!prefetchedRef.current.has(url)) {
        prefetchedRef.current.add(url)
        const img = new Image()
        img.src = url
        newImages.push(img)
      }
    }

    imagesRef.current = [...imagesRef.current.slice(-lookahead * 2), ...newImages]
  }, [
    cameraName,
    currentFrame,
    datasetId,
    episodeIndex,
    isPlaying,
    lookahead,
    totalFrames,
    videoSrc,
  ])
}
