/**
 * Shared test utilities for the dataviewer frontend.
 *
 * Centralizes QueryClient construction, render wrappers, and Response helpers
 * that downstream test suites would otherwise duplicate.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  render,
  renderHook,
  type RenderHookOptions,
  type RenderOptions,
} from '@testing-library/react'
import type { ReactElement, ReactNode } from 'react'

/**
 * Build a QueryClient configured for tests: no retries, no caching across cases.
 */
export function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  })
}

interface QueryWrapperProps {
  children: ReactNode
}

/**
 * Higher-order wrapper component for `renderHook` that provides a QueryClient.
 *
 * Defaults to a fresh client from `createTestQueryClient()` so each test gets
 * an isolated cache (`gcTime: 0` prevents cross-test bleed) with deterministic
 * failure semantics (`retry: false` surfaces errors on the first attempt).
 * Pass an explicit client to share state across renders within a single test.
 */
export function withQueryClient(client: QueryClient = createTestQueryClient()) {
  return function QueryWrapper({ children }: QueryWrapperProps) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>
  }
}

/**
 * Render a React element wrapped in a QueryClientProvider.
 */
export function renderWithQuery(
  ui: ReactElement,
  client: QueryClient = createTestQueryClient(),
  options?: Omit<RenderOptions, 'wrapper'>,
) {
  const Wrapper = withQueryClient(client)
  return {
    client,
    ...render(ui, { wrapper: Wrapper, ...options }),
  }
}

/**
 * Render a hook wrapped in a QueryClientProvider.
 */
export function renderHookWithQuery<TResult, TProps>(
  callback: (props: TProps) => TResult,
  client: QueryClient = createTestQueryClient(),
  options?: Omit<RenderHookOptions<TProps>, 'wrapper'>,
) {
  const Wrapper = withQueryClient(client)
  return {
    client,
    ...renderHook(callback, { wrapper: Wrapper, ...options }),
  }
}

/**
 * Build a JSON `Response` for `fetch` mocks.
 */
export function jsonResponse<T>(body: T, init: ResponseInit = {}): Response {
  const headers = new Headers(init.headers)
  if (!headers.has('content-type')) {
    headers.set('content-type', 'application/json')
  }
  return new Response(JSON.stringify(body), { status: 200, ...init, headers })
}
