import '@testing-library/jest-dom/vitest'

// Background TanStack Query refetches can fire after a test's afterEach restores
// the original fetch. Relative '/api/...' URLs then resolve against happy-dom's
// default origin (http://localhost:3000) and reject with ECONNREFUSED. These
// rejections are harmless test-teardown noise; swallow them so they do not
// crash the worker and abort the run.
process.on('unhandledRejection', (reason: unknown) => {
  const message = reason instanceof Error ? reason.message : String(reason)
  if (message.includes('ECONNREFUSED') || message.includes('fetch failed')) {
    return
  }
  throw reason
})
