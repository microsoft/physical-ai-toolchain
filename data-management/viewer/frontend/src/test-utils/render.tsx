import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  render,
  renderHook,
  type RenderHookOptions,
  type RenderOptions,
} from '@testing-library/react'
import { type ReactElement, type ReactNode } from 'react'

export function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  })
}

export function withQueryClient(client: QueryClient = createTestQueryClient()) {
  return function QueryWrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>
  }
}

export function renderWithQuery(
  ui: ReactElement,
  client: QueryClient = createTestQueryClient(),
  options?: Omit<RenderOptions, 'wrapper'>,
) {
  return render(ui, { wrapper: withQueryClient(client), ...options })
}

export interface RenderHookWithProvidersOptions<TProps> extends RenderHookOptions<TProps> {
  queryClient?: QueryClient
}

export function renderHookWithProviders<TResult, TProps>(
  callback: (props: TProps) => TResult,
  options: RenderHookWithProvidersOptions<TProps> = {},
) {
  const { queryClient = createTestQueryClient(), ...rest } = options
  const result = renderHook(callback, {
    wrapper: withQueryClient(queryClient),
    ...rest,
  })
  return { ...result, queryClient }
}
