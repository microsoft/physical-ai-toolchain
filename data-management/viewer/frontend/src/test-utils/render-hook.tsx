import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, type RenderHookOptions } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'

export function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: Infinity, staleTime: Infinity },
      mutations: { retry: false },
    },
  })
}

export interface RenderHookWithProvidersOptions<TProps> extends RenderHookOptions<TProps> {
  queryClient?: QueryClient
}

export function renderHookWithProviders<TResult, TProps>(
  callback: (props: TProps) => TResult,
  options?: RenderHookWithProvidersOptions<TProps>,
) {
  const client = options?.queryClient ?? createTestQueryClient()
  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client }, children)
  return { ...renderHook(callback, { ...options, wrapper }), queryClient: client }
}
