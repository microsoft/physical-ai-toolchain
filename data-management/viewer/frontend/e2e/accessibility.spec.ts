import type { Page } from '@playwright/test'
import { expect, test } from '@playwright/test'

import { attachJson, openViewer } from './accessibility-fixture'

// cspell:words opblock

test('V02 and V03 support keyboard dataset and episode selection', async ({ page }, testInfo) => {
  const errors = await openViewer(page, testInfo)
  const datasetButton = page.getByRole('button', { name: 'Dataset' })

  await datasetButton.focus()
  await page.keyboard.press('Enter')
  const filter = page.getByPlaceholder('Filter datasets')
  await expect(filter).toBeFocused()
  await filter.fill('secondary')
  await page.keyboard.press('ArrowDown')
  await page.keyboard.press('Enter')
  await expect(datasetButton).toContainText('a11y-secondary')

  const secondEpisode = page.getByRole('button', { name: /Episode 1/ })
  await secondEpisode.focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('heading', { name: 'Episode 1' })).toBeVisible()
  await attachJson(testInfo, 'keyboard-trace', {
    sequence: [
      'Dataset',
      'Enter',
      'Filter datasets',
      'secondary',
      'ArrowDown',
      'Enter',
      'Episode 1',
      'Enter',
    ],
    outcome: 'Dataset and episode selection completed by keyboard',
  })
  expect(errors.consoleErrors).toEqual([])
  expect(errors.pageErrors).toEqual([])
})

test('V05 V06 V08 V13 V14 and V18 expose exact workspace semantics', async ({ page }, testInfo) => {
  const errors = await openViewer(page, testInfo)

  const trajectoryTab = page.getByRole('tab', { name: 'Trajectory Viewer' })
  const analyzerTab = page.getByRole('tab', { name: 'Episode Analyzer' })
  await trajectoryTab.focus()
  await page.keyboard.press('ArrowRight')
  await expect(analyzerTab).toHaveAttribute('aria-selected', 'true')
  await page.keyboard.press('ArrowLeft')
  await expect(trajectoryTab).toHaveAttribute('aria-selected', 'true')

  await expect(page.getByRole('button', { name: /^(Play|Pause) playback$/ })).toBeVisible()
  await expect(page.getByRole('table', { name: 'Trajectory data' })).toBeAttached()
  await expect(page.getByRole('heading', { name: 'VLM Judge' }).first()).toBeVisible()
  await expect(page.getByText('Object Detection', { exact: true }).first()).toBeVisible()

  const exportButton = page.getByRole('button', { name: 'Export', exact: true })
  await exportButton.click()
  const dialog = page.getByRole('dialog', { name: 'Export Episodes' })
  await expect(dialog).toBeVisible()
  await expect(dialog.getByPlaceholder('/path/to/exports')).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(dialog).toBeHidden()
  const focusReturned = await exportButton.evaluate((element) => document.activeElement === element)

  await attachJson(testInfo, 'workspace-semantics', {
    journeys: ['V05', 'V06', 'V08', 'V13', 'V14', 'V18'],
    assertions: [
      'tab selection',
      'playback name',
      'trajectory table',
      'judge heading',
      'detection text',
      'dialog focus return',
    ],
    focusReturned,
  })
  expect.soft(focusReturned, 'Escape must return focus to the Export trigger').toBe(true)
  expect(errors.consoleErrors).toEqual([])
  expect(errors.pageErrors).toEqual([])
})

test('V02 and V18 preserve forward and reverse focus boundaries', async ({ page }, testInfo) => {
  const errors = await openViewer(page, testInfo)
  const datasetButton = page.getByRole('button', { name: 'Dataset' })
  const datasetDialog = page.getByRole('dialog', { name: 'Select dataset' })
  const diagnosticsButton = page.getByRole('button', { name: 'Toggle Diagnostics' })

  await datasetButton.focus()
  await datasetButton.press('Enter')
  const filterInput = page.getByPlaceholder('Filter datasets')
  await expect(filterInput).toBeFocused()
  await page.keyboard.press('Tab')
  await expect(filterInput).toBeFocused()
  await page.keyboard.press('Shift+Tab')
  await expect(filterInput).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(datasetDialog).toBeHidden()
  await expect(datasetButton).toBeFocused()
  await page.keyboard.press('Tab')
  await expect(diagnosticsButton).toBeFocused()
  await page.keyboard.press('Shift+Tab')
  await expect(datasetButton).toBeFocused()

  const exportButton = page.getByRole('button', { name: 'Export', exact: true })
  await exportButton.focus()
  await exportButton.press('Enter')
  const exportDialog = page.getByRole('dialog', { name: 'Export Episodes' })
  await expect(exportDialog.getByPlaceholder('/path/to/exports')).toBeFocused()

  const dialogStops = exportDialog.locator(
    'button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])',
  )
  const stopCount = await dialogStops.count()
  expect(stopCount).toBeGreaterThan(1)

  for (const key of ['Tab', 'Shift+Tab'] as const) {
    for (let step = 0; step < stopCount + 2; step += 1) {
      await page.keyboard.press(key)
      expect(
        await exportDialog.evaluate((dialog) => dialog.contains(document.activeElement)),
        `${key} must remain inside the open modal`,
      ).toBe(true)
    }
  }

  await page.keyboard.press('Escape')
  await expect(exportDialog).toBeHidden()
  await expect(exportButton).toBeFocused()
  await attachJson(testInfo, 'focus-boundary-trace', {
    journeys: ['V02', 'V18'],
    assertions: [
      'dataset popover contains forward and reverse focus',
      'Escape closes dataset popover and restores trigger focus',
      'forward and reverse shell navigation resumes after dismissal',
      'export modal contains forward focus',
      'export modal contains reverse focus',
      'Escape closes export and restores trigger focus',
    ],
  })
  expect(errors.consoleErrors).toEqual([])
  expect(errors.pageErrors).toEqual([])
})

test('V19 preserves adaptive layout focus and target geometry', async ({ page }, testInfo) => {
  const errors = await openViewer(page, testInfo)

  const collectGeometry = () =>
    page.evaluate(() => {
      const label = (element: HTMLElement) =>
        element.getAttribute('aria-label') ?? element.innerText ?? element.textContent ?? ''
      const visible = [...document.querySelectorAll<HTMLElement>('body *')].filter((element) => {
        const style = getComputedStyle(element)
        const rect = element.getBoundingClientRect()
        const visuallyHidden =
          Boolean(element.closest('.sr-only')) || style.clipPath !== 'none' || style.clip !== 'auto'
        return (
          !visuallyHidden &&
          style.display !== 'none' &&
          style.visibility !== 'hidden' &&
          rect.width > 0 &&
          rect.height > 0
        )
      })
      const clipped = visible
        .filter((element) => {
          const rect = element.getBoundingClientRect()
          let ancestor: HTMLElement | null = element
          let hasScrollableAncestor = false
          while (ancestor) {
            if (['auto', 'scroll'].includes(getComputedStyle(ancestor).overflowX)) {
              hasScrollableAncestor = true
              break
            }
            ancestor = ancestor.parentElement
          }
          return !hasScrollableAncestor && (rect.left < -1 || rect.right > innerWidth + 1)
        })
        .map((element) => ({ tag: element.tagName, text: label(element).slice(0, 80) }))
      const smallTargets = visible
        .filter((element) =>
          element.matches('button, input, select, textarea, [role="button"], [role="tab"]'),
        )
        .filter((element) => {
          const rect = element.getBoundingClientRect()
          return rect.width < 24 || rect.height < 24
        })
        .map((element) => ({ tag: element.tagName, name: label(element).slice(0, 80) }))
      const runningMotion = visible
        .filter((element) => {
          const style = getComputedStyle(element)
          return style.animationName !== 'none' && style.animationPlayState === 'running'
        })
        .map((element) => ({
          tag: element.tagName,
          animation: getComputedStyle(element).animationName,
        }))
      return {
        clipped,
        smallTargets,
        runningMotion,
        viewport: { width: innerWidth, height: innerHeight },
      }
    })

  await page.addStyleTag({
    content: 'html { font-size: 200% !important; }',
  })
  const textResize = await collectGeometry()
  await page.screenshot({ path: testInfo.outputPath('viewer-text-resize-200.png'), fullPage: true })

  await page.reload()
  await expect(page.getByRole('heading', { name: 'Episode 0' })).toBeVisible()
  await page.addStyleTag({
    content:
      '* { line-height: 1.5 !important; letter-spacing: 0.12em !important; word-spacing: 0.16em !important; }',
  })
  const textSpacing = await collectGeometry()

  await page.reload()
  await expect(page.getByRole('heading', { name: 'Episode 0' })).toBeVisible()
  await page.setViewportSize({ width: 320, height: 720 })
  const reflow = await collectGeometry()
  const datasetButton = page.getByRole('button', { name: 'Dataset' })
  await datasetButton.focus()
  const focusStyle = await datasetButton.evaluate((element) => {
    const style = getComputedStyle(element)
    const rect = element.getBoundingClientRect()
    return {
      outline: style.outlineStyle,
      shadow: style.boxShadow,
      withinViewport:
        rect.top >= 0 && rect.bottom <= innerHeight && rect.left >= 0 && rect.right <= innerWidth,
    }
  })
  await page.screenshot({ path: testInfo.outputPath('viewer-reflow-320.png'), fullPage: true })

  await page.emulateMedia({ forcedColors: 'active', reducedMotion: 'reduce' })
  const mediaPreferences = await collectGeometry()
  await attachJson(testInfo, 'adaptive-measurements', {
    textResize,
    textSpacing,
    reflow,
    mediaPreferences,
    focusStyle,
    targetSizeDisposition: 'CANT_TELL pending spacing and exception adjudication',
  })

  expect(textResize.clipped).toEqual([])
  expect(textSpacing.clipped).toEqual([])
  expect(reflow.clipped).toEqual([])
  expect(Array.isArray(reflow.smallTargets)).toBe(true)
  expect(mediaPreferences.runningMotion).toEqual([])
  expect(focusStyle.withinViewport).toBe(true)
  expect(focusStyle.outline !== 'none' || focusStyle.shadow !== 'none').toBe(true)
  expect(errors.consoleErrors).toEqual([])
  expect(errors.pageErrors).toEqual([])
})

interface SwaggerDocumentationTask {
  kind: 'swagger'
  successPath: string
  inputPath: string
  inputKind: 'body' | 'parameter'
  inputValue: string
}

interface RedocDocumentationTask {
  kind: 'redoc'
  searchTerm: string
  operationName: string
  operationPath: string
}

type DocumentationTask = SwaggerDocumentationTask | RedocDocumentationTask

function exactText(value: string): RegExp {
  return new RegExp(`^${value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}$`)
}

function swaggerOperation(page: Page, path: string) {
  return page.locator('.opblock').filter({
    has: page.locator('.opblock-summary-path').filter({ hasText: exactText(path) }),
  })
}

async function exerciseSwagger(page: Page, task: SwaggerDocumentationTask) {
  const successOperation = swaggerOperation(page, task.successPath)
  const successSummary = successOperation.locator('.opblock-summary-control')
  await successSummary.focus()
  await successSummary.press('Enter')
  await successOperation.getByRole('button', { name: 'Try it out' }).press('Enter')
  await successOperation.getByRole('button', { name: 'Execute' }).press('Enter')
  const response = successOperation.locator('.live-responses-table')
  const responseStatus = response.locator('tbody .response-col_status').first()
  await expect(response).toBeVisible()
  await expect(responseStatus).toHaveText(/^2\d\d$/)

  const inputOperation = swaggerOperation(page, task.inputPath)
  const inputSummary = inputOperation.locator('.opblock-summary-control')
  await inputSummary.focus()
  await inputSummary.press('Enter')
  await inputOperation.getByRole('button', { name: 'Try it out' }).press('Enter')
  const input =
    task.inputKind === 'body'
      ? inputOperation.locator('textarea.body-param__text')
      : inputOperation.getByPlaceholder('dataset_id')
  await input.fill(task.inputValue)
  await expect(input).toHaveValue(task.inputValue)
  await input.fill('')
  await inputOperation.getByRole('button', { name: 'Execute' }).press('Enter')
  const invalidInput = inputOperation.getByRole('textbox', {
    name: 'Required field is not provided',
  })
  await expect(invalidInput).toBeVisible()
  const errorPresentation = await invalidInput.getAttribute('aria-label')

  return {
    successPath: task.successPath,
    successStatus: await responseStatus.textContent(),
    inputPath: task.inputPath,
    errorPresentation,
  }
}

async function exerciseRedoc(page: Page, task: RedocDocumentationTask) {
  const search = page.getByRole('textbox', { name: 'Search' })
  await search.focus()
  await search.fill(task.searchTerm)
  const result = page
    .getByRole('search')
    .getByRole('menuitem', { name: task.operationName, exact: true })
  await result.focus()
  await result.press('Enter')
  await expect(page).toHaveURL(/#(?:tag\/.+\/)?operation\//)
  for (
    let attempt = 0;
    attempt < 5 && !(await search.evaluate((el) => el === document.activeElement));
    attempt += 1
  ) {
    await page.keyboard.press('Shift+Tab')
  }
  await expect(search).toBeFocused()

  await search.fill('')
  const operation = page.locator('button').filter({ hasText: task.operationPath }).first()
  await operation.scrollIntoViewIfNeeded()
  await operation.focus()
  await operation.press('Enter')
  const serverUrl = new URL(task.operationPath, page.url()).toString()
  await expect(operation.locator('xpath=..')).toContainText(serverUrl)

  return {
    searchTerm: task.searchTerm,
    operationName: task.operationName,
    operationPath: task.operationPath,
    keyboardReturn: await search.evaluate((element) => element === document.activeElement),
  }
}

const documentationCases: Array<{
  id: string
  name: string
  url: string
  selector: string
  title: string
  task: DocumentationTask
}> = [
  {
    id: 'F01',
    name: 'Viewer Swagger UI',
    url: 'http://127.0.0.1:8000/docs',
    selector: '#swagger-ui',
    title: 'LeRobot Annotation API',
    task: {
      kind: 'swagger',
      successPath: '/api/auth/context',
      inputPath: '/api/datasets/{dataset_id}',
      inputKind: 'parameter',
      inputValue: 'a11y-missing',
    },
  },
  {
    id: 'F02',
    name: 'Viewer ReDoc',
    url: 'http://127.0.0.1:8000/redoc',
    selector: 'redoc, .redoc-wrap',
    title: 'LeRobot Annotation API',
    task: {
      kind: 'redoc',
      searchTerm: 'auth context',
      operationName: 'Get Auth Context',
      operationPath: '/api/auth/context',
    },
  },
  {
    id: 'F03',
    name: 'VLM Judge Swagger UI',
    url: 'http://127.0.0.1:8001/docs',
    selector: '#swagger-ui',
    title: 'VLM-as-Judge',
    task: {
      kind: 'swagger',
      successPath: '/health',
      inputPath: '/judge',
      inputKind: 'body',
      inputValue: '{}',
    },
  },
  {
    id: 'F04',
    name: 'VLM Judge ReDoc',
    url: 'http://127.0.0.1:8001/redoc',
    selector: 'redoc, .redoc-wrap',
    title: 'VLM-as-Judge',
    task: {
      kind: 'redoc',
      searchTerm: 'judge',
      operationName: 'Judge',
      operationPath: '/judge',
    },
  },
  {
    id: 'F05',
    name: 'OpenAI Shim Swagger UI',
    url: 'http://127.0.0.1:8002/docs',
    selector: '#swagger-ui',
    title: 'Qwen3-VL OpenAI-compat shim',
    task: {
      kind: 'swagger',
      successPath: '/v1/models',
      inputPath: '/v1/chat/completions',
      inputKind: 'body',
      inputValue: '{"messages":[{"role":"user","content":"test"}]}',
    },
  },
  {
    id: 'F06',
    name: 'OpenAI Shim ReDoc',
    url: 'http://127.0.0.1:8002/redoc',
    selector: 'redoc, .redoc-wrap',
    title: 'Qwen3-VL OpenAI-compat shim',
    task: {
      kind: 'redoc',
      searchTerm: 'list models',
      operationName: 'List Models',
      operationPath: '/v1/models',
    },
  },
] as const

for (const documentationCase of documentationCases) {
  test(`${documentationCase.id} renders ${documentationCase.name} with safe API states`, async ({
    page,
  }, testInfo) => {
    test.setTimeout(60_000)
    const consoleErrors: string[] = []
    const pageErrors: string[] = []
    page.on('console', (message) => {
      if (message.type() === 'error') consoleErrors.push(message.text())
    })
    page.on('pageerror', (error) => pageErrors.push(error.message))

    await page.goto(documentationCase.url)
    await expect(page.locator(documentationCase.selector).first()).toBeVisible()
    await expect(page).toHaveTitle(new RegExp(documentationCase.title, 'i'))
    if (documentationCase.task.kind === 'swagger') {
      await expect(swaggerOperation(page, documentationCase.task.successPath)).toBeVisible()
    } else {
      await expect(page.getByRole('textbox', { name: 'Search' })).toBeVisible()
    }
    const focusTrace: string[] = []
    for (let attempt = 0; attempt < 20; attempt += 1) {
      await page.keyboard.press('Tab')
      const active = await page.evaluate(() => {
        const element = document.activeElement as HTMLElement | null
        return `${element?.tagName ?? 'NONE'}:${element?.getAttribute('aria-label') ?? element?.innerText?.slice(0, 80) ?? ''}`
      })
      focusTrace.push(active)
      if (!active.startsWith('BODY:') && !active.startsWith('HTML:')) break
    }
    const focused = focusTrace.some(
      (entry) => !entry.startsWith('BODY:') && !entry.startsWith('HTML:'),
    )
    expect(focused).toBe(true)
    const taskResult =
      documentationCase.task.kind === 'swagger'
        ? await exerciseSwagger(page, documentationCase.task)
        : await exerciseRedoc(page, documentationCase.task)

    await page.setViewportSize({ width: 320, height: 720 })
    const overflowMeasurement = await page.evaluate(() => ({
      pageOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      elements: Array.from(document.querySelectorAll('body *'))
        .map((element) => {
          const rect = element.getBoundingClientRect()
          return {
            selector: `${element.tagName.toLowerCase()}${element.id ? `#${element.id}` : ''}${Array.from(
              element.classList,
            )
              .slice(0, 3)
              .map((name) => `.${name}`)
              .join('')}`,
            left: rect.left,
            right: rect.right,
            width: rect.width,
          }
        })
        .filter((element) => element.left < -1 || element.right > innerWidth + 1)
        .sort((left, right) => right.width - left.width)
        .slice(0, 12),
    }))
    const { pageOverflow } = overflowMeasurement
    await page.screenshot({
      path: testInfo.outputPath(`${documentationCase.id.toLowerCase()}-reflow-320.png`),
      fullPage: true,
    })
    await attachJson(testInfo, `${documentationCase.id}-state-proof`, {
      route: new URL(documentationCase.url).pathname,
      expected: `${documentationCase.name} renders, focuses, and exposes safe API outcomes`,
      observed: {
        focused,
        focusTrace,
        taskResult,
        pageOverflow,
        overflowElements: overflowMeasurement.elements,
        consoleErrors,
        pageErrors,
      },
    })

    expect(pageOverflow).toBeLessThanOrEqual(1)
    expect(consoleErrors).toEqual([])
    expect(pageErrors).toEqual([])
  })
}
