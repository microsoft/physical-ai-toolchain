import { useQuery } from '@tanstack/react-query'

import { fetchPrincipalContext } from '@/lib/principal-context'

export function usePrincipalContext() {
  return useQuery({
    queryKey: ['auth', 'principal-context'],
    queryFn: fetchPrincipalContext,
    staleTime: Number.POSITIVE_INFINITY,
  })
}
