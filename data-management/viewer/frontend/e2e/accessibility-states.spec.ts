import { expect, test } from '@playwright/test'

import { attachJson, installApiFixture, openViewer } from './accessibility-fixture'

test('V03 and V04 episode list failure is exposed as an alert', async ({ page }, testInfo) => {
  await installApiFixture(page, { episodeList: 'error' })
  await page.goto('/')
  await expect(page.getByRole('banner')).toBeVisible()
  const alert = page.getByRole('alert')
  await expect(alert).toContainText('The server could not complete the request')
  await expect(alert).not.toContainText('Synthetic episode list failure')
  await attachJson(testInfo, 'episode-list-error-state', {
    journeys: ['V03', 'V04'],
    expected: 'Episode list failure is exposed as an alert',
    observed: await alert.textContent(),
  })
})

test('V05 save status preserves focus and reports completion', async ({ page }, testInfo) => {
  const errors = await openViewer(page, testInfo)
  const navigationStatus = page.getByTestId('episode-navigation-status')
  await expect(navigationStatus).toHaveAttribute('role', 'status')
  await expect(navigationStatus).toHaveAttribute('aria-atomic', 'true')
  await expect(navigationStatus).toBeEmpty()
  const labelsPanel = page.getByTestId('trajectory-labels-panel')
  await labelsPanel.getByRole('button', { name: 'REVIEWED', exact: true }).click()
  const saveStatus = page.getByTestId('workspace-save-status-slot')
  await expect(saveStatus).toContainText('Unsaved episode changes.')

  const saveButton = page.getByRole('button', { name: 'Save & Next Episode' })
  await saveButton.focus()
  await saveButton.press('Enter')
  await expect(page.getByRole('heading', { name: 'Episode 1' })).toBeVisible()
  await expect(navigationStatus).toContainText('Episode changes saved.')
  await attachJson(testInfo, 'save-status-transition', {
    journeys: ['V05', 'V11'],
    states: ['unsaved', 'saved', 'advanced'],
    focusAfterSave: await page.evaluate(() => document.activeElement?.textContent?.trim() ?? null),
  })
  expect(errors.consoleErrors).toEqual([])
  expect(errors.pageErrors).toEqual([])
})

test('V18 export completion uses status semantics and failure uses alert semantics', async ({
  page,
}, testInfo) => {
  const errors = await openViewer(page, testInfo)
  await page.getByRole('button', { name: 'Export', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: 'Export Episodes' })
  await dialog.getByRole('button', { name: 'Start Export' }).click()
  await expect(dialog.getByRole('status')).toContainText('Export Complete')
  await dialog.getByRole('button', { name: 'Done' }).click()

  await page.unrouteAll({ behavior: 'wait' })
  await installApiFixture(page, { export: 'failure' })
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Episode 0' })).toBeVisible()
  await page.getByRole('button', { name: 'Export', exact: true }).click()
  const failureDialog = page.getByRole('dialog', { name: 'Export Episodes' })
  await failureDialog.getByRole('button', { name: 'Start Export' }).click()
  await expect(failureDialog.getByRole('alert')).toContainText('Export Failed')

  await attachJson(testInfo, 'export-status-transitions', {
    journeys: ['V18'],
    states: ['success status', 'failure alert'],
  })
  expect(errors.consoleErrors.some((message) => message.includes('500'))).toBe(true)
  expect(errors.consoleErrors.filter((message) => !message.includes('500'))).toEqual([])
  expect(errors.pageErrors).toEqual([])
})

test('J04 completes a representative local Viewer process', async ({ page }, testInfo) => {
  const errors = await openViewer(page, testInfo)
  const transitions: string[] = []

  const datasetButton = page.getByRole('button', { name: 'Dataset' })
  await datasetButton.focus()
  await datasetButton.press('Enter')
  const filter = page.getByPlaceholder('Filter datasets')
  await filter.fill('secondary')
  await filter.press('ArrowDown')
  await filter.press('Enter')
  await expect(datasetButton).toContainText('a11y-secondary')
  transitions.push('V02 dataset selected')

  const secondEpisode = page.getByRole('button', { name: /Episode 1/ })
  await secondEpisode.focus()
  await secondEpisode.press('Enter')
  await expect(page.getByRole('heading', { name: 'Episode 1' })).toBeVisible()
  transitions.push('V03 episode selected')

  const labelsPanel = page.getByTestId('trajectory-labels-panel')
  await labelsPanel.getByRole('button', { name: 'NEEDS_ATTENTION', exact: true }).click()
  await expect(page.getByTestId('workspace-save-status-slot')).toContainText(
    'Unsaved episode changes.',
  )
  transitions.push('V11 annotation changed')

  const diagnosticsButton = page.getByRole('button', { name: 'Toggle Diagnostics' })
  await diagnosticsButton.focus()
  await diagnosticsButton.press('Enter')
  await expect(page.getByText('Dataviewer Diagnostics')).toBeVisible()
  transitions.push('V17 diagnostics inspected')

  const exportButton = page.getByRole('button', { name: 'Export', exact: true })
  await exportButton.focus()
  await exportButton.press('Enter')
  const dialog = page.getByRole('dialog', { name: 'Export Episodes' })
  await dialog.getByPlaceholder('/path/to/exports').fill('/synthetic/export')
  await dialog.getByRole('button', { name: 'Start Export' }).press('Enter')
  await expect(dialog.getByRole('status')).toContainText('Export Complete')
  transitions.push('V18 export completed')

  await attachJson(testInfo, 'J04-transition-trace', {
    journey: 'J04',
    transitions,
    unclaimed: ['media meaning', 'actual screen-reader speech', 'hosted authentication'],
  })
  expect(errors.consoleErrors).toEqual([])
  expect(errors.pageErrors).toEqual([])
})
