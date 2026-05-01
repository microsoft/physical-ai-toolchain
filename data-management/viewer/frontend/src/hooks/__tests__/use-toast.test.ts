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
  it('starts with an empty toast queue', () => {
    const { result } = renderHook(() => useToast())

    expect(result.current.toasts).toEqual([])
  })

  it('toast() appends a new toast and returns its id', () => {
    const { result } = renderHook(() => useToast())

    let id = ''
    act(() => {
      id = result.current.toast({ title: 'Saved', description: 'Changes persisted' })
    })

    expect(id).toBeTruthy()
    expect(result.current.toasts).toHaveLength(1)
    expect(result.current.toasts[0]).toMatchObject({
      id,
      title: 'Saved',
      description: 'Changes persisted',
    })
  })

  it('supports multiple queued toasts in insertion order', () => {
    const { result } = renderHook(() => useToast())

    act(() => {
      result.current.toast({ title: 'First' })
      result.current.toast({ title: 'Second', variant: 'destructive' })
    })

    expect(result.current.toasts).toHaveLength(2)
    expect(result.current.toasts[0].title).toBe('First')
    expect(result.current.toasts[1].title).toBe('Second')
    expect(result.current.toasts[1].variant).toBe('destructive')
  })

  it('dismiss() removes a toast by id', () => {
    const { result } = renderHook(() => useToast())

    let firstId = ''
    let secondId = ''
    act(() => {
      firstId = result.current.toast({ title: 'First' })
      secondId = result.current.toast({ title: 'Second' })
    })

    act(() => {
      result.current.dismiss(firstId)
    })

    expect(result.current.toasts).toHaveLength(1)
    expect(result.current.toasts[0].id).toBe(secondId)
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

  it('dismiss() of an unknown id is a no-op', () => {
    const { result } = renderHook(() => useToast())

    act(() => {
      result.current.toast({ title: 'Keep' })
    })

    act(() => {
      result.current.dismiss('does-not-exist')
    })

    expect(result.current.toasts).toHaveLength(1)
  })
})
