import { useCallback, useEffect, useRef } from 'react'

import { cn } from '@/lib/utils'

interface CameraView {
    name: string
    videoUrl: string
}

interface MultiCameraGridProps {
    cameras: CameraView[]
    primaryCamera: string
    primaryVideoRef: React.RefObject<HTMLVideoElement>
    primaryVideoSrc: string | null
    onVideoEnded: () => void
    onLoadedMetadata: (event: React.SyntheticEvent<HTMLVideoElement>) => void
    displayFilter?: string
    selectedCamera: string | null
    onSelectCamera: (camera: string) => void
}

function formatCameraName(name: string): string {
    return name
        .replace(/^observation\.images\./, '')
        .replace(/_/g, ' ')
        .replace(/\b\w/g, (c) => c.toUpperCase())
}

export function MultiCameraGrid({
    cameras,
    primaryCamera,
    primaryVideoRef,
    primaryVideoSrc,
    onVideoEnded,
    onLoadedMetadata,
    displayFilter,
    selectedCamera,
    onSelectCamera,
}: MultiCameraGridProps) {
    const secondaryRefs = useRef<Map<string, HTMLVideoElement>>(new Map())

    const activeCameraName = selectedCamera ?? primaryCamera
    const activeVideoUrl =
        cameras.find((c) => c.name === activeCameraName)?.videoUrl ?? primaryVideoSrc

    // Sync secondary videos to primary video on timeupdate
    useEffect(() => {
        const primary = primaryVideoRef.current
        if (!primary || cameras.length <= 1) return

        const syncSecondaries = () => {
            for (const [name, el] of secondaryRefs.current) {
                if (name === activeCameraName) continue
                if (Math.abs(el.currentTime - primary.currentTime) > 0.1) {
                    el.currentTime = primary.currentTime
                }
            }
        }

        primary.addEventListener('timeupdate', syncSecondaries)
        primary.addEventListener('seeked', syncSecondaries)

        return () => {
            primary.removeEventListener('timeupdate', syncSecondaries)
            primary.removeEventListener('seeked', syncSecondaries)
        }
    }, [activeCameraName, cameras.length, primaryVideoRef])

    // Sync play/pause state
    useEffect(() => {
        const primary = primaryVideoRef.current
        if (!primary || cameras.length <= 1) return

        const syncPlay = () => {
            for (const [, el] of secondaryRefs.current) {
                el.play().catch(() => { })
            }
        }

        const syncPause = () => {
            for (const [, el] of secondaryRefs.current) {
                el.pause()
            }
        }

        primary.addEventListener('play', syncPlay)
        primary.addEventListener('pause', syncPause)

        return () => {
            primary.removeEventListener('play', syncPlay)
            primary.removeEventListener('pause', syncPause)
        }
    }, [cameras.length, primaryVideoRef])

    // Register secondary video refs via callback refs
    const setSecondaryRef = useCallback(
        (name: string) => (el: HTMLVideoElement | null) => {
            if (el) {
                secondaryRefs.current.set(name, el)
                // Sync to primary time once loaded so a frame is visible
                const primary = primaryVideoRef.current
                if (primary && primary.readyState >= 1) {
                    el.currentTime = primary.currentTime
                }
                el.addEventListener(
                    'loadeddata',
                    () => {
                        const p = primaryVideoRef.current
                        if (p) el.currentTime = p.currentTime
                    },
                    { once: true },
                )
            } else {
                secondaryRefs.current.delete(name)
            }
        },
        [primaryVideoRef],
    )

    const secondaryCameras = cameras.filter((c) => c.name !== activeCameraName)

    // Grid layout: active camera large, secondaries as thumbnails below
    return (
        <div className="flex flex-col gap-2">
            <div className="relative flex aspect-video items-center justify-center overflow-hidden rounded-lg bg-black">
                <video
                    ref={primaryVideoRef}
                    src={activeVideoUrl ?? undefined}
                    onEnded={onVideoEnded}
                    onLoadedMetadata={onLoadedMetadata}
                    muted
                    playsInline
                    preload="auto"
                    className="max-h-full max-w-full object-contain"
                    style={displayFilter ? { filter: displayFilter } : undefined}
                />
                <div className="absolute bottom-2 left-2 rounded bg-black/60 px-2 py-1 text-xs font-medium text-white">
                    {formatCameraName(activeCameraName)}
                </div>
            </div>

            {secondaryCameras.length > 0 && (
                <div
                    className={cn(
                        'grid gap-2',
                        secondaryCameras.length === 1 && 'grid-cols-1',
                        secondaryCameras.length === 2 && 'grid-cols-2',
                        secondaryCameras.length >= 3 && 'grid-cols-3',
                    )}
                >
                    {secondaryCameras.map((camera) => (
                        <div key={camera.name} className="relative">
                            <button
                                type="button"
                                onClick={() => onSelectCamera(camera.name)}
                                className={cn(
                                    'relative flex w-full aspect-video items-center justify-center overflow-hidden rounded-lg bg-black transition-all',
                                    'opacity-80 hover:opacity-100 hover:ring-1 hover:ring-primary/50',
                                )}
                            >
                                <video
                                    ref={setSecondaryRef(camera.name)}
                                    src={camera.videoUrl}
                                    muted
                                    playsInline
                                    preload="auto"
                                    className="max-h-full max-w-full object-contain"
                                    style={displayFilter ? { filter: displayFilter } : undefined}
                                />
                                <div className="absolute bottom-1 left-1 rounded bg-black/60 px-1.5 py-0.5 text-[10px] font-medium text-white">
                                    {formatCameraName(camera.name)}
                                </div>
                            </button>
                        </div>
                    ))}
                </div>
            )}
        </div>
    )
}
