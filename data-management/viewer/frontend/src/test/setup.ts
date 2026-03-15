import '@testing-library/jest-dom/vitest'

import { vi } from 'vitest'

class ResizeObserverMock {
  observe() {}
  unobserve() {}
  disconnect() {}
}

vi.stubGlobal('ResizeObserver', ResizeObserverMock)

Element.prototype.scrollIntoView = vi.fn()
