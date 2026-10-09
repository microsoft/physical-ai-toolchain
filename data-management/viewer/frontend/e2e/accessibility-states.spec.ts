import { expect, test } from '@playwright/test'

import { attachJson, installApiFixture, openViewer } from './accessibility-fixture'

test('P06 real backend persists labels and rejects stale writes', async ({ request }) => {
  const dataset = 'http://127.0.0.1:18000/api/datasets/integration-fixture'
  expect((await request.get(`${dataset}/episodes/0`)).ok()).toBe(true)
  const csrf = await (await request.get('http://127.0.0.1:18000/api/csrf-token')).json()
  const saved = await request.get(`${dataset}/labels`)
  const etag = saved.headers().etag
  const created = await request.put(`${dataset}/episodes/0/labels`, {
    headers: {
      'X-CSRF-Token': csrf.csrf_token,
      ...(etag ? { 'If-Match': etag } : { 'If-None-Match': '*' }),
    },
    data: { labels: ['SUCCESS'], intent: 'human-edit' },
  })
  expect(created.ok()).toBe(true)
  expect((await (await request.get(`${dataset}/labels`)).json()).episodes['0']).toEqual(['SUCCESS'])
  const stale = await request.put(`${dataset}/episodes/0/labels`, {
    headers: { 'X-CSRF-Token': csrf.csrf_token, 'If-Match': '"stale"' },
    data: { labels: [], intent: 'human-edit' },
  })
  expect(stale.status()).toBe(412)
  const cleared = await request.put(`${dataset}/episodes/0/labels`, {
    headers: { 'X-CSRF-Token': csrf.csrf_token, 'If-Match': created.headers().etag },
    data: { labels: [], intent: 'human-edit' },
  })
  expect(cleared.ok()).toBe(true)
  expect((await (await request.get(`${dataset}/labels`)).json()).episodes['0']).toEqual([])
})

test('P06 fresh browser recovers a saved label change without browser drafts', async ({
  browser,
  request,
}) => {
  const dataset = 'http://127.0.0.1:18000/api/datasets/integration-fixture'
  const csrf = await (await request.get('http://127.0.0.1:18000/api/csrf-token')).json()
  const saved = await request.get(`${dataset}/labels`)
  const etag = saved.headers().etag
  const seed = await request.put(`${dataset}/episodes/0/labels`, {
    headers: {
      'X-CSRF-Token': csrf.csrf_token,
      ...(etag ? { 'If-Match': etag } : { 'If-None-Match': '*' }),
    },
    data: { labels: ['SUCCESS'], intent: 'human-edit' },
  })
  expect(seed.ok()).toBe(true)
  const context = await browser.newContext()
  try {
    const page = await context.newPage()
    await page.goto('http://127.0.0.1:4173/?dataset=integration-fixture')
    const label = page
      .getByTestId('trajectory-labels-panel')
      .getByRole('button', { name: 'SUCCESS', exact: true })
    await expect(label).toHaveAttribute('aria-pressed', 'true')
    await label.click()
    await page.getByRole('button', { name: 'Save Episode', exact: true }).click()
    await expect
      .poll(async () => (await (await request.get(`${dataset}/labels`)).json()).episodes['0'])
      .toEqual([])
  } finally {
    await context.close()
  }
  const freshContext = await browser.newContext()
  try {
    const page = await freshContext.newPage()
    await page.goto('http://127.0.0.1:4173/?dataset=integration-fixture')
    await expect(
      page
        .getByTestId('trajectory-labels-panel')
        .getByRole('button', { name: 'SUCCESS', exact: true }),
    ).toHaveAttribute('aria-pressed', 'false')
    await expect(page.getByTestId('workspace-save-status-slot')).not.toContainText('Unsaved')
  } finally {
    await freshContext.close()
  }
})

test('V07 joint defaults conflict retains the draft and keyboard focus with an alert', async ({
  page,
}, testInfo) => {
  await openViewer(page, testInfo, { trajectoryVariables: 'joints', jointConfig: 'conflict' })
  const openDefaults = page.getByRole('button', { name: /defaults/i })
  await openDefaults.focus()
  await openDefaults.press('Enter')
  const dialog = page.getByRole('dialog', { name: 'Joint Configuration Defaults' })
  await expect(dialog).toBeVisible()
  const editLabel = dialog.getByRole('button', { name: 'Edit joint label' }).first()
  await editLabel.focus()
  await editLabel.press('Enter')
  const input = dialog.getByRole('textbox')
  await input.fill('Draft shoulder')
  await input.press('Enter')
  const save = dialog.getByRole('button', { name: 'Save', exact: true })
  await save.focus()
  const requestPromise = page.waitForRequest(
    (request) => request.method() === 'PUT' && request.url().endsWith('/joint-config/defaults'),
  )
  await save.press('Enter')
  const request = await requestPromise
  expect(request.headers()['if-match']).toBe('"settings-baseline"')
  await expect(dialog.getByRole('alert')).toContainText('Joint defaults were not saved.')
  await expect(dialog.getByText('Draft shoulder', { exact: true })).toBeVisible()
  await expect(save).toBeFocused()
  await dialog.press('Escape')
  await expect(dialog).not.toBeVisible()
  await expect(openDefaults).toBeFocused()
})

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
  await expect(saveStatus).toContainText('Unsaved episode changes')

  const saveButton = page.getByRole('button', { name: 'Save Episode', exact: true })
  await saveButton.focus()
  await saveButton.press('Enter')
  await expect(page.getByRole('heading', { name: 'Episode 0' })).toBeVisible()
  await expect(saveStatus).toContainText('Episode changes saved.')
  await expect(saveButton).toBeFocused()
  await expect(navigationStatus).toBeEmpty()
  await page.getByRole('button', { name: 'Next Episode', exact: true }).press('Enter')
  await expect(page.getByRole('heading', { name: 'Episode 1' })).toBeVisible()
  await expect(navigationStatus).not.toContainText('saved')
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
    'Unsaved episode changes (labels)',
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
