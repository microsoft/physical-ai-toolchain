import { readdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'

import {
  attachJson,
  openEmptyViewer,
  openViewer,
  riskComponentRegistry,
} from './accessibility-fixture'

async function expectNoAxeViolations(
  page: Parameters<typeof AxeBuilder>[0]['page'],
  testInfo: Parameters<typeof attachJson>[0],
  name: string,
  journeys: string[],
) {
  const results = await new AxeBuilder({ page }).analyze()
  await attachJson(testInfo, `axe-${name}`, {
    journeys,
    violations: results.violations,
    incomplete: results.incomplete,
  })
  expect(results.violations).toEqual([])
}

test('V02 through V18 loaded workspace has no axe violations', async ({ page }, testInfo) => {
  const errors = await openViewer(page, testInfo)
  await expectNoAxeViolations(page, testInfo, 'loaded-workspace', [
    'V02',
    'V03',
    'V05',
    'V06',
    'V08',
    'V13',
    'V14',
    'V18',
  ])
  expect(errors.consoleErrors).toEqual([])
  expect(errors.pageErrors).toEqual([])
})

test('V02 V03 and V04 empty shell has no axe violations', async ({ page }, testInfo) => {
  const errors = await openEmptyViewer(page, testInfo)
  await expectNoAxeViolations(page, testInfo, 'empty-shell', ['V02', 'V03', 'V04'])
  expect(errors.consoleErrors).toEqual([])
  expect(errors.pageErrors).toEqual([])
})

test('V05 and V15 analyzer state has no axe violations', async ({ page }, testInfo) => {
  const errors = await openViewer(page, testInfo)
  await page.getByRole('tab', { name: 'Episode Analyzer' }).click()
  await expect(page.getByRole('heading', { name: 'Automated Episode Analysis' })).toBeVisible()
  await attachJson(testInfo, 'state-proof-analyzer', {
    journeyIds: ['V05', 'V15'],
    expected: 'Analyzer tab is selected and rendered',
    observed: 'Automated Episode Analysis heading is visible',
  })
  await expectNoAxeViolations(page, testInfo, 'analyzer', ['V05', 'V15'])
  expect(errors.consoleErrors).toEqual([])
  expect(errors.pageErrors).toEqual([])
})

test('V02 dataset chooser has no axe violations', async ({ page }, testInfo) => {
  const errors = await openViewer(page, testInfo)
  await page.getByRole('button', { name: 'Dataset' }).click()
  await expect(page.getByRole('dialog', { name: 'Select dataset' })).toBeVisible()
  await attachJson(testInfo, 'state-proof-dataset-open', {
    journeyIds: ['V02'],
    expected: 'Dataset chooser is open and focused',
    observed: 'Select dataset dialog is visible',
  })
  await expectNoAxeViolations(page, testInfo, 'dataset-open', ['V02'])
  expect(errors.consoleErrors).toEqual([])
  expect(errors.pageErrors).toEqual([])
})

test('V18 export dialog has no axe violations', async ({ page }, testInfo) => {
  const errors = await openViewer(page, testInfo)
  await page.getByRole('button', { name: 'Export', exact: true }).click()
  await expect(page.getByRole('dialog', { name: 'Export Episodes' })).toBeVisible()
  await attachJson(testInfo, 'state-proof-export-open', {
    journeyIds: ['V18'],
    expected: 'Export dialog is open with initial focus',
    observed: 'Export Episodes dialog is visible',
  })
  await expectNoAxeViolations(page, testInfo, 'export-open', ['V18'])
  expect(errors.consoleErrors).toEqual([])
  expect(errors.pageErrors).toEqual([])
})

test('V02 V05 and V18 expose exact page and component semantics', async ({ page }, testInfo) => {
  const errors = await openViewer(page, testInfo)

  await expect(page).toHaveTitle('Robotic Training Data Analysis Tool')
  await expect(page.locator('html')).toHaveAttribute('lang', 'en')
  await expect(page.getByRole('banner')).toBeVisible()
  await expect(page.getByRole('complementary')).toBeVisible()
  await expect(page.getByRole('main')).toBeVisible()
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Robotic Training Data Analysis')
  await expect(page.getByRole('heading', { level: 2 })).toHaveText('Episode 0')

  const datasetButton = page.getByRole('button', { name: 'Dataset' })
  await expect(datasetButton).toHaveAttribute('aria-expanded', 'false')
  await datasetButton.click()
  await expect(datasetButton).toHaveAttribute('aria-expanded', 'true')
  const datasetFilter = page.getByRole('combobox', { name: 'Filter datasets' })
  const controlledListboxId = await datasetFilter.getAttribute('aria-controls')
  expect(controlledListboxId).toBeTruthy()
  await expect(page.locator(`#${controlledListboxId}`)).toHaveRole('listbox')
  await expect(page.locator(`#${controlledListboxId}`)).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(datasetButton).toHaveAttribute('aria-expanded', 'false')

  const trajectoryTab = page.getByRole('tab', { name: 'Trajectory Viewer' })
  const analyzerTab = page.getByRole('tab', { name: 'Episode Analyzer' })
  await expect(trajectoryTab).toHaveAttribute('aria-selected', 'true')
  await analyzerTab.click()
  await expect(analyzerTab).toHaveAttribute('aria-selected', 'true')
  await expect(trajectoryTab).toHaveAttribute('aria-selected', 'false')

  const diagnosticsButton = page.getByRole('button', { name: 'Toggle Diagnostics' })
  await expect(diagnosticsButton).toHaveAttribute('aria-pressed', 'false')
  await diagnosticsButton.click()
  await expect(diagnosticsButton).toHaveAttribute('aria-pressed', 'true')

  await trajectoryTab.click()
  const sliders = page.getByRole('slider')
  expect(await sliders.count()).toBeGreaterThan(0)
  for (let index = 0; index < (await sliders.count()); index += 1) {
    await expect(sliders.nth(index)).not.toHaveAccessibleName('')
  }

  await attachJson(testInfo, 'semantic-state-contract', {
    journeys: ['V02', 'V05', 'V18'],
    asserted: [
      'page metadata',
      'landmarks',
      'headings',
      'expanded state',
      'tabs',
      'pressed state',
      'slider names',
    ],
  })
  expect(errors.consoleErrors).toEqual([])
  expect(errors.pageErrors).toEqual([])
})

function sourceFiles(root: string): string[] {
  return readdirSync(root, { withFileTypes: true }).flatMap((entry) => {
    const path = join(root, entry.name)
    if (entry.isDirectory()) return entry.name === '__tests__' ? [] : sourceFiles(path)
    return /\.tsx?$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name) ? [path] : []
  })
}

test('V02 through V19 accessibility-risk source classes have explicit ownership', async () => {
  const componentsRoot = join(dirname(fileURLToPath(import.meta.url)), '..', 'src', 'components')
  const registeredIds = riskComponentRegistry.map((entry) => entry.id)
  const registeredSources = riskComponentRegistry.flatMap((entry) => entry.sourcePaths)

  expect(new Set(registeredIds).size).toBe(registeredIds.length)
  expect(riskComponentRegistry.filter((entry) => !entry.owner || !entry.mappedEvidence)).toEqual([])
  expect(riskComponentRegistry.filter((entry) => entry.sourcePaths.length === 0)).toEqual([])
  expect(
    registeredSources.filter(
      (sourcePath) => !sourceFiles(componentsRoot).includes(join(componentsRoot, sourcePath)),
    ),
  ).toEqual([])
})
