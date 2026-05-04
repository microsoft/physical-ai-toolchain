import { QueryClient } from '@tanstack/react-query'
import { describe, expect, it } from 'vitest'

import { queryClient } from '@/lib/query-client'

describe('queryClient', () => {
  it('is a QueryClient instance', () => {
    expect(queryClient).toBeInstanceOf(QueryClient)
  })

  it('uses 5 minute staleTime and retry of 1 for queries', () => {
    const queryDefaults = queryClient.getDefaultOptions().queries
    expect(queryDefaults?.staleTime).toBe(1000 * 60 * 5)
    expect(queryDefaults?.retry).toBe(1)
  })
})
