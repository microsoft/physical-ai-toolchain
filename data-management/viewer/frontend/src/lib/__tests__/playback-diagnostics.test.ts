import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  clearDiagnosticEvents,
  DIAGNOSTICS_STORAGE_KEY,
  disableDiagnostics,
  enableDiagnostics,
  getEnabledDiagnosticsChannels,
  isDiagnosticsChannelEnabled,
  isDiagnosticsEnabled,
  readDiagnosticEvents,
  recordDiagnosticEvent,
  startPerformanceEntryCleanup,
} from '../playback-diagnostics'

describe('playback diagnostics', () => {
  const originalLocation = window.location
  const storage = new Map<string, string>()

  beforeEach(() => {
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      value: {
        clear: () => storage.clear(),
        getItem: (key: string) => storage.get(key) ?? null,
        removeItem: (key: string) => storage.delete(key),
        setItem: (key: string, value: string) => storage.set(key, value),
      },
    })
    localStorage.clear()
    clearDiagnosticEvents('playback')
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: originalLocation,
    })
  })

  it('remains inert without a browser window or supported persistent storage', () => {
    vi.stubGlobal('window', undefined)
    expect(getEnabledDiagnosticsChannels()).toEqual([])
    enableDiagnostics()
    disableDiagnostics()
    recordDiagnosticEvent('workspace', 'synthetic')
    expect(readDiagnosticEvents()).toEqual([])
    vi.unstubAllGlobals()
    Object.defineProperty(window, 'localStorage', { configurable: true, value: {} })
    enableDiagnostics()
    disableDiagnostics()
    expect(isDiagnosticsEnabled()).toBe(false)
  })

  it('normalizes comma-separated channels and does not enable an empty selection', () => {
    enableDiagnostics([])
    expect(isDiagnosticsEnabled()).toBe(false)
    enableDiagnostics('workspace, playback')
    expect(getEnabledDiagnosticsChannels()).toEqual(['workspace', 'playback'])
    clearDiagnosticEvents()
    expect(readDiagnosticEvents()).toEqual([])
  })

  it('enables a channel from local storage without affecting default behavior', () => {
    expect(isDiagnosticsEnabled()).toBe(false)
    expect(isDiagnosticsChannelEnabled('playback')).toBe(false)

    localStorage.setItem(DIAGNOSTICS_STORAGE_KEY, 'playback')

    expect(isDiagnosticsEnabled()).toBe(true)
    expect(isDiagnosticsChannelEnabled('playback')).toBe(true)
  })

  it('enables a channel from the diagnostics query string', () => {
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: {
        ...originalLocation,
        search: '?diagnostics=playback',
      },
    })

    expect(isDiagnosticsChannelEnabled('playback')).toBe(true)
  })

  it('records a bounded playback event stream only when the channel is enabled', () => {
    recordDiagnosticEvent('playback', 'selection-complete', { range: [10, 20] })

    expect(readDiagnosticEvents('playback')).toEqual([])

    localStorage.setItem(DIAGNOSTICS_STORAGE_KEY, 'playback')

    for (let index = 0; index < 205; index += 1) {
      recordDiagnosticEvent('playback', 'tick', { index })
    }

    const events = readDiagnosticEvents('playback')

    expect(events).toHaveLength(200)
    expect(events[0]?.data).toEqual({ index: 5 })
    expect(events[events.length - 1]?.data).toEqual({ index: 204 })
  })

  it('can enable and disable diagnostics from code for the whole dataviewer', () => {
    enableDiagnostics()

    expect(isDiagnosticsEnabled()).toBe(true)
    expect(getEnabledDiagnosticsChannels()).toEqual(['all'])

    disableDiagnostics()

    expect(isDiagnosticsEnabled()).toBe(false)
    expect(getEnabledDiagnosticsChannels()).toEqual([])
  })

  it('reads all diagnostics events when no channel filter is provided', () => {
    enableDiagnostics(['all'])

    recordDiagnosticEvent('workspace', 'tab-change', { nextTab: 'trajectory' })
    recordDiagnosticEvent('playback', 'selection-complete', { range: [10, 20] })

    expect(readDiagnosticEvents()).toHaveLength(2)
  })

  it('clears only the requested diagnostics channel', () => {
    enableDiagnostics(['all'])

    recordDiagnosticEvent('labels', 'draft-change', { labels: ['FAILURE'] })
    recordDiagnosticEvent('playback', 'sync-action', { action: 'play' })

    clearDiagnosticEvents('labels')

    expect(readDiagnosticEvents('labels')).toEqual([])
    expect(readDiagnosticEvents('playback')).toHaveLength(1)
  })

  it('bounds React development performance entries during continuous playback', () => {
    vi.useFakeTimers()
    const performanceApi = {
      clearMarks: vi.fn(),
      clearMeasures: vi.fn(),
    }

    const stop = startPerformanceEntryCleanup(performanceApi, 1_000)

    expect(performanceApi.clearMeasures).toHaveBeenCalledTimes(1)
    expect(performanceApi.clearMarks).toHaveBeenCalledTimes(1)

    vi.advanceTimersByTime(3_000)

    expect(performanceApi.clearMeasures).toHaveBeenCalledTimes(4)
    expect(performanceApi.clearMarks).toHaveBeenCalledTimes(4)

    stop()
    vi.advanceTimersByTime(1_000)

    expect(performanceApi.clearMeasures).toHaveBeenCalledTimes(4)
    expect(performanceApi.clearMarks).toHaveBeenCalledTimes(4)
  })
})
