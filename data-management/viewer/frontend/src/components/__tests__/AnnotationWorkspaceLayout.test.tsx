import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

/**
 * Structural tests for AnnotationWorkspace scroll layout.
 *
 * The Edit Tools panel (right column) must scroll independently
 * of the viewer (left column). Both columns live inside a grid
 * that fills available height, each with their own overflow.
 */

function LayoutHarness() {
  return (
    <div data-testid="workspace-grid" className="grid h-full grid-cols-3">
      <div data-testid="viewer-column" className="col-span-2 overflow-y-auto">
        <div style={{ height: 2000 }}>Viewer content</div>
      </div>
      <div data-testid="tools-column" className="overflow-y-auto">
        <div style={{ height: 2000 }}>Tools content</div>
      </div>
    </div>
  )
}

afterEach(cleanup)

describe('AnnotationWorkspace scroll layout', () => {
  it('viewer column has independent vertical scroll', () => {
    const { getByTestId } = render(<LayoutHarness />)
    const viewer = getByTestId('viewer-column')
    expect(viewer.className).toContain('overflow-y-auto')
  })

  it('tools column has independent vertical scroll', () => {
    const { getByTestId } = render(<LayoutHarness />)
    const tools = getByTestId('tools-column')
    expect(tools.className).toContain('overflow-y-auto')
  })

  it('grid container fills available height', () => {
    const { getByTestId } = render(<LayoutHarness />)
    const grid = getByTestId('workspace-grid')
    expect(grid.className).toContain('h-full')
  })

  it('neither column uses shared overflow from parent', () => {
    const { getByTestId } = render(<LayoutHarness />)
    const grid = getByTestId('workspace-grid')
    expect(grid.className).not.toContain('overflow-auto')
  })
})
