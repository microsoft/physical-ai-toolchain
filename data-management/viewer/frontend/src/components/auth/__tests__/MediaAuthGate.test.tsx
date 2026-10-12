import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { mutationFetch } from '@/lib/api-client'

import { MediaAuthGate } from '../MediaAuthGate'

const account = vi.hoisted(() => ({ id: 'account-alpha' }))
vi.mock('@azure/msal-react', () => ({
  useMsal: () => ({
    instance: { getActiveAccount: () => ({ homeAccountId: account.id }) },
  }),
}))
vi.mock('@/lib/api-client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api-client')>()
  return { ...actual, mutationFetch: vi.fn() }
})

describe('MediaAuthGate', () => {
  beforeEach(() => {
    account.id = 'account-alpha'
    vi.useFakeTimers()
    vi.mocked(mutationFetch).mockReset()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('waits for authenticated cookie issuance before mounting native media', async () => {
    let resolveSession!: (response: Response) => void
    vi.mocked(mutationFetch).mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          resolveSession = resolve
        }),
    )
    render(
      <MediaAuthGate>
        <video data-testid="protected-video" src="/api/datasets/demo/episodes/0/video/front">
          <track kind="captions" />
        </video>
      </MediaAuthGate>,
    )
    expect(screen.queryByTestId('protected-video')).not.toBeInTheDocument()
    await act(async () => {
      resolveSession(Response.json({ expires_in: 300 }))
    })
    expect(screen.getByTestId('protected-video')).toBeInTheDocument()
    expect(mutationFetch).toHaveBeenCalledWith(
      '/api/auth/media-session',
      expect.objectContaining({ method: 'POST', signal: expect.any(AbortSignal) }),
    )
  })

  it('renews at half the server lifetime without unmounting playback', async () => {
    vi.mocked(mutationFetch).mockImplementation(() =>
      Promise.resolve(Response.json({ expires_in: 300 })),
    )
    await act(async () => {
      render(
        <MediaAuthGate>
          <span>Playback</span>
        </MediaAuthGate>,
      )
    })
    expect(screen.getByText('Playback')).toBeInTheDocument()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(149_999)
    })
    expect(mutationFetch).toHaveBeenCalledTimes(1)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1)
    })
    expect(mutationFetch).toHaveBeenCalledTimes(2)
    expect(screen.getByText('Playback')).toBeInTheDocument()
  })

  it.each([
    Response.json({ detail: 'Authentication required' }, { status: 401 }),
    Response.json({ expires_in: 0 }),
  ])('fails visibly without publishing unauthorized media', async (response) => {
    const errorLog = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    vi.mocked(mutationFetch).mockResolvedValueOnce(response)
    await act(async () => {
      render(
        <MediaAuthGate>
          <span>Playback</span>
        </MediaAuthGate>,
      )
    })
    expect(screen.getByRole('alert')).toHaveTextContent('Dataset media authentication')
    expect(screen.queryByText('Playback')).not.toBeInTheDocument()
    expect(errorLog).toHaveBeenCalled()
  })

  it('cancels renewal and in-flight requests on unmount', async () => {
    vi.mocked(mutationFetch).mockResolvedValueOnce(Response.json({ expires_in: 300 }))
    let unmount!: () => void
    await act(async () => {
      unmount = render(<MediaAuthGate>Playback</MediaAuthGate>).unmount
    })
    const signal = vi.mocked(mutationFetch).mock.calls[0][1]?.signal
    unmount()
    expect(signal?.aborted).toBe(true)
    await vi.advanceTimersByTimeAsync(300_000)
    expect(mutationFetch).toHaveBeenCalledTimes(1)
  })

  it('requires a fresh session when the active account changes', async () => {
    vi.mocked(mutationFetch).mockResolvedValueOnce(Response.json({ expires_in: 300 }))
    let rerender!: (element: React.ReactNode) => void
    await act(async () => {
      rerender = render(<MediaAuthGate>Playback</MediaAuthGate>).rerender
    })
    const previousSignal = vi.mocked(mutationFetch).mock.calls[0][1]?.signal
    let resolveSession!: (response: Response) => void
    vi.mocked(mutationFetch).mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          resolveSession = resolve
        }),
    )
    account.id = 'account-beta'
    rerender(<MediaAuthGate>Playback</MediaAuthGate>)
    expect(previousSignal?.aborted).toBe(true)
    expect(screen.queryByText('Playback')).not.toBeInTheDocument()
    await act(async () => {
      resolveSession(Response.json({ expires_in: 61 }))
    })
    expect(screen.getByText('Playback')).toBeInTheDocument()
  })

  it('stops exposing playback when renewal fails', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined)
    vi.mocked(mutationFetch)
      .mockResolvedValueOnce(Response.json({ expires_in: 60 }))
      .mockRejectedValueOnce(new Error('Network failure'))
    await act(async () => {
      render(<MediaAuthGate>Playback</MediaAuthGate>)
    })
    expect(screen.getByText('Playback')).toBeInTheDocument()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000)
    })
    expect(screen.getByRole('alert')).toBeInTheDocument()
    expect(screen.queryByText('Playback')).not.toBeInTheDocument()
    await vi.advanceTimersByTimeAsync(60_000)
    expect(mutationFetch).toHaveBeenCalledTimes(2)
  })
})
