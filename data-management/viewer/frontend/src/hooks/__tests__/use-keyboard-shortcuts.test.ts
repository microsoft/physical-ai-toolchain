import { renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  formatShortcut,
  type KeyboardShortcut,
  useAnnotationShortcuts,
  useKeyboardShortcuts,
} from '../use-keyboard-shortcuts'

function fireKeyDown(options: KeyboardEventInit) {
  window.dispatchEvent(new KeyboardEvent('keydown', { ...options, bubbles: true }))
}

describe('useKeyboardShortcuts', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('calls action on matching keydown', () => {
    const action = vi.fn()
    const shortcuts: KeyboardShortcut[] = [{ key: 'a', action, description: 'Test A' }]

    renderHook(() => useKeyboardShortcuts(shortcuts))

    fireKeyDown({ key: 'a' })
    expect(action).toHaveBeenCalledOnce()
  })

  it('matches case-insensitively', () => {
    const action = vi.fn()
    const shortcuts: KeyboardShortcut[] = [{ key: 'a', action, description: 'Test A' }]

    renderHook(() => useKeyboardShortcuts(shortcuts))

    fireKeyDown({ key: 'A' })
    expect(action).toHaveBeenCalledOnce()
  })

  it('matches ctrl modifier (ctrlKey)', () => {
    const action = vi.fn()
    const shortcuts: KeyboardShortcut[] = [{ key: 's', ctrl: true, action, description: 'Save' }]

    renderHook(() => useKeyboardShortcuts(shortcuts))

    fireKeyDown({ key: 's', ctrlKey: true })
    expect(action).toHaveBeenCalledOnce()
  })

  it('matches ctrl modifier (metaKey for Mac)', () => {
    const action = vi.fn()
    const shortcuts: KeyboardShortcut[] = [{ key: 's', ctrl: true, action, description: 'Save' }]

    renderHook(() => useKeyboardShortcuts(shortcuts))

    fireKeyDown({ key: 's', metaKey: true })
    expect(action).toHaveBeenCalledOnce()
  })

  it('does not fire when ctrl required but not pressed', () => {
    const action = vi.fn()
    const shortcuts: KeyboardShortcut[] = [{ key: 's', ctrl: true, action, description: 'Save' }]

    renderHook(() => useKeyboardShortcuts(shortcuts))

    fireKeyDown({ key: 's' })
    expect(action).not.toHaveBeenCalled()
  })

  it('matches shift modifier', () => {
    const action = vi.fn()
    const shortcuts: KeyboardShortcut[] = [{ key: 'z', shift: true, action, description: 'Test' }]

    renderHook(() => useKeyboardShortcuts(shortcuts))

    fireKeyDown({ key: 'z', shiftKey: true })
    expect(action).toHaveBeenCalledOnce()
  })

  it('matches alt modifier', () => {
    const action = vi.fn()
    const shortcuts: KeyboardShortcut[] = [{ key: 'x', alt: true, action, description: 'Test' }]

    renderHook(() => useKeyboardShortcuts(shortcuts))

    fireKeyDown({ key: 'x', altKey: true })
    expect(action).toHaveBeenCalledOnce()
  })

  it('does not fire when disabled', () => {
    const action = vi.fn()
    const shortcuts: KeyboardShortcut[] = [{ key: 'a', action, description: 'Test' }]

    renderHook(() => useKeyboardShortcuts(shortcuts, { enabled: false }))

    fireKeyDown({ key: 'a' })
    expect(action).not.toHaveBeenCalled()
  })

  it('skips when target is INPUT element', () => {
    const action = vi.fn()
    const shortcuts: KeyboardShortcut[] = [{ key: 'a', action, description: 'Test' }]

    renderHook(() => useKeyboardShortcuts(shortcuts))

    const input = document.createElement('input')
    document.body.appendChild(input)
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'a', bubbles: true }))
    document.body.removeChild(input)

    expect(action).not.toHaveBeenCalled()
  })

  it('allows Escape through input elements', () => {
    const action = vi.fn()
    const shortcuts: KeyboardShortcut[] = [{ key: 'Escape', action, description: 'Close' }]

    renderHook(() => useKeyboardShortcuts(shortcuts))

    const input = document.createElement('input')
    document.body.appendChild(input)
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    document.body.removeChild(input)

    expect(action).toHaveBeenCalledOnce()
  })

  it('allows Ctrl+S through input elements', () => {
    const action = vi.fn()
    const shortcuts: KeyboardShortcut[] = [{ key: 's', ctrl: true, action, description: 'Save' }]

    renderHook(() => useKeyboardShortcuts(shortcuts))

    const input = document.createElement('input')
    document.body.appendChild(input)
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 's', ctrlKey: true, bubbles: true }))
    document.body.removeChild(input)

    expect(action).toHaveBeenCalledOnce()
  })

  it('prevents default when configured', () => {
    const action = vi.fn()
    const shortcuts: KeyboardShortcut[] = [{ key: 'a', action, description: 'Test' }]

    renderHook(() => useKeyboardShortcuts(shortcuts, { preventDefault: true }))

    const event = new KeyboardEvent('keydown', { key: 'a', bubbles: true })
    const preventSpy = vi.spyOn(event, 'preventDefault')
    window.dispatchEvent(event)

    expect(preventSpy).toHaveBeenCalled()
  })

  it('cleans up event listener on unmount', () => {
    const action = vi.fn()
    const shortcuts: KeyboardShortcut[] = [{ key: 'a', action, description: 'Test' }]

    const { unmount } = renderHook(() => useKeyboardShortcuts(shortcuts))
    unmount()

    fireKeyDown({ key: 'a' })
    expect(action).not.toHaveBeenCalled()
  })
})

describe('formatShortcut', () => {
  beforeEach(() => {
    Object.defineProperty(navigator, 'platform', {
      value: 'Win32',
      writable: true,
      configurable: true,
    })
  })

  it('formats simple key', () => {
    expect(formatShortcut({ key: 'a', action: vi.fn(), description: '' })).toBe('A')
  })

  it('formats ctrl shortcut on non-Mac', () => {
    expect(formatShortcut({ key: 's', ctrl: true, action: vi.fn(), description: '' })).toBe(
      'Ctrl+S',
    )
  })

  it('formats ctrl shortcut on Mac', () => {
    Object.defineProperty(navigator, 'platform', {
      value: 'MacIntel',
      configurable: true,
    })
    expect(formatShortcut({ key: 's', ctrl: true, action: vi.fn(), description: '' })).toBe('⌘+S')
  })

  it('formats shift modifier', () => {
    expect(formatShortcut({ key: 'z', shift: true, action: vi.fn(), description: '' })).toBe(
      'Shift+Z',
    )
  })

  it('formats alt modifier on non-Mac', () => {
    expect(formatShortcut({ key: 'x', alt: true, action: vi.fn(), description: '' })).toBe('Alt+X')
  })

  it('formats alt modifier on Mac', () => {
    Object.defineProperty(navigator, 'platform', {
      value: 'MacIntel',
      configurable: true,
    })
    expect(formatShortcut({ key: 'x', alt: true, action: vi.fn(), description: '' })).toBe('⌥+X')
  })

  it('formats special keys', () => {
    expect(formatShortcut({ key: ' ', action: vi.fn(), description: '' })).toBe('Space')
    expect(formatShortcut({ key: 'arrowleft', action: vi.fn(), description: '' })).toBe('←')
    expect(formatShortcut({ key: 'arrowright', action: vi.fn(), description: '' })).toBe('→')
    expect(formatShortcut({ key: 'arrowup', action: vi.fn(), description: '' })).toBe('↑')
    expect(formatShortcut({ key: 'arrowdown', action: vi.fn(), description: '' })).toBe('↓')
    expect(formatShortcut({ key: 'enter', action: vi.fn(), description: '' })).toBe('↵')
    expect(formatShortcut({ key: 'escape', action: vi.fn(), description: '' })).toBe('Esc')
  })

  it('formats combined modifiers', () => {
    expect(
      formatShortcut({
        key: 's',
        ctrl: true,
        shift: true,
        action: vi.fn(),
        description: '',
      }),
    ).toBe('Ctrl+Shift+S')
  })
})

describe('useAnnotationShortcuts', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('creates shortcuts for all annotation actions', () => {
    const actions = {
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

    renderHook(() => useAnnotationShortcuts(actions))

    fireKeyDown({ key: 's' })
    expect(actions.markSuccess).toHaveBeenCalledOnce()

    fireKeyDown({ key: 'p' })
    expect(actions.markPartial).toHaveBeenCalledOnce()

    fireKeyDown({ key: 'f' })
    expect(actions.markFailure).toHaveBeenCalledOnce()

    fireKeyDown({ key: ' ' })
    expect(actions.togglePlayback).toHaveBeenCalledOnce()

    fireKeyDown({ key: 'ArrowLeft' })
    expect(actions.previousFrame).toHaveBeenCalledOnce()

    fireKeyDown({ key: 'ArrowRight' })
    expect(actions.nextFrame).toHaveBeenCalledOnce()

    fireKeyDown({ key: 'ArrowLeft', shiftKey: true })
    expect(actions.previousEpisode).toHaveBeenCalledOnce()

    fireKeyDown({ key: 'ArrowRight', shiftKey: true })
    expect(actions.nextEpisode).toHaveBeenCalledOnce()

    fireKeyDown({ key: '?' })
    expect(actions.showHelp).toHaveBeenCalledOnce()
  })

  it('calls setRating with numeric keys', () => {
    const actions = {
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

    renderHook(() => useAnnotationShortcuts(actions))

    for (let i = 1; i <= 5; i++) {
      fireKeyDown({ key: String(i) })
    }

    expect(actions.setRating).toHaveBeenCalledTimes(5)
  })

  it('handles Ctrl+S for save', () => {
    const actions = {
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

    renderHook(() => useAnnotationShortcuts(actions))

    fireKeyDown({ key: 's', ctrlKey: true })
    expect(actions.save).toHaveBeenCalledOnce()
  })

  it('handles Ctrl+Enter for saveAndAdvance', () => {
    const actions = {
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

    renderHook(() => useAnnotationShortcuts(actions))

    fireKeyDown({ key: 'Enter' })
    expect(actions.saveAndAdvance).toHaveBeenCalledOnce()
  })
})
