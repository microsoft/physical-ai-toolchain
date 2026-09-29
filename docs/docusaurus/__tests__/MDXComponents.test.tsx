import React from 'react'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { axe } from 'jest-axe'

const mockSearchBar = {
  variant: 'single' as 'single' | 'preconfigured' | 'preAttach' | 'empty',
}

jest.mock('@site/src/components/BoxCard', () => () => null, { virtual: true })
jest.mock('@site/src/components/CardGrid', () => () => null, { virtual: true })
jest.mock('@site/src/components/IconCard', () => () => null, { virtual: true })
jest.mock(
  '@theme-original/SearchBar',
  () => ({
    __esModule: true,
    default: () => {
      if (mockSearchBar.variant === 'empty') {
        return null
      }

      const preAttach = mockSearchBar.variant === 'preAttach'
      const preconfigured = mockSearchBar.variant === 'preconfigured'
      return (
        <>
          <input
            className="navbar__search-input"
            aria-owns={preAttach ? undefined : 'search-results'}
            aria-autocomplete={preAttach ? undefined : 'list'}
            aria-expanded={preAttach ? undefined : 'true'}
            aria-controls={preconfigured ? 'search-results' : undefined}
            aria-label={preconfigured ? 'Search documentation' : undefined}
          />
          {!preAttach && (
            <div id="search-results" role="listbox">
              <div id="search-result-1" role="option" aria-selected="false">
                Training
              </div>
              {preconfigured && (
                <div id="search-result-2" className="cursor_mock" role="option" aria-selected="false">
                  Evaluation
                </div>
              )}
              <div className="hitFooter_mock"><a href="/search/">See all results</a></div>
            </div>
          )}
          {!preconfigured && !preAttach && <button type="reset">Reset</button>}
        </>
      )
    },
  }),
  { virtual: true },
)

import MDXComponents from '../src/theme/MDXComponents'
import SearchBar from '../src/theme/SearchBar'

describe('MDXComponents', () => {
  it('names disabled task controls from their task text while preserving native state', () => {
    const TaskInput = MDXComponents.input
    const TaskListItem = MDXComponents.li

    render(
      <ul className="contains-task-list">
        <TaskListItem className="task-list-item">
          <TaskInput type="checkbox" disabled /> Configure cluster access
        </TaskListItem>
        <TaskListItem className="task-list-item">
          <TaskInput type="checkbox" disabled checked readOnly /> Validate deployment
        </TaskListItem>
      </ul>,
    )

    expect(screen.getByRole('checkbox', { name: 'Configure cluster access' })).toBeDisabled()
    expect(screen.getByRole('checkbox', { name: 'Configure cluster access' })).not.toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'Validate deployment' })).toBeDisabled()
    expect(screen.getByRole('checkbox', { name: 'Validate deployment' })).toBeChecked()
  })

  it('names scrollable tables from their captions', () => {
    const Table = MDXComponents.table

    render(
      <Table>
        <caption>Training metrics</caption>
        <tbody>
          <tr>
            <td>Loss</td>
          </tr>
        </tbody>
      </Table>,
    )

    expect(screen.getByRole('group', { name: 'Training metrics, scrollable table' })).toHaveAttribute(
      'tabindex',
      '0',
    )
    expect(screen.getByRole('table', { name: 'Training metrics' })).toBeInTheDocument()
  })

  it('preserves heading labels on scrollable table groups', () => {
    const Table = MDXComponents.table

    render(
      <>
        <h2 id="metrics-heading">Evaluation metrics</h2>
        <Table aria-labelledby="metrics-heading">
          <tbody><tr><td>Accuracy</td></tr></tbody>
        </Table>
      </>,
    )

    expect(screen.getByRole('group', { name: 'Evaluation metrics' })).toHaveAttribute(
      'aria-labelledby',
      'metrics-heading',
    )
    expect(screen.getByRole('table', { name: 'Evaluation metrics' })).toBeInTheDocument()
  })

  it('leaves ordinary inputs and list items unchanged', () => {
    const Input = MDXComponents.input
    const ListItem = MDXComponents.li
    const { container } = render(
      <ul>
        <ListItem>
          <Input type="text" /> Plain item
        </ListItem>
      </ul>,
    )

    expect(container.querySelector('li > input')).not.toHaveAttribute('aria-labelledby')
  })
})

describe('SearchBar theme ownership', () => {
  beforeAll(() => {
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: jest.fn(),
    })
  })

  afterEach(() => {
    mockSearchBar.variant = 'single'
    jest.useRealTimers()
  })

  it('links the attached combobox to normalized results and announces after settlement', async () => {
    jest.useFakeTimers()
    const { container, unmount } = render(<SearchBar />)
    const input = screen.getByLabelText('Search documentation')

    expect(input).toHaveAttribute('role', 'combobox')
    expect(input).toHaveAttribute('aria-controls', 'search-results')
    expect(input).toHaveAccessibleDescription('Keyboard shortcut: Control plus K')
    expect(screen.getByRole('button', { name: 'Clear search' })).toBeInTheDocument()
    const footer = screen.getByRole('option', { name: 'See all results' })
    expect(footer).toHaveAttribute('aria-posinset', '2')
    expect(footer).toHaveAttribute('aria-setsize', '2')

    input.setAttribute('aria-expanded', 'true')
    fireEvent.input(input, { target: { value: 'training' } })
    act(() => jest.advanceTimersByTime(460))
    expect(screen.getByRole('status')).toHaveTextContent('1 result for training')
    jest.useRealTimers()
    expect(await axe(container)).toHaveNoViolations()

    unmount()
  })

  it('preserves configured attributes and announces plural and empty results', () => {
    mockSearchBar.variant = 'preconfigured'
    jest.useFakeTimers()
    render(<SearchBar />)
    const input = screen.getByLabelText('Search documentation')

    input.setAttribute('aria-expanded', 'true')
    fireEvent.input(input, { target: { value: 'training' } })
    act(() => jest.advanceTimersByTime(460))
    expect(screen.getByRole('status')).toHaveTextContent('2 results for training')

    fireEvent.input(input, { target: { value: '   ' } })
    act(() => jest.advanceTimersByTime(460))
    expect(screen.getByRole('status')).toBeEmptyDOMElement()
    expect(screen.queryByRole('button', { name: 'Clear search' })).not.toBeInTheDocument()
  })

  it('preserves native semantics before attachment and clears stale state after destroy', async () => {
    mockSearchBar.variant = 'preAttach'
    const { rerender } = render(<SearchBar />)
    const input = screen.getByRole('textbox', { name: 'Search documentation' })

    expect(input).not.toHaveAttribute('role')
    expect(input).not.toHaveAttribute('aria-expanded')
    expect(input).not.toHaveAttribute('aria-controls')

    mockSearchBar.variant = 'single'
    rerender(<SearchBar />)
    await act(async () => undefined)
    expect(input).toHaveAttribute('role', 'combobox')
    expect(input).toHaveAttribute('aria-controls', 'search-results')

    mockSearchBar.variant = 'preAttach'
    rerender(<SearchBar />)
    await act(async () => undefined)
    expect(input).not.toHaveAttribute('role')
    expect(input).not.toHaveAttribute('aria-expanded')
    expect(input).not.toHaveAttribute('aria-controls')
  })

  it('isolates multiple instances and restores patched methods on unmount', () => {
    const { unmount } = render(<><SearchBar /><SearchBar /></>)
    const inputs = screen.getAllByLabelText('Search documentation')
    const descriptionIds = inputs.map((input) => input.getAttribute('aria-describedby'))

    expect(new Set(descriptionIds).size).toBe(2)
    expect(screen.getAllByRole('status')).toHaveLength(2)
    expect(Object.hasOwn(inputs[0], 'focus')).toBe(true)
    expect(Object.hasOwn(inputs[0], 'blur')).toBe(true)

    unmount()
    expect(Object.hasOwn(inputs[0], 'focus')).toBe(false)
    expect(Object.hasOwn(inputs[0], 'blur')).toBe(false)
  })

  it('moves the active descendant through the footer and activates or clears it', () => {
    mockSearchBar.variant = 'preconfigured'
    render(<SearchBar />)
    const input = screen.getByLabelText('Search documentation')
    const lastResult = screen.getByRole('option', { name: 'Evaluation' })
    const footer = screen.getByRole('option', { name: 'See all results' })

    input.setAttribute('aria-activedescendant', lastResult.id)
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    expect(input).toHaveAttribute('aria-activedescendant', footer.id)
    expect(footer).toHaveAttribute('aria-selected', 'true')
    expect(footer).toHaveClass('search-footer-active')

    fireEvent.keyDown(input, { key: 'ArrowUp' })
    expect(input).toHaveAttribute('aria-activedescendant', lastResult.id)
    expect(lastResult).toHaveAttribute('aria-selected', 'true')

    fireEvent.keyDown(input, { key: 'End' })
    const click = jest.spyOn(footer, 'click').mockImplementation()
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(click).toHaveBeenCalledTimes(1)

    fireEvent.keyDown(input, { key: 'Escape' })
    expect(input).not.toHaveAttribute('aria-activedescendant')
    expect(footer).toHaveAttribute('aria-selected', 'false')
  })

  it('moves Tab and Shift+Tab to real sequential focus targets', () => {
    const rectangles = jest
      .spyOn(HTMLElement.prototype, 'getClientRects')
      .mockReturnValue({ length: 1 } as DOMRectList)
    render(<><a href="/before">Before</a><SearchBar /></>)
    const input = screen.getByLabelText('Search documentation')
    const before = screen.getByRole('link', { name: 'Before' })
    const clear = screen.getByRole('button', { name: 'Clear search' })

    HTMLElement.prototype.focus.call(input)
    fireEvent.keyDown(input, { key: 'Tab' })
    expect(clear).toHaveFocus()

    HTMLElement.prototype.focus.call(input)
    fireEvent.keyDown(input, { key: 'Tab', shiftKey: true })
    expect(before).toHaveFocus()
    rectangles.mockRestore()
  })

  it('suppresses asynchronous refocus while preserving gesture focus and safe blur', () => {
    render(<><SearchBar /><button type="button">After</button></>)
    const input = screen.getByLabelText('Search documentation')
    const after = screen.getByRole('button', { name: 'After' })

    after.focus()
    input.focus()
    expect(after).toHaveFocus()

    const focusDuringGesture = () => input.focus()
    document.addEventListener('keydown', focusDuringGesture, { once: true })
    after.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', bubbles: true }))
    expect(input).toHaveFocus()

    input.blur()
    expect(input).not.toHaveFocus()
    after.focus()
    input.blur()
    expect(after).toHaveFocus()
  })

  it('clears announcements and active state when the popup closes', async () => {
    jest.useFakeTimers()
    render(<SearchBar />)
    const input = screen.getByLabelText('Search documentation')

    input.setAttribute('aria-expanded', 'true')
    fireEvent.input(input, { target: { value: 'training' } })
    act(() => jest.advanceTimersByTime(460))
    expect(screen.getByRole('status')).toHaveTextContent('1 result for training')

    input.setAttribute('aria-activedescendant', 'search-result-1')
    input.setAttribute('aria-expanded', 'false')
    fireEvent.keyDown(input, { key: 'Escape' })
    await act(async () => undefined)
    expect(screen.getByRole('status')).toBeEmptyDOMElement()
    expect(input).not.toHaveAttribute('aria-activedescendant')
  })

  it('tolerates an original search component without an input', () => {
    mockSearchBar.variant = 'empty'
    render(<SearchBar />)

    expect(screen.getByRole('status')).toBeEmptyDOMElement()
  })
})