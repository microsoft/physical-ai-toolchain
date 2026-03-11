import { afterEach, beforeEach, describe, expect, it } from 'vitest'

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
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: originalLocation,
    })
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
})
