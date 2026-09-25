import type { Locator, Page, TestInfo } from '@playwright/test'
import { expect, test } from '@playwright/test'

import { attachJson, openEmptyViewer, openViewer } from './accessibility-fixture'

const interactiveSelector = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[role="button"]:not([aria-disabled="true"])',
  '[role="slider"]:not([aria-disabled="true"])',
  '[role="tab"]:not([aria-disabled="true"])',
].join(', ')

function contrastRatio(foreground: string, background: string) {
  const luminance = (color: string) => {
    const channels = color
      .match(/[\d.]+/g)
      ?.slice(0, 3)
      .map(Number)
    if (!channels || channels.length !== 3) throw new Error(`Unsupported color: ${color}`)
    const linear = channels.map((channel) => {
      const normalized = channel / 255
      return normalized <= 0.04045 ? normalized / 12.92 : ((normalized + 0.055) / 1.055) ** 2.4
    })
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]
  }
  const values = [luminance(foreground), luminance(background)].sort((left, right) => right - left)
  return (values[0] + 0.05) / (values[1] + 0.05)
}

async function textColors(locator: Locator) {
  return locator.evaluate((element) => {
    const foreground = getComputedStyle(element).color
    let background = 'rgb(255, 255, 255)'
    let ancestor: Element | null = element
    while (ancestor) {
      const candidate = getComputedStyle(ancestor).backgroundColor
      if (!candidate.endsWith(', 0)') && candidate !== 'transparent') {
        background = candidate
        break
      }
      ancestor = ancestor.parentElement
    }
    return { foreground, background }
  })
}

async function layoutFindings(page: Page) {
  return page.evaluate(() => {
    const clipped = [...document.querySelectorAll<HTMLElement>('body *')]
      .filter((element) =>
        element.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true }),
      )
      .filter((element) => {
        const rect = element.getBoundingClientRect()
        let ancestor = element.parentElement
        while (ancestor) {
          if (['auto', 'scroll'].includes(getComputedStyle(ancestor).overflowX)) return false
          ancestor = ancestor.parentElement
        }
        return rect.left < -1 || rect.right > innerWidth + 1
      })
      .map((element) => ({
        tag: element.tagName,
        name: element.getAttribute('aria-label') ?? element.textContent?.trim().slice(0, 80) ?? '',
      }))
    return {
      clipped,
      pageOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      viewport: { width: innerWidth, height: innerHeight },
    }
  })
}

async function assertNoLayoutLoss(page: Page, testInfo: TestInfo, state: string) {
  const findings = await layoutFindings(page)
  await attachJson(testInfo, `${state}-layout`, findings)
  expect(findings.pageOverflow, `${state} document overflow`).toBeLessThanOrEqual(1)
  expect(findings.clipped, `${state} clipped content`).toEqual([])
}

test('V19 preserves loaded and transient content under adaptive rendering', async ({
  page,
}, testInfo) => {
  const errors = await openViewer(page, testInfo)

  const textResizeCheckpoints = []
  for (const scale of [1, 1.5, 2]) {
    if (scale > 1) {
      await page.reload()
      await expect(page.getByRole('heading', { name: 'Episode 0' })).toBeVisible()
    }
    const baselineSize = await page.evaluate(() =>
      Number.parseFloat(getComputedStyle(document.documentElement).fontSize),
    )
    if (scale > 1) {
      await page.addStyleTag({ content: `html { font-size: ${scale * 100}% !important; }` })
    }
    const computedSize = await page.evaluate(() =>
      Number.parseFloat(getComputedStyle(document.documentElement).fontSize),
    )
    expect(computedSize, `${scale * 100}% computed root text size`).toBeCloseTo(
      baselineSize * scale,
      1,
    )
    await assertNoLayoutLoss(page, testInfo, `text-resize-${scale * 100}`)
    const datasetButton = page.getByRole('button', { name: 'Dataset' })
    await datasetButton.click()
    await expect(page.getByPlaceholder('Filter datasets')).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(datasetButton).toBeFocused()
    textResizeCheckpoints.push({ scale, baselineSize, computedSize, task: 'dataset-open-close' })
  }
  await attachJson(testInfo, 'text-resize-checkpoints', {
    condition: 'css-text-resize',
    checkpoints: textResizeCheckpoints,
  })

  await page.reload()
  await expect(page.getByRole('heading', { name: 'Episode 0' })).toBeVisible()
  await page.addStyleTag({
    content:
      '* { line-height: 1.5 !important; letter-spacing: 0.12em !important; word-spacing: 0.16em !important; }',
  })
  await assertNoLayoutLoss(page, testInfo, 'text-spacing')

  await page.reload()
  await expect(page.getByRole('heading', { name: 'Episode 0' })).toBeVisible()
  await page.setViewportSize({ width: 320, height: 720 })
  await assertNoLayoutLoss(page, testInfo, 'reflow-loaded')
  await page.getByRole('button', { name: 'Dataset' }).click()
  await assertNoLayoutLoss(page, testInfo, 'reflow-dataset-open')
  await page.keyboard.press('Escape')
  const exportButton = page.getByRole('button', { name: 'Export', exact: true })
  await exportButton.focus()
  await exportButton.press('Enter')
  await assertNoLayoutLoss(page, testInfo, 'reflow-export-open')

  await page.reload()
  await expect(page.getByRole('heading', { name: 'Episode 0' })).toBeVisible()
  await page.setViewportSize({ width: 320, height: 720 })
  await page.getByRole('button', { name: 'Toggle Diagnostics' }).click()
  await expect(page.getByText('Dataviewer Diagnostics')).toBeVisible()
  await assertNoLayoutLoss(page, testInfo, 'reflow-diagnostics')

  await page.unrouteAll({ behavior: 'wait' })
  await openEmptyViewer(page, testInfo)
  await page.setViewportSize({ width: 320, height: 720 })
  await assertNoLayoutLoss(page, testInfo, 'reflow-empty')

  await page.unrouteAll({ behavior: 'wait' })
  await openViewer(page, testInfo)
  await page.setViewportSize({ width: 720, height: 320 })
  await page.getByRole('tab', { name: 'Episode Analyzer' }).click()
  await assertNoLayoutLoss(page, testInfo, 'landscape-analyzer')

  await page.emulateMedia({ forcedColors: 'active', reducedMotion: 'reduce' })
  const runningAnimations = await page.locator('body *').evaluateAll((elements) =>
    elements
      .filter((element) => {
        const style = getComputedStyle(element)
        return style.animationName !== 'none' && style.animationPlayState === 'running'
      })
      .map((element) => ({
        tag: element.tagName,
        animation: getComputedStyle(element).animationName,
      })),
  )
  expect(runningAnimations).toEqual([])
  expect(errors.consoleErrors).toEqual([])
  expect(errors.pageErrors).toEqual([])
})

async function focusFinding(locator: Locator, shouldFocus = true) {
  await locator.scrollIntoViewIfNeeded()
  if (shouldFocus) await locator.focus()
  return locator.evaluate((element) => {
    const target = element as HTMLElement
    const style = getComputedStyle(target)
    const rect = target.getBoundingClientRect()
    const centerX = Math.min(Math.max(rect.left + rect.width / 2, 0), innerWidth - 1)
    const centerY = Math.min(Math.max(rect.top + rect.height / 2, 0), innerHeight - 1)
    const coveringElement = document.elementFromPoint(centerX, centerY)
    return {
      name:
        target.getAttribute('aria-label') ??
        target.getAttribute('placeholder') ??
        target.textContent?.trim().slice(0, 80) ??
        '',
      tag: target.tagName,
      role: target.getAttribute('role'),
      state: target.getAttribute('data-state'),
      className: target.className,
      hasIndicator: style.outlineStyle !== 'none' || style.boxShadow !== 'none',
      inViewport:
        rect.bottom > 0 && rect.right > 0 && rect.top < innerHeight && rect.left < innerWidth,
      entirelyObscured:
        coveringElement !== null &&
        !target.contains(coveringElement) &&
        !coveringElement.contains(target),
    }
  })
}

test('V02 V03 V05 and V19 primary focus stops stay visible and unobscured', async ({
  page,
}, testInfo) => {
  const errors = await openViewer(page, testInfo)
  const stops = [
    page.getByRole('button', { name: 'Dataset' }),
    page.getByRole('button', { name: 'Toggle Diagnostics' }),
    page.getByRole('link', { name: 'Help' }),
    page.getByRole('link', { name: 'Report problem' }),
    page.getByRole('button', { name: /Episode 0/ }),
    page.getByRole('button', { name: /Episode 1/ }),
    page.getByRole('button', { name: 'Export', exact: true }),
    page.getByRole('button', { name: 'Save & Next Episode' }),
    page.getByRole('tab', { name: 'Trajectory Viewer' }),
  ]
  const findings = []
  for (const stop of stops) findings.push(await focusFinding(stop))

  await page.getByRole('button', { name: 'Toggle Diagnostics' }).click()
  const diagnosticsStops = [
    page.getByRole('combobox', { name: 'Filter Events' }),
    page.getByRole('button', { name: 'Clear Visible Events' }),
    page.getByRole('button', { name: 'Copy JSON' }),
    page.getByRole('button', { name: 'Download JSON' }),
  ]
  for (const stop of diagnosticsStops) findings.push(await focusFinding(stop))

  await page.getByRole('button', { name: 'Dataset' }).click()
  findings.push(await focusFinding(page.getByRole('combobox', { name: 'Filter datasets' })))
  await page.keyboard.press('Escape')

  const exportTrigger = page.getByRole('button', { name: 'Export', exact: true })
  await exportTrigger.focus()
  await exportTrigger.press('Enter')
  const exportDialog = page.getByRole('dialog', { name: 'Export Episodes' })
  const exportStops = [
    exportDialog.getByPlaceholder('/path/to/exports'),
    exportDialog.getByRole('checkbox', { name: 'Apply crop, resize, and frame removal' }),
    exportDialog.getByRole('checkbox', { name: 'Include subtask metadata' }),
    exportDialog.getByRole('button', { name: 'Cancel' }),
    exportDialog.getByRole('button', { name: 'Start Export' }),
    exportDialog.getByRole('button', { name: 'Close' }),
  ]
  await expect(exportStops[0]).toBeFocused()
  findings.push(await focusFinding(exportStops[0], false))
  for (const stop of exportStops.slice(1)) {
    await page.keyboard.press('Tab')
    await expect(stop).toBeFocused()
    findings.push(await focusFinding(stop, false))
  }
  await page.keyboard.press('Escape')
  const contrastSamples = [
    page.getByRole('heading', { level: 1 }),
    page.getByRole('button', { name: /Episode 0/ }).locator('.text-sm'),
    page.getByText('✓ Annotated'),
    page.getByRole('tab', { name: 'Episode Analyzer' }),
  ]
  const contrastFindings = []
  for (const sample of contrastSamples) {
    const colors = await textColors(sample)
    contrastFindings.push({ ...colors, ratio: contrastRatio(colors.foreground, colors.background) })
  }
  await attachJson(testInfo, 'primary-focus-presentation', { findings, contrastFindings })
  expect(findings.filter((finding) => !finding.hasIndicator)).toEqual([])
  expect(findings.filter((finding) => !finding.inViewport)).toEqual([])
  expect(findings.filter((finding) => finding.entirelyObscured)).toEqual([])
  expect(contrastFindings.filter((finding) => finding.ratio < 4.5)).toEqual([])
  expect(errors.consoleErrors).toEqual([])
  expect(errors.pageErrors).toEqual([])
})

test('V08 V09 and V19 targets satisfy size or spacing and pointer cancellation', async ({
  page,
}, testInfo) => {
  const errors = await openViewer(page, testInfo, { trajectoryVariables: 'joints' })
  const targets = await page.locator(interactiveSelector).evaluateAll((elements) => {
    const visible = elements
      .filter((element) =>
        (element as HTMLElement).checkVisibility({ checkOpacity: true, checkVisibilityCSS: true }),
      )
      .map((element) => {
        const target = element as HTMLElement
        const rect = target.getBoundingClientRect()
        return {
          name:
            target.getAttribute('aria-label') ??
            target.getAttribute('title') ??
            target.textContent?.trim().slice(0, 80) ??
            target.tagName,
          width: rect.width,
          height: rect.height,
          centerX: rect.left + rect.width / 2,
          centerY: rect.top + rect.height / 2,
        }
      })
    return visible.map((target, index) => ({
      ...target,
      nearestCenterDistance: Math.min(
        ...visible
          .filter((_, candidateIndex) => candidateIndex !== index)
          .map((candidate) =>
            Math.hypot(target.centerX - candidate.centerX, target.centerY - candidate.centerY),
          ),
      ),
    }))
  })
  const targetFailures = targets.filter(
    (target) => (target.width < 24 || target.height < 24) && target.nearestCenterDistance < 24,
  )
  await attachJson(testInfo, 'target-geometry', { targets, targetFailures })
  expect(targetFailures).toEqual([])

  const surface = page.getByTestId('trajectory-selection-surface')
  const box = await surface.boundingBox()
  expect(box).not.toBeNull()
  await page.mouse.move((box?.x ?? 0) + 40, (box?.y ?? 0) + 40)
  await page.mouse.down()
  await page.mouse.move((box?.x ?? 0) + 180, (box?.y ?? 0) + 40)
  await surface.dispatchEvent('pointercancel', { pointerId: 1 })
  await page.mouse.up()
  await expect(page.getByRole('button', { name: 'Clear Selection' })).toHaveCount(0)

  await surface.click({ position: { x: Math.max((box?.width ?? 100) - 20, 1), y: 40 } })
  await expect(page.getByText(/Frame \d+ of 12/).first()).toBeVisible()

  const jointOrder = async () =>
    page
      .locator('[data-joint-chip]')
      .evaluateAll((elements) => elements.map((element) => element.textContent?.trim()))
  const shoulderJoint = page.getByRole('button', { name: 'shoulder_joint', exact: true })
  await shoulderJoint.click({ button: 'right' })
  await page.getByRole('menuitem', { name: 'Move right' }).click()
  expect(await jointOrder()).toEqual(['target_joint', 'shoulder_joint'])

  const nativeSlider = page.locator('input[type="range"]').first()
  const initialSliderValue = await nativeSlider.inputValue()
  const sliderBox = await nativeSlider.boundingBox()
  expect(sliderBox).not.toBeNull()
  await nativeSlider.click({
    position: { x: 2, y: (sliderBox?.height ?? 10) / 2 },
  })
  expect(await nativeSlider.inputValue()).not.toBe(initialSliderValue)
  expect(errors.consoleErrors).toEqual([])
  expect(errors.pageErrors).toEqual([])
})
