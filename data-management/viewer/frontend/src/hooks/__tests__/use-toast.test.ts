import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useToast } from '../use-toast'

describe('useToast', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('starts with an empty toast list', () => {
    const { result } = renderHook(() => useToast())
    expect(result.current.toasts).toEqual([])
  })

  it('adds a toast with generated id', () => {
    const { result } = renderHook(() => useToast())

    act(() => {
      result.current.toast({ title: 'Hello', description: 'World' })
    })

    expect(result.current.toasts).toHaveLength(1)
    expect(result.current.toasts[0]).toMatchObject({
      title: 'Hello',
      description: 'World',
    })
    expect(result.current.toasts[0].id).toBeDefined()
  })

  it('supports variant option', () => {
    const { result } = renderHook(() => useToast())

    act(() => {
      result.current.toast({ title: 'Error', variant: 'destructive' })
    })

    expect(result.current.toasts[0].variant).toBe('destructive')
  })

  it('dismisses a toast by id', () => {
    const { result } = renderHook(() => useToast())

    let toastId: string
    act(() => {
      toastId = result.current.toast({ title: 'Test' })
    })

    expect(result.current.toasts).toHaveLength(1)

    act(() => {
      result.current.dismiss(toastId)
    })

    expect(result.current.toasts).toHaveLength(0)
  })

  it('auto-dismisses after 5000ms', () => {
    const { result } = renderHook(() => useToast())

    act(() => {
      result.current.toast({ title: 'Auto dismiss' })
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

  it('handles multiple toasts independently', () => {
    const { result } = renderHook(() => useToast())

    act(() => {
      result.current.toast({ title: 'First' })
    })

    act(() => {
      vi.advanceTimersByTime(2000)
    })

    act(() => {
      result.current.toast({ title: 'Second' })
    })

    expect(result.current.toasts).toHaveLength(2)

    // First toast expires at 5000ms from creation
    act(() => {
      vi.advanceTimersByTime(3000)
    })
    expect(result.current.toasts).toHaveLength(1)
    expect(result.current.toasts[0].title).toBe('Second')

    // Second toast expires at 5000ms from its creation
    act(() => {
      vi.advanceTimersByTime(2000)
    })
    expect(result.current.toasts).toHaveLength(0)
  })

  it('dismiss does nothing for non-existent id', () => {
    const { result } = renderHook(() => useToast())

    act(() => {
      result.current.toast({ title: 'Keep' })
    })

    act(() => {
      result.current.dismiss('non-existent')
    })

    expect(result.current.toasts).toHaveLength(1)
  })
})
