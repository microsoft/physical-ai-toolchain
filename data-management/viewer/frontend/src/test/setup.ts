import 'fake-indexeddb/auto'
import '@testing-library/jest-dom/vitest'

import { cleanup } from '@testing-library/react'
import { afterEach, beforeEach } from 'vitest'

import { _resetCsrfToken } from '@/lib/api-client'

// `globals: false` in vitest config prevents @testing-library/react's auto-cleanup
// from registering, so unmount rendered trees between tests manually.
afterEach(() => {
  cleanup()
})

// Drop the cached CSRF token between tests so each test's pre-queued
// `/api/csrf-token` mock response is consumed exactly once.
beforeEach(() => {
  _resetCsrfToken()
})

// Background TanStack Query refetches can fire after a test's afterEach restores
// the original fetch. Relative '/api/...' URLs then resolve against happy-dom's
// default origin (http://localhost:3000) and reject with a TypeError whose
// `cause` is a Node system error with code 'ECONNREFUSED'. These rejections are
// harmless teardown noise; swallow only that exact shape so unrelated bugs
// still surface. Access `process` via globalThis so this file type-checks under
// the Vite app tsconfig (which excludes Node ambients).
type UnhandledRejectionListener = (reason: unknown) => void
const nodeProcess = (
  globalThis as {
    process?: { on?: (event: 'unhandledRejection', listener: UnhandledRejectionListener) => void }
  }
).process

const isTeardownFetchRejection = (reason: unknown): boolean => {
  if (!(reason instanceof TypeError) || reason.message !== 'fetch failed') {
    return false
  }
  const cause = (reason as { cause?: unknown }).cause
  if (!cause || typeof cause !== 'object') {
    return false
  }
  return (cause as { code?: unknown }).code === 'ECONNREFUSED'
}

nodeProcess?.on?.('unhandledRejection', (reason: unknown) => {
  if (isTeardownFetchRejection(reason)) {
    return
  }
  throw reason
})

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
