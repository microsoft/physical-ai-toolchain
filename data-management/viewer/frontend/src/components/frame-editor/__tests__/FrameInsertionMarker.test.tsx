import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { FrameInsertionMarker } from '../FrameInsertionMarker'

describe('FrameInsertionMarker', () => {
  it('renders insert button title when not inserted', () => {
    render(
      <FrameInsertionMarker
        afterFrameIndex={5}
        isInserted={false}
        onClick={vi.fn()}
        position={50}
      />,
    )
    expect(screen.getByTitle('Insert frame here')).toBeInTheDocument()
  })

  it('renders remove button title when inserted', () => {
    render(
      <FrameInsertionMarker
        afterFrameIndex={5}
        isInserted={true}
        onClick={vi.fn()}
        onRemove={vi.fn()}
        position={50}
      />,
    )
    expect(screen.getByTitle('Remove inserted frame')).toBeInTheDocument()
  })

  it('calls onClick when not inserted', () => {
    const onClick = vi.fn()
    render(
      <FrameInsertionMarker
        afterFrameIndex={5}
        isInserted={false}
        onClick={onClick}
        position={50}
      />,
    )
    fireEvent.click(screen.getByTitle('Insert frame here'))
    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it('calls onRemove when inserted with onRemove handler', () => {
    const onClick = vi.fn()
    const onRemove = vi.fn()
    render(
      <FrameInsertionMarker
        afterFrameIndex={5}
        isInserted={true}
        onClick={onClick}
        onRemove={onRemove}
        position={50}
      />,
    )
    fireEvent.click(screen.getByTitle('Remove inserted frame'))
    expect(onRemove).toHaveBeenCalledTimes(1)
    expect(onClick).not.toHaveBeenCalled()
  })

  it('falls back to onClick when inserted without onRemove handler', () => {
    const onClick = vi.fn()
    render(
      <FrameInsertionMarker
        afterFrameIndex={5}
        isInserted={true}
        onClick={onClick}
        position={50}
      />,
    )
    fireEvent.click(screen.getByTitle('Remove inserted frame'))
    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it('applies position style and data attribute', () => {
    const { container } = render(
      <FrameInsertionMarker
        afterFrameIndex={7}
        isInserted={false}
        onClick={vi.fn()}
        position={42}
      />,
    )
    const marker = container.querySelector('[data-frame-index="7"]') as HTMLElement
    expect(marker).not.toBeNull()
    expect(marker.style.left).toBe('42%')
  })

  it('stops event propagation on click', () => {
    const onClick = vi.fn()
    const parentClick = vi.fn()
    render(
      <div onClick={parentClick} onKeyDown={parentClick} role="presentation">
        <FrameInsertionMarker
          afterFrameIndex={5}
          isInserted={false}
          onClick={onClick}
          position={50}
        />
      </div>,
    )
    fireEvent.click(screen.getByTitle('Insert frame here'))
    expect(onClick).toHaveBeenCalledTimes(1)
    expect(parentClick).not.toHaveBeenCalled()
  })
})
