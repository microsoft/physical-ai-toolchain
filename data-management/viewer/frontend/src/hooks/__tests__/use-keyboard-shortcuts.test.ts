import { renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  formatShortcut,
  type KeyboardShortcut,
  useKeyboardShortcuts,
} from '@/hooks/use-keyboard-shortcuts'

function dispatchKey(init: KeyboardEventInit & { target?: EventTarget }) {
  const event = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init })
  if (init.target) {
    init.target.dispatchEvent(event)
  } else {
    window.dispatchEvent(event)
  }
  return event
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('useKeyboardShortcuts', () => {
  it('invokes the matching action on keydown', () => {
    const action = vi.fn()
    const shortcuts: KeyboardShortcut[] = [{ key: 's', action, description: 'Save' }]

    renderHook(() => useKeyboardShortcuts(shortcuts))

    dispatchKey({ key: 's' })

    expect(action).toHaveBeenCalledTimes(1)
  })

  it('matches Ctrl modifier and treats metaKey as equivalent', () => {
    const action = vi.fn()
    renderHook(() => useKeyboardShortcuts([{ key: 's', ctrl: true, action, description: 'Save' }]))

    dispatchKey({ key: 's' })
    expect(action).not.toHaveBeenCalled()

    dispatchKey({ key: 's', ctrlKey: true })
    expect(action).toHaveBeenCalledTimes(1)

    dispatchKey({ key: 's', metaKey: true })
    expect(action).toHaveBeenCalledTimes(2)
  })

  it('requires shift and alt modifiers when specified', () => {
    const action = vi.fn()
    renderHook(() =>
      useKeyboardShortcuts([{ key: 'a', shift: true, alt: true, action, description: 'Combo' }]),
    )

    dispatchKey({ key: 'a', shiftKey: true })
    expect(action).not.toHaveBeenCalled()

    dispatchKey({ key: 'a', shiftKey: true, altKey: true })
    expect(action).toHaveBeenCalledTimes(1)
  })

  it('calls preventDefault by default and skips it when disabled', () => {
    const action = vi.fn()
    const { rerender } = renderHook(
      ({ preventDefault }: { preventDefault: boolean }) =>
        useKeyboardShortcuts([{ key: 'p', action, description: 'Pause' }], { preventDefault }),
      { initialProps: { preventDefault: true } },
    )

    const event = dispatchKey({ key: 'p' })
    expect(action).toHaveBeenCalledTimes(1)
    expect(event.defaultPrevented).toBe(true)

    rerender({ preventDefault: false })
    const event2 = dispatchKey({ key: 'p' })
    expect(action).toHaveBeenCalledTimes(2)
    expect(event2.defaultPrevented).toBe(false)
  })

  it('does nothing when enabled is false', () => {
    const action = vi.fn()
    renderHook(() =>
      useKeyboardShortcuts([{ key: 'x', action, description: 'X' }], { enabled: false }),
    )

    dispatchKey({ key: 'x' })

    expect(action).not.toHaveBeenCalled()
  })

  it('ignores shortcuts while typing in input fields', () => {
    const action = vi.fn()
    renderHook(() => useKeyboardShortcuts([{ key: 's', action, description: 'Save' }]))

    const input = document.createElement('input')
    document.body.appendChild(input)
    try {
      dispatchKey({ key: 's', target: input })
      expect(action).not.toHaveBeenCalled()
    } finally {
      input.remove()
    }
  })

  it('still allows Escape inside input fields', () => {
    const action = vi.fn()
    renderHook(() => useKeyboardShortcuts([{ key: 'Escape', action, description: 'Close' }]))

    const textarea = document.createElement('textarea')
    document.body.appendChild(textarea)
    try {
      dispatchKey({ key: 'Escape', target: textarea })
      expect(action).toHaveBeenCalledTimes(1)
    } finally {
      textarea.remove()
    }
  })

  it('still allows Ctrl+S inside input fields', () => {
    const action = vi.fn()
    renderHook(() => useKeyboardShortcuts([{ key: 's', ctrl: true, action, description: 'Save' }]))

    const input = document.createElement('input')
    document.body.appendChild(input)
    try {
      dispatchKey({ key: 's', ctrlKey: true, target: input })
      expect(action).toHaveBeenCalledTimes(1)
    } finally {
      input.remove()
    }
  })

  it('removes the keydown listener on unmount', () => {
    const action = vi.fn()
    const { unmount } = renderHook(() =>
      useKeyboardShortcuts([{ key: 'q', action, description: 'Quit' }]),
    )

    unmount()
    dispatchKey({ key: 'q' })

    expect(action).not.toHaveBeenCalled()
  })
})

describe('formatShortcut', () => {
  it('renders plain keys uppercased', () => {
    expect(formatShortcut({ key: 's', action: () => {}, description: '' })).toBe('S')
  })

  it('renders modifiers and special keys', () => {
    expect(
      formatShortcut({
        key: ' ',
        shift: true,
        action: () => {},
        description: '',
      }),
    ).toBe('Shift+Space')

    expect(formatShortcut({ key: 'ArrowLeft', action: () => {}, description: '' })).toBe('←')
    expect(formatShortcut({ key: 'ArrowRight', action: () => {}, description: '' })).toBe('→')
    expect(formatShortcut({ key: 'ArrowUp', action: () => {}, description: '' })).toBe('↑')
    expect(formatShortcut({ key: 'ArrowDown', action: () => {}, description: '' })).toBe('↓')
    expect(formatShortcut({ key: 'Enter', action: () => {}, description: '' })).toBe('↵')
    expect(formatShortcut({ key: 'Escape', action: () => {}, description: '' })).toBe('Esc')
  })
})
