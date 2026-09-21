import { useEffect, useMemo, useRef, useState } from 'react'

import { combineCssFilters } from '@/lib/css-filters'
import type { DatasetInfo, EpisodeData } from '@/types'
import type { ColorAdjustment, FrameInsertion, ImageTransform } from '@/types/episode-edit'

interface UseAnnotationWorkspaceMediaSourcesOptions {
  currentDataset: DatasetInfo | null
  currentEpisode: EpisodeData | null
  currentFrame: number
  totalFrames: number
  originalFrameIndex: number | null
  displayAdjustment: ColorAdjustment | null
  displayActive: boolean
  globalTransform: ImageTransform | null
  insertedFrames: Map<number, FrameInsertion>
  removedFrames: Set<number>
}

export function useAnnotationWorkspaceMediaSources({
  currentDataset,
  currentEpisode,
  currentFrame,
  totalFrames,
  originalFrameIndex,
  displayAdjustment,
  displayActive,
  globalTransform,
  insertedFrames,
  removedFrames,
}: UseAnnotationWorkspaceMediaSourcesOptions) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [interpolatedImageUrl, setInterpolatedImageUrl] = useState<string | null>(null)

  const displayFilter = useMemo(
    () =>
      combineCssFilters(
        displayAdjustment ?? undefined,
        displayActive,
        globalTransform?.colorAdjustment,
        globalTransform?.colorFilter,
      ),
    [
      displayAdjustment,
      displayActive,
      globalTransform?.colorAdjustment,
      globalTransform?.colorFilter,
    ],
  )

  const cameras = useMemo(() => {
    const fromEpisode = currentEpisode?.cameras ?? []
    if (fromEpisode.length > 0) {
      return fromEpisode
    }
    return Object.keys(currentEpisode?.videoUrls ?? {})
  }, [currentEpisode?.cameras, currentEpisode?.videoUrls])

  // User-selected camera override; null means "follow the default (cameras[0])".
  // Tracking the override (rather than the resolved camera) keeps the resolved
  // cameraName synchronous on first render, avoiding a transient null that would
  // briefly produce an empty videoSrc and disrupt autoplay sequencing.
  const [cameraOverride, setCameraOverride] = useState<string | null>(null)

  const cameraName = useMemo(() => {
    if (cameras.length === 0) {
      return null
    }
    if (cameraOverride && cameras.includes(cameraOverride)) {
      return cameraOverride
    }
    return cameras[0]
  }, [cameras, cameraOverride])

  // Drop a stale override when the camera list no longer contains it.
  useEffect(() => {
    if (cameraOverride && !cameras.includes(cameraOverride)) {
      setCameraOverride(null)
    }
  }, [cameras, cameraOverride])

  const videoUrls = useMemo(() => currentEpisode?.videoUrls ?? {}, [currentEpisode?.videoUrls])
  const videoWindow = useMemo<[number, number] | null>(() => {
    if (!cameraName) {
      return null
    }
    const window = currentEpisode?.videoTimeWindows?.[cameraName]
    if (!window || window.length !== 2) {
      return null
    }
    return [window[0], window[1]]
  }, [cameraName, currentEpisode?.videoTimeWindows])

  // Resolve the streaming URL for the selected camera. videoTimeWindows is a
  // separate concern (concatenated v3 LeRobot clips need a [start, end] hint),
  // and is exposed as videoWindow above. v2 LeRobot stores one video per
  // episode with no window, so requiring a window here would force the player
  // into the per-frame <img> fallback and make playback stutter.
  const videoSrc = useMemo<string | null>(() => {
    if (!cameraName) {
      return null
    }
    return videoUrls[cameraName] ?? null
  }, [cameraName, videoUrls])

  const isInsertedFrame = originalFrameIndex === null

  const adjacentFrames = useMemo(() => {
    if (!isInsertedFrame) {
      return null
    }

    const originalFrameCount =
      currentEpisode?.meta.length ?? currentEpisode?.trajectoryData?.length ?? totalFrames
    const sortedInsertions = Array.from(insertedFrames.keys())
      .filter((afterIdx) => !removedFrames.has(afterIdx) && afterIdx < originalFrameCount - 1)
      .sort((a, b) => a - b)

    for (const afterIdx of sortedInsertions) {
      let insertPos = afterIdx + 1

      for (const removedIdx of removedFrames) {
        if (removedIdx <= afterIdx) {
          insertPos--
        }
      }

      for (const prevIdx of sortedInsertions) {
        if (prevIdx < afterIdx) {
          insertPos++
        }
      }

      if (insertPos === currentFrame) {
        const insertion = insertedFrames.get(afterIdx)
        return {
          beforeFrame: afterIdx,
          afterFrame: afterIdx + 1,
          factor: insertion?.interpolationFactor ?? 0.5,
        }
      }
    }

    return null
  }, [
    currentEpisode?.meta.length,
    currentEpisode?.trajectoryData?.length,
    currentFrame,
    insertedFrames,
    isInsertedFrame,
    removedFrames,
    totalFrames,
  ])

  const frameImageUrl = useMemo(() => {
    if (!currentDataset || !currentEpisode || !cameraName || originalFrameIndex === null) {
      return null
    }

    return `/api/datasets/${currentDataset.id}/episodes/${currentEpisode.meta.index}/frames/${originalFrameIndex}?camera=${encodeURIComponent(cameraName)}`
  }, [cameraName, currentDataset, currentEpisode, originalFrameIndex])

  useEffect(() => {
    if (!isInsertedFrame || !adjacentFrames || !currentDataset || !currentEpisode || !cameraName) {
      setInterpolatedImageUrl(null)
      return
    }

    const canvas = canvasRef.current
    if (!canvas) {
      return
    }

    const ctx = canvas.getContext('2d')
    if (!ctx) {
      return
    }

    const encodedCamera = encodeURIComponent(cameraName)
    const beforeUrl = `/api/datasets/${currentDataset.id}/episodes/${currentEpisode.meta.index}/frames/${adjacentFrames.beforeFrame}?camera=${encodedCamera}`
    const afterUrl = `/api/datasets/${currentDataset.id}/episodes/${currentEpisode.meta.index}/frames/${adjacentFrames.afterFrame}?camera=${encodedCamera}`

    const img1 = new Image()
    const img2 = new Image()
    let loadedCount = 0

    const blend = () => {
      loadedCount++
      if (loadedCount < 2) {
        return
      }

      canvas.width = img1.width
      canvas.height = img1.height

      ctx.globalAlpha = 1 - adjacentFrames.factor
      ctx.drawImage(img1, 0, 0)

      ctx.globalAlpha = adjacentFrames.factor
      ctx.drawImage(img2, 0, 0)

      ctx.globalAlpha = 1
      setInterpolatedImageUrl(canvas.toDataURL('image/jpeg', 0.9))
    }

    img1.onload = blend
    img2.onload = blend
    img1.src = beforeUrl
    img2.src = afterUrl

    return () => {
      img1.onload = null
      img2.onload = null
    }
  }, [adjacentFrames, cameraName, currentDataset, currentEpisode, isInsertedFrame])

  return {
    canvasRef,
    cameras,
    cameraName,
    setCameraName: setCameraOverride,
    displayFilter,
    frameImageUrl,
    interpolatedImageUrl,
    isInsertedFrame,
    videoSrc,
    videoUrls,
    videoWindow,
  }
}
