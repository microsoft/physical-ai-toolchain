import '@testing-library/jest-dom/vitest'

/**
 * Happy DOM does not implement several browser APIs that Radix UI primitives
 * (and a handful of dashboard widgets) rely on. Install minimal feature-detect
 * shims so component tests can render annotation-panel, frame-editor, and
 * other Radix-based UIs without throwing.
 */

type PointerCapableElement = Element & {
  hasPointerCapture?: (pointerId: number) => boolean
  setPointerCapture?: (pointerId: number) => void
  releasePointerCapture?: (pointerId: number) => void
  scrollIntoView?: (arg?: boolean | ScrollIntoViewOptions) => void
}

const elementProto = globalThis.Element?.prototype as PointerCapableElement | undefined

if (elementProto) {
  if (typeof elementProto.scrollIntoView !== 'function') {
    elementProto.scrollIntoView = () => {}
  }
  if (typeof elementProto.hasPointerCapture !== 'function') {
    elementProto.hasPointerCapture = () => false
  }
  if (typeof elementProto.setPointerCapture !== 'function') {
    elementProto.setPointerCapture = () => {}
  }
  if (typeof elementProto.releasePointerCapture !== 'function') {
    elementProto.releasePointerCapture = () => {}
  }
}

if (typeof globalThis.ResizeObserver === 'undefined') {
  class ResizeObserverShim implements ResizeObserver {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  }
  globalThis.ResizeObserver = ResizeObserverShim
}

if (typeof globalThis.IntersectionObserver === 'undefined') {
  class IntersectionObserverShim implements IntersectionObserver {
    readonly root: Element | Document | null = null
    readonly rootMargin: string = ''
    readonly scrollMargin: string = ''
    readonly thresholds: ReadonlyArray<number> = []
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
    takeRecords(): IntersectionObserverEntry[] {
      return []
    }
  }
  globalThis.IntersectionObserver = IntersectionObserverShim
}

if (
  typeof globalThis.window !== 'undefined' &&
  typeof globalThis.window.matchMedia !== 'function'
) {
  globalThis.window.matchMedia = (query: string): MediaQueryList => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })
}
