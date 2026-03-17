export const DIAGNOSTICS_STORAGE_KEY = 'dataviewer:diagnostics'
export const DIAGNOSTICS_EVENT_NAME = 'dataviewer:diagnostics'
export const DEFAULT_DIAGNOSTICS_CHANNELS = ['all'] as const
export const DIAGNOSTIC_CHANNEL_OPTIONS = [
  'all',
  'workspace',
  'playback',
  'labels',
  'subtasks',
  'persistence',
  'export',
  'navigation',
  'detection',
] as const

const MAX_DIAGNOSTIC_EVENTS = 200

export interface DataviewerDiagnosticEvent {
  channel: string
  type: string
  data?: Record<string, unknown>
  timestamp: string
}

export type PlaybackDiagnosticEvent = DataviewerDiagnosticEvent

declare global {
  interface Window {
    __dataviewerDiagnostics__?: DataviewerDiagnosticEvent[]
  }
}

function splitChannels(raw: string | null | undefined) {
  return (raw ?? '')
    .split(',')
    .map((value) => value.trim())
    .filter(Boolean)
}

function getSearchValue(name: string) {
  if (typeof window === 'undefined') {
    return null
  }

  return new URLSearchParams(window.location.search).get(name)
}

function getStoredDiagnosticsValue() {
  if (typeof window === 'undefined') {
    return null
  }

  const storage = window.localStorage

  if (!storage || typeof storage.getItem !== 'function') {
    return null
  }

  return storage.getItem(DIAGNOSTICS_STORAGE_KEY)
}

function getStorage() {
  if (typeof window === 'undefined') {
    return null
  }

  const storage = window.localStorage

  if (!storage || typeof storage.getItem !== 'function') {
    return null
  }

  return storage
}

function getConfiguredChannels() {
  const searchChannels = splitChannels(getSearchValue('diagnostics'))
  const storageChannels = splitChannels(getStoredDiagnosticsValue())
  const envChannels = splitChannels(import.meta.env.VITE_DATAVIEWER_DIAGNOSTICS)

  return new Set([...envChannels, ...storageChannels, ...searchChannels])
}

function normalizeChannels(channels?: readonly string[] | string) {
  if (!channels) {
    return [...DEFAULT_DIAGNOSTICS_CHANNELS]
  }

  if (typeof channels === 'string') {
    return splitChannels(channels)
  }

  return channels.flatMap((value) => splitChannels(value)).filter(Boolean)
}

function getDiagnosticBuffer() {
  if (typeof window === 'undefined') {
    return []
  }

  window.__dataviewerDiagnostics__ ??= []

  return window.__dataviewerDiagnostics__
}

export function getEnabledDiagnosticsChannels() {
  return [...getConfiguredChannels()]
}

export function isDiagnosticsEnabled() {
  return getEnabledDiagnosticsChannels().length > 0
}

export function enableDiagnostics(channels?: readonly string[] | string) {
  const storage = getStorage()

  if (!storage || typeof storage.setItem !== 'function') {
    return
  }

  const nextChannels = normalizeChannels(channels)

  if (nextChannels.length === 0) {
    return
  }

  storage.setItem(DIAGNOSTICS_STORAGE_KEY, nextChannels.join(','))
}

export function disableDiagnostics() {
  const storage = getStorage()

  if (!storage || typeof storage.removeItem !== 'function') {
    return
  }

  storage.removeItem(DIAGNOSTICS_STORAGE_KEY)
}

export function isDiagnosticsChannelEnabled(channel: string) {
  const channels = getConfiguredChannels()

  return channels.has('all') || channels.has(channel)
}

export function readDiagnosticEvents(channel?: string) {
  const events = getDiagnosticBuffer()

  if (!channel) {
    return events
  }

  return events.filter((event) => event.channel === channel)
}

export function clearDiagnosticEvents(channel?: string) {
  if (typeof window === 'undefined' || !window.__dataviewerDiagnostics__) {
    return
  }

  if (!channel) {
    window.__dataviewerDiagnostics__ = []
    return
  }

  window.__dataviewerDiagnostics__ = window.__dataviewerDiagnostics__.filter(
    (event) => event.channel !== channel,
  )
}

export function stringifyDiagnosticEvents(events: readonly DataviewerDiagnosticEvent[]) {
  return JSON.stringify(events, null, 2)
}

export function recordDiagnosticEvent(
  channel: string,
  type: string,
  data?: Record<string, unknown>,
) {
  if (!isDiagnosticsChannelEnabled(channel) || typeof window === 'undefined') {
    return
  }

  const events = getDiagnosticBuffer()

  events.push({
    channel,
    type,
    data,
    timestamp: new Date().toISOString(),
  })

  if (events.length > MAX_DIAGNOSTIC_EVENTS) {
    events.splice(0, events.length - MAX_DIAGNOSTIC_EVENTS)
  }

  window.dispatchEvent(new CustomEvent(DIAGNOSTICS_EVENT_NAME, { detail: { channel, type, data } }))
}
