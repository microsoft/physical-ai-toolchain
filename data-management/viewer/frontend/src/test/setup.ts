import '@testing-library/jest-dom/vitest'

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
