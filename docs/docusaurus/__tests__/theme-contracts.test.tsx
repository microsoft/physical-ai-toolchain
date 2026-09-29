import React from 'react'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { axe } from 'jest-axe'

let mockLocation = { pathname: '/', hash: '' }
let mockHistory = { action: 'POP' }
let mockLayoutHasMain = true
let mockCollapsed = true
const mockToggleCollapsed = jest.fn()
let mockNavigationExpanded = false
let mockNavbarHasSidebar = true
const mockNavbarToggle = jest.fn()
let mockBreadcrumbs = [
  { label: 'Getting started', type: 'category', href: '/getting-started/' },
  { label: 'Quickstart', type: 'doc', href: '/getting-started/quickstart/' },
]
let mockSearchPage = { query: 'training', resultCount: 2, settled: true }
const mockFooter = {
  style: 'dark',
  links: [
    { title: 'Docs', items: [{ label: 'Quickstart', to: '/getting-started/' }] },
    { title: 'Community', items: [{ label: 'Issues', href: 'https://example.test/issues' }] },
  ],
  copyright: 'Copyright Microsoft',
}
let mockFooterConfig: unknown = mockFooter

jest.mock('@docusaurus/router', () => ({
  useHistory: () => mockHistory,
  useLocation: () => mockLocation,
}), { virtual: true })

jest.mock('@docusaurus/theme-common', () => ({
  Collapsible: ({ children, className, collapsed }: React.PropsWithChildren<{ className?: string; collapsed: boolean }>) => (
    <div className={className} data-collapsed={String(collapsed)}>{children}</div>
  ),
  ThemeClassNames: { docs: { docBreadcrumbs: 'doc-breadcrumbs' } },
  isMultiColumnFooterLinks: (links: Array<{ items?: unknown }>) => Boolean(links[0]?.items),
  useCollapsible: () => ({ collapsed: mockCollapsed, toggleCollapsed: mockToggleCollapsed }),
  useThemeConfig: () => ({ footer: mockFooterConfig }),
}))

jest.mock('@docusaurus/theme-common/internal', () => ({
  useHomePageRoute: () => ({ path: '/' }),
}))

jest.mock('@docusaurus/plugin-content-docs/client', () => ({
  useSidebarBreadcrumbs: () => mockBreadcrumbs,
}))

jest.mock('@docusaurus/Translate', () => ({
  translate: ({ message }: { message: string }) => message,
}), { virtual: true })

jest.mock('@theme-original/Layout', () => ({
  __esModule: true,
  default: () => mockLayoutHasMain
    ? <main tabIndex={-1}><a href="#content">Content link</a></main>
    : <div id="__docusaurus_skipToContent_fallback" />,
}), { virtual: true })

jest.mock('@theme-original/Navbar', () => ({
  __esModule: true,
  default: () => (
    <nav aria-label="Primary">
      <button
        type="button"
        className="navbar__toggle"
        aria-expanded={String(mockNavigationExpanded)}
        onClick={mockNavbarToggle}
      >
        Toggle navigation
      </button>
      {mockNavbarHasSidebar && (
        <div className="navbar-sidebar">
          <a href="/first">First navigation item</a>
          <button type="button">Last navigation item</button>
        </div>
      )}
    </nav>
  ),
}), { virtual: true })

jest.mock('@theme-original/Footer/LinkItem', () => ({
  __esModule: true,
  default: ({ item }: { item: { href?: string; label?: string; to?: string } }) => (
    <a href={item.href ?? item.to}>{item.label}</a>
  ),
}), { virtual: true })

jest.mock('@theme-original/Footer/Links', () => ({
  __esModule: true,
  default: () => <div>Simple links</div>,
}), { virtual: true })

jest.mock('@theme-original/Footer/Logo', () => ({
  __esModule: true,
  default: () => <div>Logo</div>,
}), { virtual: true })

jest.mock('@theme-original/Footer/Copyright', () => ({
  __esModule: true,
  default: ({ copyright }: { copyright: string }) => <div>{copyright}</div>,
}), { virtual: true })

jest.mock('@theme-original/Footer/Layout', () => ({
  __esModule: true,
  default: ({ links, logo, copyright }: React.PropsWithChildren<{ links?: React.ReactNode; logo?: React.ReactNode; copyright?: React.ReactNode }>) => (
    <footer>{links}{logo}{copyright}</footer>
  ),
}), { virtual: true })

jest.mock('@theme-original/DocBreadcrumbs/Items/Home', () => ({
  __esModule: true,
  default: () => <li><a href="/">Home</a></li>,
}), { virtual: true })

jest.mock('@theme-original/DocBreadcrumbs/StructuredData', () => ({
  __esModule: true,
  default: () => null,
}), { virtual: true })

jest.mock('@theme-original/TOCItems', () => ({
  __esModule: true,
  default: () => <ul><li><a href="#section">Section</a></li></ul>,
}), { virtual: true })

jest.mock('@theme-original/TOCCollapsible/CollapseButton', () => ({
  __esModule: true,
  default: ({ collapsed: _collapsed, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement> & { collapsed: boolean }) => (
    <button type="button" {...props}>On this page</button>
  ),
}), { virtual: true })

jest.mock('@theme-original/SearchPage', () => ({
  __esModule: true,
  default: () => (
    <div className="container">
      <h1>Search</h1>
      <input
        name="q"
        defaultValue={mockSearchPage.query}
        ref={(input) => {
          input?.setAttribute('autofocus', '')
          input?.focus()
        }}
      />
      {mockSearchPage.settled && <p>Search complete</p>}
      {Array.from({ length: mockSearchPage.resultCount }, (_, index) => <article key={index}>Result {index + 1}</article>)}
    </div>
  ),
}), { virtual: true })

import DocBreadcrumbs from '../src/theme/DocBreadcrumbs'
import Footer from '../src/theme/Footer'
import Layout from '../src/theme/Layout'
import Navbar from '../src/theme/Navbar'
import SearchPage from '../src/theme/SearchPage'
import TOCCollapsible from '../src/theme/TOCCollapsible'

describe('semantic theme contracts', () => {
  beforeEach(() => {
    mockLocation = { pathname: '/', hash: '' }
    mockHistory = { action: 'POP' }
    mockLayoutHasMain = true
    mockCollapsed = true
    mockNavigationExpanded = false
    mockNavbarHasSidebar = true
    mockNavbarToggle.mockReset()
    mockBreadcrumbs = [
      { label: 'Getting started', type: 'category', href: '/getting-started/' },
      { label: 'Quickstart', type: 'doc', href: '/getting-started/quickstart/' },
    ]
    mockSearchPage = { query: 'training', resultCount: 2, settled: true }
    mockFooterConfig = mockFooter
    jest.useRealTimers()
  })

  it('renders one banner and a semantically grouped footer', async () => {
    const { container } = render(<><Navbar /><Footer /></>)

    expect(screen.getByRole('banner')).toHaveAccessibleName('Physical AI Toolchain documentation header')
    expect(screen.getByRole('heading', { level: 2, name: 'Site footer' })).toBeInTheDocument()
    for (const name of ['Docs', 'Community']) {
      const heading = screen.getByRole('heading', { level: 3, name })
      expect(screen.getByRole('list', { name })).toHaveAttribute('aria-labelledby', heading.id)
    }
    expect(await axe(container)).toHaveNoViolations()
  })

  it('contains mobile navigation focus and restores the toggle on dismissal', async () => {
    const getClientRects = jest
      .spyOn(HTMLElement.prototype, 'getClientRects')
      .mockReturnValue([{} as DOMRect])
    const requestAnimationFrame = jest
      .spyOn(window, 'requestAnimationFrame')
      .mockImplementation((callback) => {
        callback(0)
        return 1
      })
    const { rerender, unmount } = render(
      <>
        <main>Main content</main>
        <Navbar />
        <footer>Footer content</footer>
      </>,
    )
    const main = screen.getByRole('main')
    const footer = screen.getByText('Footer content')
    const toggle = screen.getByRole('button', { name: 'Toggle navigation' })

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(mockNavbarToggle).not.toHaveBeenCalled()

    mockNavigationExpanded = true
    rerender(
      <>
        <main>Main content</main>
        <Navbar />
        <footer>Footer content</footer>
      </>,
    )
    await waitFor(() => {
      expect(main).toHaveAttribute('inert')
      expect(footer).toHaveAttribute('inert')
    })

    const first = screen.getByRole('link', { name: 'First navigation item' })
    const last = screen.getByRole('button', { name: 'Last navigation item' })
    last.focus()
    fireEvent.keyDown(document, { key: 'Tab' })
    expect(first).toHaveFocus()

    first.focus()
    fireEvent.keyDown(document, { key: 'Tab', shiftKey: true })
    expect(last).toHaveFocus()

    toggle.focus()
    fireEvent.keyDown(document, { key: 'Tab' })
    expect(first).toHaveFocus()

    fireEvent.keyDown(document, { key: 'ArrowRight' })
    expect(first).toHaveFocus()

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(mockNavbarToggle).toHaveBeenCalledTimes(1)
    expect(toggle).toHaveFocus()

    unmount()
    expect(main).not.toHaveAttribute('inert')
    expect(footer).not.toHaveAttribute('inert')
    getClientRects.mockRestore()
    requestAnimationFrame.mockRestore()
  })

  it('supports absent and simple footer configurations', () => {
    mockFooterConfig = null
    const { container, unmount } = render(<Footer />)
    expect(container).toBeEmptyDOMElement()
    unmount()

    mockFooterConfig = {
      style: 'dark',
      links: [{ label: 'Privacy', to: '/privacy/' }],
    }
    render(<Footer />)
    expect(screen.getByText('Simple links')).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: 'Site footer' })).toBeInTheDocument()
  })

  it('renders one non-link current breadcrumb inside named navigation', () => {
    render(<DocBreadcrumbs />)

    const navigation = screen.getByRole('navigation', { name: 'Breadcrumbs' })
    expect(navigation.querySelectorAll('[aria-current="page"]')).toHaveLength(1)
    expect(navigation.querySelector('[aria-current="page"]')).not.toHaveAttribute('href')
  })

  it('suppresses unlisted breadcrumb links and renders nothing without breadcrumbs', () => {
    mockBreadcrumbs = [
      { label: 'Hidden category', type: 'category', href: '/hidden/', linkUnlisted: true } as never,
      { label: 'Current page', type: 'doc', href: '/current/' },
    ]
    const { rerender } = render(<DocBreadcrumbs />)
    expect(screen.getByText('Hidden category')).not.toHaveAttribute('href')

    mockBreadcrumbs = null as never
    rerender(<DocBreadcrumbs />)
    expect(screen.queryByRole('navigation', { name: 'Breadcrumbs' })).not.toBeInTheDocument()
  })

  it('keeps the mobile TOC region mounted and controlled without a leading heading', () => {
    render(<TOCCollapsible toc={[{ value: 'Section', id: 'section', level: 2 }]} />)

    const button = screen.getByRole('button', { name: 'On this page' })
    const region = screen.getByRole('region', { name: 'On this page' })
    expect(button).toHaveAttribute('aria-controls', region.id)
    expect(button).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('heading')).not.toBeInTheDocument()
  })

  it('synchronizes the expanded mobile TOC control', () => {
    mockCollapsed = false
    render(<TOCCollapsible toc={[{ value: 'Section', id: 'section', level: 2 }]} />)

    const button = screen.getByRole('button', { name: 'On this page' })
    expect(button).toHaveAttribute('aria-expanded', 'true')
    fireEvent.click(button)
    expect(mockToggleCollapsed).toHaveBeenCalled()
  })

  it('focuses main only after pathname-changing PUSH navigation without a hash', () => {
    const requestAnimationFrame = jest.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
      callback(0)
      return 1
    })
    const { rerender } = render(<Layout />)
    expect(screen.getByRole('main')).not.toHaveFocus()

    mockHistory = { action: 'PUSH' }
    mockLocation = { pathname: '/getting-started/', hash: '' }
    rerender(<Layout />)
    expect(screen.getByRole('main')).toHaveFocus()

    const contentLink = screen.getByRole('link', { name: 'Content link' })
    contentLink.focus()
    mockLocation = { pathname: '/getting-started/quickstart/', hash: '#content' }
    rerender(<Layout />)
    expect(contentLink).toHaveFocus()
    requestAnimationFrame.mockRestore()
  })

  it('promotes and cleans up the search fallback only when no main exists', () => {
    mockLayoutHasMain = false
    mockLocation = { pathname: '/search/', hash: '' }
    const { unmount } = render(<Layout />)
    const fallback = document.getElementById('__docusaurus_skipToContent_fallback')

    expect(fallback).toHaveAttribute('role', 'main')
    unmount()
    expect(fallback).not.toHaveAttribute('role')
  })
})

describe('SearchPage theme ownership', () => {
  beforeEach(() => {
    jest.useFakeTimers()
  })

  afterEach(() => {
    jest.useRealTimers()
  })

  it('removes autofocus, restores the initial focus start, and announces settled results', () => {
    render(<SearchPage />)
    const input = screen.getByRole('textbox')

    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Search')
    expect(input).not.toHaveAttribute('autofocus')
    expect(document.body).toHaveFocus()
    act(() => jest.advanceTimersByTime(210))
    expect(screen.getByRole('status')).toHaveTextContent('2 documents found')
  })

  it('waits for rendered settlement before announcing zero results', () => {
    mockSearchPage = { query: 'absent', resultCount: 0, settled: false }
    const { rerender } = render(<SearchPage />)

    act(() => jest.advanceTimersByTime(2000))
    expect(screen.getByRole('status')).toBeEmptyDOMElement()

    mockSearchPage = { query: 'absent', resultCount: 0, settled: true }
    rerender(<SearchPage />)
    fireEvent.input(screen.getByRole('textbox'))
    act(() => jest.advanceTimersByTime(1710))
    expect(screen.getByRole('status')).toHaveTextContent('No documents found')
  })

  it('keeps the status empty for an empty query', () => {
    mockSearchPage = { query: '', resultCount: 0, settled: true }
    render(<SearchPage />)

    act(() => jest.advanceTimersByTime(2000))
    expect(screen.getByRole('status')).toBeEmptyDOMElement()
  })
})
