import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { HelpOverlay } from '@/components/HelpOverlay'
import type { KeyboardShortcut } from '@/hooks/use-keyboard-shortcuts'

const noop = () => {}

let originalPlatform: PropertyDescriptor | undefined

beforeEach(() => {
  originalPlatform = Object.getOwnPropertyDescriptor(Navigator.prototype, 'platform')
})

afterEach(() => {
  if (originalPlatform) {
    Object.defineProperty(Navigator.prototype, 'platform', originalPlatform)
  }
  vi.restoreAllMocks()
})

function setPlatform(value: string) {
  Object.defineProperty(Navigator.prototype, 'platform', {
    configurable: true,
    get: () => value,
  })
}

describe('HelpOverlay', () => {
  it('renders nothing when open is false', () => {
    const { container } = render(<HelpOverlay open={false} onClose={noop} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('renders the keyboard shortcuts card when open is true', () => {
    render(<HelpOverlay open onClose={noop} />)
    expect(screen.getByText('Keyboard Shortcuts')).toBeInTheDocument()
    expect(screen.getByText(/anytime to show this help/i)).toBeInTheDocument()
  })

  it('renders all four default category headings when no shortcuts are provided', () => {
    render(<HelpOverlay open onClose={noop} />)
    expect(screen.getByText('Annotation')).toBeInTheDocument()
    expect(screen.getByText('Playback')).toBeInTheDocument()
    expect(screen.getByText('Navigation')).toBeInTheDocument()
    expect(screen.getByText('Workflow')).toBeInTheDocument()
  })

  it('renders default shortcut descriptions when no shortcuts are provided', () => {
    render(<HelpOverlay open onClose={noop} />)
    expect(screen.getByText('Mark as Success')).toBeInTheDocument()
    expect(screen.getByText('Mark as Partial')).toBeInTheDocument()
    expect(screen.getByText('Mark as Failure')).toBeInTheDocument()
    expect(screen.getByText('Set Quality Rating')).toBeInTheDocument()
    expect(screen.getByText('Toggle Jittery Flag')).toBeInTheDocument()
    expect(screen.getByText('Play/Pause')).toBeInTheDocument()
    expect(screen.getByText('Previous Frame')).toBeInTheDocument()
    expect(screen.getByText('Next Frame')).toBeInTheDocument()
    expect(screen.getByText('Back 10 Frames')).toBeInTheDocument()
    expect(screen.getByText('Forward 10 Frames')).toBeInTheDocument()
    expect(screen.getByText('Previous Episode')).toBeInTheDocument()
    expect(screen.getByText('Next Episode')).toBeInTheDocument()
    expect(screen.getByText('Save & Next Episode')).toBeInTheDocument()
    expect(screen.getByText('Save Current')).toBeInTheDocument()
    expect(screen.getByText('Show This Help')).toBeInTheDocument()
    expect(screen.getByText('Close Dialog')).toBeInTheDocument()
  })

  it('renders default shortcut keys verbatim without formatShortcut', () => {
    render(<HelpOverlay open onClose={noop} />)
    expect(screen.getByText('Space')).toBeInTheDocument()
    expect(screen.getByText('Ctrl+S')).toBeInTheDocument()
    expect(screen.getByText('Shift+←')).toBeInTheDocument()
    expect(screen.getByText('↵ Enter')).toBeInTheDocument()
    expect(screen.getByText('Esc')).toBeInTheDocument()
    expect(screen.getByText('1-5')).toBeInTheDocument()
  })

  it('calls onClose when Escape is pressed on the window while open', () => {
    const onClose = vi.fn()
    render(<HelpOverlay open onClose={onClose} />)
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('does not call onClose for non-Escape window keydown events', () => {
    const onClose = vi.fn()
    render(<HelpOverlay open onClose={onClose} />)
    fireEvent.keyDown(window, { key: 'a' })
    fireEvent.keyDown(window, { key: 'Enter' })
    expect(onClose).not.toHaveBeenCalled()
  })

  it('does not register a window keydown listener when open is false', () => {
    const onClose = vi.fn()
    render(<HelpOverlay open={false} onClose={onClose} />)
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).not.toHaveBeenCalled()
  })

  it('removes the window keydown listener when open transitions to false', () => {
    const onClose = vi.fn()
    const { rerender } = render(<HelpOverlay open onClose={onClose} />)
    rerender(<HelpOverlay open={false} onClose={onClose} />)
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).not.toHaveBeenCalled()
  })

  it('calls onClose when the backdrop is clicked', async () => {
    const onClose = vi.fn()
    const user = userEvent.setup()
    render(<HelpOverlay open onClose={onClose} />)
    const backdrop = screen.getAllByRole('button')[0]
    await user.click(backdrop)
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('calls onClose when Escape is pressed on the backdrop', () => {
    const onClose = vi.fn()
    render(<HelpOverlay open onClose={onClose} />)
    const backdrop = screen.getAllByRole('button')[0]
    fireEvent.keyDown(backdrop, { key: 'Escape' })
    expect(onClose).toHaveBeenCalled()
  })

  it('calls onClose when Enter is pressed on the backdrop', () => {
    const onClose = vi.fn()
    render(<HelpOverlay open onClose={onClose} />)
    const backdrop = screen.getAllByRole('button')[0]
    fireEvent.keyDown(backdrop, { key: 'Enter' })
    expect(onClose).toHaveBeenCalled()
  })

  it('ignores other backdrop keydown keys', () => {
    const onClose = vi.fn()
    render(<HelpOverlay open onClose={onClose} />)
    const backdrop = screen.getAllByRole('button')[0]
    fireEvent.keyDown(backdrop, { key: 'a' })
    fireEvent.keyDown(backdrop, { key: 'Tab' })
    expect(onClose).not.toHaveBeenCalled()
  })

  it('does not call onClose when clicking inside the card body (stopPropagation)', async () => {
    const onClose = vi.fn()
    const user = userEvent.setup()
    render(<HelpOverlay open onClose={onClose} />)
    await user.click(screen.getByText('Keyboard Shortcuts'))
    expect(onClose).not.toHaveBeenCalled()
  })

  it('calls onClose when the close X button is clicked', async () => {
    const onClose = vi.fn()
    const user = userEvent.setup()
    render(<HelpOverlay open onClose={onClose} />)
    const closeButton = screen.getAllByRole('button').find((btn) => btn.tagName === 'BUTTON')
    expect(closeButton).toBeDefined()
    await user.click(closeButton!)
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('renders custom shortcuts grouped by category and formatted via formatShortcut', () => {
    setPlatform('Linux x86_64')
    const shortcuts: KeyboardShortcut[] = [
      { key: 'q', action: noop, description: 'Custom Annotate', category: 'annotation' },
      { key: 'arrowleft', action: noop, description: 'Custom Prev Frame', category: 'playback' },
      {
        key: 'n',
        shift: true,
        action: noop,
        description: 'Custom Next Episode',
        category: 'navigation',
      },
      { key: 'r', alt: true, action: noop, description: 'Custom Reset', category: 'workflow' },
    ]
    render(<HelpOverlay open onClose={noop} shortcuts={shortcuts} />)

    expect(screen.getByText('Custom Annotate')).toBeInTheDocument()
    expect(screen.getByText('Custom Prev Frame')).toBeInTheDocument()
    expect(screen.getByText('Custom Next Episode')).toBeInTheDocument()
    expect(screen.getByText('Custom Reset')).toBeInTheDocument()

    expect(screen.getByText('Q')).toBeInTheDocument()
    expect(screen.getByText('←')).toBeInTheDocument()
    expect(screen.getByText('Shift+N')).toBeInTheDocument()
    expect(screen.getByText('Alt+R')).toBeInTheDocument()
  })

  it('returns null for categories that have no matching custom shortcuts', () => {
    const shortcuts: KeyboardShortcut[] = [
      { key: 'q', action: noop, description: 'Only Annotation', category: 'annotation' },
    ]
    render(<HelpOverlay open onClose={noop} shortcuts={shortcuts} />)

    expect(screen.getByText('Annotation')).toBeInTheDocument()
    expect(screen.getByText('Only Annotation')).toBeInTheDocument()
    expect(screen.queryByText('Playback')).not.toBeInTheDocument()
    expect(screen.queryByText('Navigation')).not.toBeInTheDocument()
    expect(screen.queryByText('Workflow')).not.toBeInTheDocument()
    expect(screen.queryByText('Mark as Success')).not.toBeInTheDocument()
    expect(screen.queryByText('Play/Pause')).not.toBeInTheDocument()
  })

  it('formats Ctrl modifier as ⌘ when navigator.platform contains Mac', () => {
    setPlatform('MacIntel')
    const shortcuts: KeyboardShortcut[] = [
      { key: 's', ctrl: true, action: noop, description: 'Mac Save', category: 'workflow' },
    ]
    render(<HelpOverlay open onClose={noop} shortcuts={shortcuts} />)
    expect(screen.getByText('⌘+S')).toBeInTheDocument()
  })

  it('formats Alt modifier as ⌥ when navigator.platform contains Mac', () => {
    setPlatform('MacIntel')
    const shortcuts: KeyboardShortcut[] = [
      { key: 's', alt: true, action: noop, description: 'Mac Alt Save', category: 'workflow' },
    ]
    render(<HelpOverlay open onClose={noop} shortcuts={shortcuts} />)
    expect(screen.getByText('⌥+S')).toBeInTheDocument()
  })

  it('formats space, arrow keys, enter, and escape via formatShortcut special cases', () => {
    setPlatform('Linux x86_64')
    const shortcuts: KeyboardShortcut[] = [
      { key: ' ', action: noop, description: 'Space Action', category: 'annotation' },
      { key: 'arrowright', action: noop, description: 'Right Action', category: 'annotation' },
      { key: 'arrowup', action: noop, description: 'Up Action', category: 'annotation' },
      { key: 'arrowdown', action: noop, description: 'Down Action', category: 'annotation' },
      { key: 'enter', action: noop, description: 'Enter Action', category: 'workflow' },
      { key: 'escape', action: noop, description: 'Escape Action', category: 'workflow' },
    ]
    render(<HelpOverlay open onClose={noop} shortcuts={shortcuts} />)
    expect(screen.getByText('Space')).toBeInTheDocument()
    expect(screen.getByText('→')).toBeInTheDocument()
    expect(screen.getByText('↑')).toBeInTheDocument()
    expect(screen.getByText('↓')).toBeInTheDocument()
    expect(screen.getByText('↵')).toBeInTheDocument()
    expect(screen.getByText('Esc')).toBeInTheDocument()
  })
})
