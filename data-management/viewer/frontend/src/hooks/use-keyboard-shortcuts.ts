/**
 * Keyboard shortcut hook for annotation workflow.
 *
 * Provides a centralized keyboard event handler with
 * support for modifier keys and input field filtering.
 */

import { useCallback, useEffect } from 'react'

export interface KeyboardShortcut {
  /** Key to listen for */
  key: string
  /** Whether Ctrl/Cmd is required */
  ctrl?: boolean
  /** Whether Shift is required */
  shift?: boolean
  /** Whether Alt is required */
  alt?: boolean
  /** Action to perform */
  action: () => void
  /** Description for help display */
  description: string
  /** Category for grouping in help */
  category?: 'playback' | 'navigation' | 'annotation' | 'workflow'
}

interface UseKeyboardShortcutsOptions {
  /** Whether shortcuts are enabled */
  enabled?: boolean
  /** Whether to prevent default browser behavior */
  preventDefault?: boolean
}

/**
 * Hook for managing keyboard shortcuts.
 *
 * @param shortcuts Array of shortcut definitions
 * @param options Configuration options
 *
 * @example
 * ```tsx
 * useKeyboardShortcuts([
 *   { key: 's', action: () => save(), description: 'Mark as Success' },
 *   { key: 's', ctrl: true, action: () => saveAnnotation(), description: 'Save' },
 * ]);
 * ```
 */
export function useKeyboardShortcuts(
  shortcuts: KeyboardShortcut[],
  options: UseKeyboardShortcutsOptions = {},
) {
  const { enabled = true, preventDefault = true } = options

  const handleKeyDown = useCallback(
    (event: KeyboardEvent) => {
      if (!enabled) return

      // Ignore when typing in input fields
      const target = event.target as HTMLElement
      if (
        target.tagName === 'INPUT' ||
        target.tagName === 'TEXTAREA' ||
        target.tagName === 'SELECT' ||
        target.isContentEditable
      ) {
        // Allow Escape and Ctrl+S even in inputs
        if (event.key !== 'Escape' && !(event.ctrlKey && event.key === 's')) {
          return
        }
      }

      // Find matching shortcut
      const matchingShortcut = shortcuts.find((shortcut) => {
        const keyMatch = event.key.toLowerCase() === shortcut.key.toLowerCase()
        const ctrlMatch = !!shortcut.ctrl === (event.ctrlKey || event.metaKey)
        const shiftMatch = !!shortcut.shift === event.shiftKey
        const altMatch = !!shortcut.alt === event.altKey

        return keyMatch && ctrlMatch && shiftMatch && altMatch
      })

      if (matchingShortcut) {
        if (preventDefault) {
          event.preventDefault()
        }
        matchingShortcut.action()
      }
    },
    [shortcuts, enabled, preventDefault],
  )

  useEffect(() => {
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [handleKeyDown])
}

/**
 * Format a shortcut for display.
 */
export function formatShortcut(shortcut: KeyboardShortcut): string {
  const parts: string[] = []

  if (shortcut.ctrl) {
    parts.push(navigator.platform.includes('Mac') ? '⌘' : 'Ctrl')
  }
  if (shortcut.shift) {
    parts.push('Shift')
  }
  if (shortcut.alt) {
    parts.push(navigator.platform.includes('Mac') ? '⌥' : 'Alt')
  }

  // Format special keys
  let keyDisplay = shortcut.key
  switch (shortcut.key.toLowerCase()) {
    case ' ':
      keyDisplay = 'Space'
      break
    case 'arrowleft':
      keyDisplay = '←'
      break
    case 'arrowright':
      keyDisplay = '→'
      break
    case 'arrowup':
      keyDisplay = '↑'
      break
    case 'arrowdown':
      keyDisplay = '↓'
      break
    case 'enter':
      keyDisplay = '↵'
      break
    case 'escape':
      keyDisplay = 'Esc'
      break
    default:
      keyDisplay = shortcut.key.toUpperCase()
  }

  parts.push(keyDisplay)
  return parts.join('+')
}

/**
 * Default shortcuts for the annotation workflow.
 */
export function useAnnotationShortcuts(actions: {
  markSuccess: () => void
  markPartial: () => void
  markFailure: () => void
  setRating: (rating: number) => void
  toggleJittery: () => void
  togglePlayback: () => void
  previousFrame: () => void
  nextFrame: () => void
  previousEpisode: () => void
  nextEpisode: () => void
  saveAndAdvance: () => void
  save: () => void
  showHelp: () => void
  insertFrame?: () => void
}) {
  const shortcuts: KeyboardShortcut[] = [
    // Annotation shortcuts
    {
      key: 's',
      action: actions.markSuccess,
      description: 'Mark as Success',
      category: 'annotation',
    },
    {
      key: 'p',
      action: actions.markPartial,
      description: 'Mark as Partial',
      category: 'annotation',
    },
    {
      key: 'f',
      action: actions.markFailure,
      description: 'Mark as Failure',
      category: 'annotation',
    },
    {
      key: 'j',
      action: actions.toggleJittery,
      description: 'Toggle Jittery Flag',
      category: 'annotation',
    },
    // Rating shortcuts
    {
      key: '1',
      action: () => actions.setRating(1),
      description: 'Rate 1 Star',
      category: 'annotation',
    },
    {
      key: '2',
      action: () => actions.setRating(2),
      description: 'Rate 2 Stars',
      category: 'annotation',
    },
    {
      key: '3',
      action: () => actions.setRating(3),
      description: 'Rate 3 Stars',
      category: 'annotation',
    },
    {
      key: '4',
      action: () => actions.setRating(4),
      description: 'Rate 4 Stars',
      category: 'annotation',
    },
    {
      key: '5',
      action: () => actions.setRating(5),
      description: 'Rate 5 Stars',
      category: 'annotation',
    },
    // Playback shortcuts
    {
      key: ' ',
      action: actions.togglePlayback,
      description: 'Play/Pause',
      category: 'playback',
    },
    {
      key: 'ArrowLeft',
      action: actions.previousFrame,
      description: 'Previous Frame',
      category: 'playback',
    },
    {
      key: 'ArrowRight',
      action: actions.nextFrame,
      description: 'Next Frame',
      category: 'playback',
    },
    // Navigation shortcuts
    {
      key: 'ArrowLeft',
      shift: true,
      action: actions.previousEpisode,
      description: 'Previous Episode',
      category: 'navigation',
    },
    {
      key: 'ArrowRight',
      shift: true,
      action: actions.nextEpisode,
      description: 'Next Episode',
      category: 'navigation',
    },
    // Workflow shortcuts
    {
      key: 'Enter',
      action: actions.saveAndAdvance,
      description: 'Save & Next',
      category: 'workflow',
    },
    {
      key: 's',
      ctrl: true,
      action: actions.save,
      description: 'Save Current',
      category: 'workflow',
    },
    {
      key: '?',
      action: actions.showHelp,
      description: 'Show Help',
      category: 'workflow',
    },
  ]

  // Add insert frame shortcut if action is provided
  if (actions.insertFrame) {
    shortcuts.push({
      key: 'i',
      action: actions.insertFrame,
      description: 'Insert Frame',
      category: 'annotation',
    })
  }

  useKeyboardShortcuts(shortcuts)

  return shortcuts
}
