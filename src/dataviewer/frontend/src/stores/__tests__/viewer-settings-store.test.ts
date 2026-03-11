import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'

import { usePlaybackSettings, useViewerDisplay, useViewerSettingsStore } from '../viewer-settings-store'

describe('viewer-settings-store', () => {
  beforeEach(() => {
    useViewerSettingsStore.getState().resetAdjustments()
  })

  it('initializes with default values and inactive state', () => {
    const { result } = renderHook(() => useViewerDisplay())
    expect(result.current.isActive).toBe(false)
    expect(result.current.displayAdjustment.brightness).toBe(0)
    expect(result.current.displayAdjustment.contrast).toBe(0)
    expect(result.current.displayAdjustment.saturation).toBe(0)
    expect(result.current.displayAdjustment.gamma).toBe(1)
    expect(result.current.displayAdjustment.hue).toBe(0)
  })

  it('becomes active when a non-default value is set', () => {
    const { result } = renderHook(() => useViewerDisplay())

    act(() => result.current.setAdjustment('brightness', 0.5))

    expect(result.current.isActive).toBe(true)
    expect(result.current.displayAdjustment.brightness).toBe(0.5)
  })

  it('becomes inactive when values return to default', () => {
    const { result } = renderHook(() => useViewerDisplay())

    act(() => result.current.setAdjustment('brightness', 0.5))
    expect(result.current.isActive).toBe(true)

    act(() => result.current.setAdjustment('brightness', 0))
    expect(result.current.isActive).toBe(false)
  })

  it('resets all adjustments', () => {
    const { result } = renderHook(() => useViewerDisplay())

    act(() => {
      result.current.setAdjustment('brightness', 0.3)
      result.current.setAdjustment('contrast', 0.5)
      result.current.setAdjustment('gamma', 2)
    })
    expect(result.current.isActive).toBe(true)

    act(() => result.current.resetAdjustments())

    expect(result.current.isActive).toBe(false)
    expect(result.current.displayAdjustment.brightness).toBe(0)
    expect(result.current.displayAdjustment.contrast).toBe(0)
    expect(result.current.displayAdjustment.gamma).toBe(1)
  })

  it('preserves other values when updating one adjustment', () => {
    const { result } = renderHook(() => useViewerDisplay())

    act(() => {
      result.current.setAdjustment('brightness', 0.2)
      result.current.setAdjustment('contrast', 0.4)
    })

    expect(result.current.displayAdjustment.brightness).toBe(0.2)
    expect(result.current.displayAdjustment.contrast).toBe(0.4)
  })
})

describe('playback settings', () => {
  beforeEach(() => {
    useViewerSettingsStore.getState().resetAdjustments()
  })

  it('defaults to autoPlay true and autoLoop true', () => {
    const { result } = renderHook(() => usePlaybackSettings())
    expect(result.current.autoPlay).toBe(true)
    expect(result.current.autoLoop).toBe(true)
  })

  it('toggles autoPlay', () => {
    const { result } = renderHook(() => usePlaybackSettings())

    act(() => result.current.setAutoPlay(false))
    expect(result.current.autoPlay).toBe(false)

    act(() => result.current.setAutoPlay(true))
    expect(result.current.autoPlay).toBe(true)
  })

  it('toggles autoLoop', () => {
    const { result } = renderHook(() => usePlaybackSettings())

    act(() => result.current.setAutoLoop(false))
    expect(result.current.autoLoop).toBe(false)

    act(() => result.current.setAutoLoop(true))
    expect(result.current.autoLoop).toBe(true)
  })

  it('persists across hook renders', () => {
    act(() => {
      useViewerSettingsStore.getState().setAutoPlay(false)
      useViewerSettingsStore.getState().setAutoLoop(false)
    })

    const { result } = renderHook(() => usePlaybackSettings())
    expect(result.current.autoPlay).toBe(false)
    expect(result.current.autoLoop).toBe(false)
  })
})
