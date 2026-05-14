import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { OfflineIndicator } from '@/components/offline/OfflineIndicator'
import {
  useOfflineAnnotations,
  type UseOfflineAnnotationsResult,
} from '@/hooks/use-offline-annotations'

vi.mock('@/hooks/use-offline-annotations', () => ({
  useOfflineAnnotations: vi.fn(),
}))

vi.mock('@/components/ui/popover', () => ({
  Popover: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  PopoverTrigger: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  PopoverContent: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="popover-content">{children}</div>
  ),
}))

const mockedHook = vi.mocked(useOfflineAnnotations)

const baseHookState: UseOfflineAnnotationsResult = {
  isOnline: true,
  pendingCount: 0,
  isSyncing: false,
  lastSyncResult: null,
  sync: vi.fn(),
  saveLocal: vi.fn(),
  getLocal: vi.fn(),
  getPending: vi.fn(),
  deleteLocal: vi.fn(),
  startSync: vi.fn(),
  stopSync: vi.fn(),
}

function setHookState(overrides: Partial<typeof baseHookState> = {}) {
  mockedHook.mockReturnValue({ ...baseHookState, ...overrides })
}

describe('OfflineIndicator', () => {
  beforeEach(() => {
    setHookState()
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  describe('trigger states', () => {
    it('shows Offline state when not online', () => {
      setHookState({ isOnline: false })
      render(<OfflineIndicator />)
      expect(screen.getAllByText('Offline').length).toBeGreaterThanOrEqual(1)
    })

    it('shows Syncing... state when isSyncing', () => {
      setHookState({ isSyncing: true })
      render(<OfflineIndicator />)
      // Two "Syncing..." spans: trigger label and button label
      expect(screen.getAllByText('Syncing...').length).toBeGreaterThanOrEqual(1)
    })

    it('shows pending badge with count when pendingCount > 0', () => {
      setHookState({ pendingCount: 5 })
      render(<OfflineIndicator />)
      expect(screen.getAllByText('5').length).toBeGreaterThanOrEqual(1)
    })

    it('shows Sync errors state when lastSyncResult has failures', () => {
      setHookState({
        lastSyncResult: { success: false, syncedCount: 1, failedCount: 2, errors: [] },
      })
      render(<OfflineIndicator />)
      expect(screen.getByText('Sync errors')).toBeInTheDocument()
    })

    it('shows clean state when online, idle, no pending, no errors', () => {
      render(<OfflineIndicator />)
      expect(screen.queryByText('Offline')).not.toBeInTheDocument()
      expect(screen.queryByText('Sync errors')).not.toBeInTheDocument()
    })
  })

  describe('popover content', () => {
    it('renders Online connection status', () => {
      render(<OfflineIndicator />)
      expect(screen.getByText('Online')).toBeInTheDocument()
    })

    it('renders Offline connection status when offline', () => {
      setHookState({ isOnline: false })
      render(<OfflineIndicator />)
      // Multiple "Offline" occurrences (trigger + status badge)
      expect(screen.getAllByText('Offline').length).toBeGreaterThanOrEqual(1)
    })

    it('shows pending count in popover', () => {
      setHookState({ pendingCount: 3 })
      render(<OfflineIndicator />)
      expect(screen.getByText('Pending changes')).toBeInTheDocument()
      // count appears in trigger badge AND popover row
      expect(screen.getAllByText('3').length).toBeGreaterThanOrEqual(1)
    })

    it('renders last sync result with synced count only', () => {
      setHookState({
        lastSyncResult: { success: true, syncedCount: 4, failedCount: 0, errors: [] },
      })
      render(<OfflineIndicator />)
      expect(screen.getByText(/4 synced/)).toBeInTheDocument()
      expect(screen.queryByText(/failed/)).not.toBeInTheDocument()
    })

    it('renders failed count when failedCount > 0', () => {
      setHookState({
        lastSyncResult: { success: false, syncedCount: 2, failedCount: 3, errors: [] },
      })
      render(<OfflineIndicator />)
      expect(screen.getByText(/3 failed/)).toBeInTheDocument()
    })

    it('renders at most 3 error messages from errors list', () => {
      setHookState({
        lastSyncResult: {
          success: false,
          syncedCount: 0,
          failedCount: 5,
          errors: [
            { id: '1', error: 'err-one' },
            { id: '2', error: 'err-two' },
            { id: '3', error: 'err-three' },
            { id: '4', error: 'err-four' },
            { id: '5', error: 'err-five' },
          ],
        },
      })
      render(<OfflineIndicator />)
      expect(screen.getByText('err-one')).toBeInTheDocument()
      expect(screen.getByText('err-two')).toBeInTheDocument()
      expect(screen.getByText('err-three')).toBeInTheDocument()
      expect(screen.queryByText('err-four')).not.toBeInTheDocument()
      expect(screen.queryByText('err-five')).not.toBeInTheDocument()
    })

    it('shows offline help message only when offline', () => {
      render(<OfflineIndicator />)
      expect(screen.queryByText(/Changes are saved locally and will sync/)).not.toBeInTheDocument()

      setHookState({ isOnline: false })
      render(<OfflineIndicator />)
      expect(screen.getAllByText(/Changes are saved locally and will sync/).length).toBeGreaterThan(
        0,
      )
    })
  })

  describe('sync button', () => {
    it('is disabled when offline', () => {
      setHookState({ isOnline: false, pendingCount: 2 })
      render(<OfflineIndicator />)
      const button = screen.getByRole('button', { name: /Sync Now/i })
      expect(button).toBeDisabled()
    })

    it('is disabled when isSyncing', () => {
      setHookState({ isSyncing: true, pendingCount: 2 })
      render(<OfflineIndicator />)
      const buttons = screen.getAllByRole('button', { name: /Syncing/i })
      expect(buttons[buttons.length - 1]).toBeDisabled()
    })

    it('is disabled when no pending', () => {
      render(<OfflineIndicator />)
      const button = screen.getByRole('button', { name: /Sync Now/i })
      expect(button).toBeDisabled()
    })

    it('is enabled and triggers sync when conditions met', async () => {
      const user = userEvent.setup()
      const sync = vi.fn().mockResolvedValue(undefined)
      setHookState({ pendingCount: 1, sync })
      render(<OfflineIndicator />)
      const button = screen.getByRole('button', { name: /Sync Now/i })
      expect(button).not.toBeDisabled()
      await user.click(button)
      expect(sync).toHaveBeenCalledTimes(1)
    })
  })
})
