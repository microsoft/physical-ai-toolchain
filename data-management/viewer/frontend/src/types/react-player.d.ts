declare module 'react-player' {
  import { Component } from 'react'

  interface ReactPlayerProps {
    url?: string | string[] | object | object[]
    playing?: boolean
    loop?: boolean
    controls?: boolean
    light?: boolean | string
    volume?: number
    muted?: boolean
    playbackRate?: number
    width?: string | number
    height?: string | number
    style?: React.CSSProperties
    progressInterval?: number
    playsinline?: boolean
    pip?: boolean
    stopOnUnmount?: boolean
    fallback?: React.ReactNode
    wrapper?: React.ComponentType<{ children: React.ReactNode }>
    config?: object
    onReady?: (player: ReactPlayer) => void
    onStart?: () => void
    onPlay?: () => void
    onPause?: () => void
    onBuffer?: () => void
    onBufferEnd?: () => void
    onEnded?: () => void
    onClickPreview?: (event: React.MouseEvent) => void
    onEnablePIP?: () => void
    onDisablePIP?: () => void
    onError?: (error: Error) => void
    onDuration?: (duration: number) => void
    onSeek?: (seconds: number) => void
    onProgress?: (state: {
      played: number
      playedSeconds: number
      loaded: number
      loadedSeconds: number
    }) => void
  }

  export default class ReactPlayer extends Component<ReactPlayerProps> {
    seekTo(amount: number, type?: 'seconds' | 'fraction'): void
    getCurrentTime(): number
    getDuration(): number
    getInternalPlayer(key?: string): object
    showPreview(): void
  }
}
