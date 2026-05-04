import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, type RenderHookOptions, type RenderHookResult } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'

export function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: Infinity, staleTime: Infinity },
      mutations: { retry: false },
    },
  })
}

export function createWrapper(queryClient: QueryClient) {
  return ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: queryClient }, children)
}

export function renderHookWithQuery<TResult, TProps>(
  callback: (props: TProps) => TResult,
  options?: Omit<RenderHookOptions<TProps>, 'wrapper'> & { queryClient?: QueryClient },
): RenderHookResult<TResult, TProps> & { queryClient: QueryClient } {
  const queryClient = options?.queryClient ?? createTestQueryClient()
  const wrapper = createWrapper(queryClient)
  const result = renderHook(callback, { ...options, wrapper })
  return Object.assign(result, { queryClient })
}
