import { renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  formatShortcut,
  useAnnotationShortcuts,
  useKeyboardShortcuts,
  type KeyboardShortcut,
} from '@/hooks/use-keyboard-shortcuts'

const originalPlatform = Object.getOwnPropertyDescriptor(navigator, 'platform')

function setPlatform(value: string) {
  Object.defineProperty(navigator, 'platform', {
    value,
    configurable: true,
    writable: true,
  })
}

function dispatchKey(init: KeyboardEventInit & { target?: EventTarget }) {
  const event = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init })
  if (init.target) {
    Object.defineProperty(event, 'target', { value: init.target })
  }
  window.dispatchEvent(event)
  return event
}

beforeEach(() => {
  setPlatform('Linux x86_64')
})

afterEach(() => {
  if (originalPlatform) {
    Object.defineProperty(navigator, 'platform', originalPlatform)
  }
  vi.restoreAllMocks()
})

describe('useKeyboardShortcuts', () => {
  it('invokes a matching shortcut and prevents default by default', () => {
    const action = vi.fn()
    renderHook(() =>
      useKeyboardShortcuts([{ key: 's', action, description: 'Save' }]),
    )

    const event = dispatchKey({ key: 's' })
    expect(action).toHaveBeenCalledTimes(1)
    expect(event.defaultPrevented).toBe(true)
  })

  it('does not invoke shortcuts when disabled', () => {
    const action = vi.fn()
    renderHook(() =>
      useKeyboardShortcuts([{ key: 's', action, description: 'Save' }], { enabled: false }),
    )

    dispatchKey({ key: 's' })
    expect(action).not.toHaveBeenCalled()
  })

  it('does not preventDefault when preventDefault is false', () => {
    const action = vi.fn()
    renderHook(() =>
      useKeyboardShortcuts([{ key: 's', action, description: 'Save' }], { preventDefault: false }),
    )

    const event = dispatchKey({ key: 's' })
    expect(action).toHaveBeenCalledTimes(1)
    expect(event.defaultPrevented).toBe(false)
  })

  it('matches modifier keys (ctrl/meta, shift, alt)', () => {
    const ctrlAction = vi.fn()
    const shiftAction = vi.fn()
    const altAction = vi.fn()
    renderHook(() =>
      useKeyboardShortcuts([
        { key: 's', ctrl: true, action: ctrlAction, description: 'Save' },
        { key: 'a', shift: true, action: shiftAction, description: 'Shift A' },
        { key: 'b', alt: true, action: altAction, description: 'Alt B' },
      ]),
    )

    dispatchKey({ key: 's', ctrlKey: true })
    dispatchKey({ key: 'a', shiftKey: true })
    dispatchKey({ key: 'b', altKey: true })
    // metaKey also satisfies ctrl
    dispatchKey({ key: 's', metaKey: true })

    expect(ctrlAction).toHaveBeenCalledTimes(2)
    expect(shiftAction).toHaveBeenCalledTimes(1)
    expect(altAction).toHaveBeenCalledTimes(1)
  })

  it('ignores key events from input fields except Escape and Ctrl+S', () => {
    const sAction = vi.fn()
    const escAction = vi.fn()
    const ctrlSAction = vi.fn()
    renderHook(() =>
      useKeyboardShortcuts([
        { key: 's', action: sAction, description: 'S' },
        { key: 'Escape', action: escAction, description: 'Esc' },
        { key: 's', ctrl: true, action: ctrlSAction, description: 'Save' },
      ]),
    )

    const input = document.createElement('input')
    document.body.appendChild(input)
    const textarea = document.createElement('textarea')
    document.body.appendChild(textarea)
    const select = document.createElement('select')
    document.body.appendChild(select)
    const editable = document.createElement('div')
    editable.contentEditable = 'true'
    document.body.appendChild(editable)

    try {
      dispatchKey({ key: 's', target: input })
      dispatchKey({ key: 's', target: textarea })
      dispatchKey({ key: 's', target: select })
      dispatchKey({ key: 's', target: editable })
      expect(sAction).not.toHaveBeenCalled()

      dispatchKey({ key: 'Escape', target: input })
      expect(escAction).toHaveBeenCalledTimes(1)

      dispatchKey({ key: 's', ctrlKey: true, target: input })
      expect(ctrlSAction).toHaveBeenCalledTimes(1)
    } finally {
      input.remove()
      textarea.remove()
      select.remove()
      editable.remove()
    }
  })

  it('removes the listener on unmount', () => {
    const action = vi.fn()
    const { unmount } = renderHook(() =>
      useKeyboardShortcuts([{ key: 's', action, description: 'Save' }]),
    )

    dispatchKey({ key: 's' })
    expect(action).toHaveBeenCalledTimes(1)

    unmount()
    dispatchKey({ key: 's' })
    expect(action).toHaveBeenCalledTimes(1)
  })

  it('matches keys case-insensitively', () => {
    const action = vi.fn()
    renderHook(() => useKeyboardShortcuts([{ key: 'S', action, description: 'Save' }]))

    dispatchKey({ key: 's' })
    expect(action).toHaveBeenCalledTimes(1)
  })
})

describe('formatShortcut', () => {
  it('formats Ctrl + key on non-Mac platforms', () => {
    setPlatform('Linux x86_64')
    const shortcut: KeyboardShortcut = {
      key: 's',
      ctrl: true,
      action: () => {},
      description: 'Save',
    }
    expect(formatShortcut(shortcut)).toBe('Ctrl+S')
  })

  it('formats ⌘ on Mac platforms', () => {
    setPlatform('MacIntel')
    const shortcut: KeyboardShortcut = {
      key: 's',
      ctrl: true,
      action: () => {},
      description: 'Save',
    }
    expect(formatShortcut(shortcut)).toBe('⌘+S')
  })

  it('formats ⌥ for alt on Mac', () => {
    setPlatform('MacIntel')
    const shortcut: KeyboardShortcut = {
      key: 'a',
      alt: true,
      action: () => {},
      description: 'Alt A',
    }
    expect(formatShortcut(shortcut)).toBe('⌥+A')
  })

  it('formats Alt for alt on non-Mac', () => {
    setPlatform('Linux x86_64')
    const shortcut: KeyboardShortcut = {
      key: 'a',
      alt: true,
      action: () => {},
      description: 'Alt A',
    }
    expect(formatShortcut(shortcut)).toBe('Alt+A')
  })

  it('includes Shift in the output', () => {
    const shortcut: KeyboardShortcut = {
      key: 'x',
      shift: true,
      action: () => {},
      description: 'Shift X',
    }
    expect(formatShortcut(shortcut)).toBe('Shift+X')
  })

  it.each([
    [' ', 'Space'],
    ['ArrowLeft', '←'],
    ['ArrowRight', '→'],
    ['ArrowUp', '↑'],
    ['ArrowDown', '↓'],
    ['Enter', '↵'],
    ['Escape', 'Esc'],
  ])('formats special key %s as %s', (key, expected) => {
    expect(formatShortcut({ key, action: () => {}, description: '' })).toBe(expected)
  })
})

describe('useAnnotationShortcuts', () => {
  function makeActions() {
    return {
      markSuccess: vi.fn(),
      markPartial: vi.fn(),
      markFailure: vi.fn(),
      setRating: vi.fn(),
      toggleJittery: vi.fn(),
      togglePlayback: vi.fn(),
      previousFrame: vi.fn(),
      nextFrame: vi.fn(),
      previousEpisode: vi.fn(),
      nextEpisode: vi.fn(),
      saveAndAdvance: vi.fn(),
      save: vi.fn(),
      showHelp: vi.fn(),
    }
  }

  it('wires annotation, rating, playback, navigation, and workflow shortcuts', () => {
    const actions = makeActions()
    renderHook(() => useAnnotationShortcuts(actions))

    dispatchKey({ key: 's' })
    dispatchKey({ key: 'p' })
    dispatchKey({ key: 'f' })
    dispatchKey({ key: 'j' })
    dispatchKey({ key: '3' })
    dispatchKey({ key: ' ' })
    dispatchKey({ key: 'ArrowLeft' })
    dispatchKey({ key: 'ArrowRight' })
    dispatchKey({ key: 'ArrowLeft', shiftKey: true })
    dispatchKey({ key: 'ArrowRight', shiftKey: true })
    dispatchKey({ key: 'Enter' })
    dispatchKey({ key: 's', ctrlKey: true })
    dispatchKey({ key: '?' })

    expect(actions.markSuccess).toHaveBeenCalledTimes(1)
    expect(actions.markPartial).toHaveBeenCalledTimes(1)
    expect(actions.markFailure).toHaveBeenCalledTimes(1)
    expect(actions.toggleJittery).toHaveBeenCalledTimes(1)
    expect(actions.setRating).toHaveBeenCalledWith(3)
    expect(actions.togglePlayback).toHaveBeenCalledTimes(1)
    expect(actions.previousFrame).toHaveBeenCalledTimes(1)
    expect(actions.nextFrame).toHaveBeenCalledTimes(1)
    expect(actions.previousEpisode).toHaveBeenCalledTimes(1)
    expect(actions.nextEpisode).toHaveBeenCalledTimes(1)
    expect(actions.saveAndAdvance).toHaveBeenCalledTimes(1)
    expect(actions.save).toHaveBeenCalledTimes(1)
    expect(actions.showHelp).toHaveBeenCalledTimes(1)
  })

  it('omits insert frame shortcut when action is not provided', () => {
    const actions = makeActions()
    const { result } = renderHook(() => useAnnotationShortcuts(actions))

    expect(result.current.find((s) => s.key === 'i')).toBeUndefined()
  })

  it('wires insert frame shortcut when action is provided', () => {
    const actions = makeActions()
    const insertFrame = vi.fn()
    const { result } = renderHook(() =>
      useAnnotationShortcuts({ ...actions, insertFrame }),
    )

    dispatchKey({ key: 'i' })
    expect(insertFrame).toHaveBeenCalledTimes(1)
    expect(result.current.find((s) => s.key === 'i')).toBeDefined()
  })

  it('handles each rating 1-5', () => {
    const actions = makeActions()
    renderHook(() => useAnnotationShortcuts(actions))

    for (const key of ['1', '2', '3', '4', '5']) {
      dispatchKey({ key })
    }
    expect(actions.setRating.mock.calls.map((c) => c[0])).toEqual([1, 2, 3, 4, 5])
  })
})
