import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useToast } from '@/hooks/use-toast'

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('useToast', () => {
  it('starts with no toasts', () => {
    const { result } = renderHook(() => useToast())
    expect(result.current.toasts).toEqual([])
  })

  it('adds a toast when toast() is called and returns its id', () => {
    const { result } = renderHook(() => useToast())

    let id = ''
    act(() => {
      id = result.current.toast({ title: 'Hello', description: 'World' })
    })

    expect(id).toMatch(/.+/)
    expect(result.current.toasts).toHaveLength(1)
    expect(result.current.toasts[0]).toMatchObject({
      id,
      title: 'Hello',
      description: 'World',
    })
  })

  it('generates a unique id for each toast', () => {
    const { result } = renderHook(() => useToast())

    let firstId = ''
    let secondId = ''
    act(() => {
      firstId = result.current.toast({ title: 'one' })
    })
    act(() => {
      secondId = result.current.toast({ title: 'two' })
    })

    expect(firstId).not.toBe(secondId)
    expect(result.current.toasts.map((t) => t.id)).toEqual([firstId, secondId])
  })

  it('auto-dismisses a toast after 5 seconds', () => {
    const { result } = renderHook(() => useToast())

    act(() => {
      result.current.toast({ title: 'Auto' })
    })
    expect(result.current.toasts).toHaveLength(1)

    act(() => {
      vi.advanceTimersByTime(4999)
    })
    expect(result.current.toasts).toHaveLength(1)

    act(() => {
      vi.advanceTimersByTime(1)
    })
    expect(result.current.toasts).toHaveLength(0)
  })

  it('dismiss() removes the toast with the matching id and leaves others intact', () => {
    const { result } = renderHook(() => useToast())

    let firstId = ''
    let secondId = ''
    act(() => {
      firstId = result.current.toast({ title: 'one' })
      secondId = result.current.toast({ title: 'two' })
    })
    expect(result.current.toasts).toHaveLength(2)

    act(() => {
      result.current.dismiss(firstId)
    })

    expect(result.current.toasts).toHaveLength(1)
    expect(result.current.toasts[0].id).toBe(secondId)
  })

  it('dismiss() with an unknown id is a no-op', () => {
    const { result } = renderHook(() => useToast())

    act(() => {
      result.current.toast({ title: 'one' })
    })
    expect(result.current.toasts).toHaveLength(1)

    act(() => {
      result.current.dismiss('does-not-exist')
    })
    expect(result.current.toasts).toHaveLength(1)
  })

  it('supports the destructive variant', () => {
    const { result } = renderHook(() => useToast())

    act(() => {
      result.current.toast({ title: 'Boom', variant: 'destructive' })
    })

    expect(result.current.toasts[0].variant).toBe('destructive')
  })
})
