import { randomUUID } from 'node:crypto'
import { cpSync } from 'node:fs'
import { join } from 'node:path'

import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'

import { attachJson, installApiFixture, openViewer } from './accessibility-fixture'

test('P06 job controls retain progress through refresh failure and keyboard cancel retry', async ({
  page,
}, testInfo) => {
  const job = {
    id: 'browser-control-job',
    dataset_id: 'integration-fixture',
    mode: 'judge',
    status: 'running',
    total: 2,
    judged: 1,
    applied: 0,
    errors: 0,
    config: {},
  }
  const actions: string[] = []
  let failRefresh = false
  await page.route('**/api/judge/jobs**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (request.method() === 'POST') {
      const action = path.split('/').at(-1)!
      expect(['cancel', 'retry']).toContain(action)
      expect(request.headers()['x-csrf-token']).toBeTruthy()
      actions.push(action)
      job.status = action === 'cancel' ? 'cancelled' : 'running'
      job.errors = 0
      await route.fulfill({ json: job })
      return
    }
    await route.fulfill({
      status: failRefresh ? 503 : 200,
      json: failRefresh ? { detail: 'Temporary job read failure' } : { items: [job], total: 1 },
    })
  })
  await page.goto('http://127.0.0.1:4173/?dataset=integration-fixture')
  const disclosure = page.getByRole('button', { name: /Dataset workspace/ })
  await disclosure.click()
  const workspace = page.getByRole('region', { name: 'Dataset workspace', exact: true })
  const jobs = workspace.getByRole('region', { name: 'Durable jobs' })
  await expect(jobs).toContainText('1 judged')
  await expect(jobs).toContainText('0 applied')
  await page.setViewportSize({ width: 320, height: 800 })
  const cancel = jobs.getByRole('button', { name: `Cancel job ${job.id}` })
  await cancel.focus()
  await expect(cancel).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(jobs).toContainText('cancelled')
  expect(actions).toEqual(['cancel'])
  failRefresh = true
  const refresh = workspace.getByRole('button', { name: 'Refresh dataset jobs' })
  await refresh.focus()
  await page.keyboard.press('Enter')
  await expect(jobs.getByRole('status')).toContainText('Previously loaded jobs remain visible')
  await expect(jobs).toContainText('1 judged')
  await expect(refresh).toBeFocused()
  failRefresh = false
  job.status = 'failed'
  job.errors = 1
  await page.keyboard.press('Enter')
  await expect(jobs).toContainText('1 errors')
  await expect(jobs.getByRole('status')).toHaveCount(0)
  const retry = jobs.getByRole('button', { name: `Retry job ${job.id}` })
  await retry.focus()
  await page.keyboard.press('Enter')
  await expect(jobs).toContainText('running')
  expect(actions).toEqual(['cancel', 'retry'])
  await page.reload()
  await disclosure.click()
  await expect(jobs).toContainText('running')
  await expect(jobs).toContainText('1 judged')
  await expect(cancel).toBeEnabled()
  job.status = 'succeeded'
  job.judged = 2
  await refresh.focus()
  await page.keyboard.press('Enter')
  await expect(jobs).toContainText('2 judged')
  await expect(jobs).toContainText('succeeded')
  await expect(cancel).toHaveCount(0)
  await expect(retry).toHaveCount(0)
  expect(actions).toEqual(['cancel', 'retry'])
  await expect(refresh).toBeFocused()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  const accessibility = await new AxeBuilder({ page })
    .include('[aria-label="Durable jobs"]')
    .analyze()
  expect(accessibility.violations).toEqual([])
  await jobs.scrollIntoViewIfNeeded()
  await page.screenshot({ path: testInfo.outputPath('job-controls-320px.png') })
})

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

test('P06 fresh browser recovers complete saved edits and explicit clearing', async ({
  browser,
  request,
}) => {
  const endpoint = 'http://127.0.0.1:18000/api/datasets/integration-fixture/episodes/0/edits'
  const csrf = await (await request.get('http://127.0.0.1:18000/api/csrf-token')).json()
  const baseline = await request.get(endpoint)
  const identity = await baseline.json()
  const operations = {
    datasetId: 'integration-fixture',
    episodeIndex: 0,
    globalTransform: { resize: { width: 32, height: 24 }, colorFilter: 'grayscale' },
    cameraTransforms: { 'obs.front': { crop: { x: 0, y: 0, width: 32, height: 24 } } },
    removedFrames: [3],
    insertedFrames: [{ afterFrameIndex: 5, interpolationFactor: 0.5 }],
    trajectoryAdjustments: [{ frameIndex: 1, rightArmDelta: [0.01, 0, 0] }],
    subtasks: [
      {
        id: 'saved-segment',
        label: 'Saved segment',
        frameRange: [0, 2],
        color: '#112233',
        source: 'manual',
      },
    ],
  }
  const saved = await request.put(endpoint, {
    headers: {
      'X-CSRF-Token': csrf.csrf_token,
      ...(baseline.headers().etag
        ? { 'If-Match': baseline.headers().etag }
        : { 'If-None-Match': '*' }),
    },
    data: { source_id: identity.source_id, source_revision: identity.source_revision, operations },
  })
  expect(saved.ok()).toBe(true)
  const context = await browser.newContext()
  try {
    const page = await context.newPage()
    await page.goto('http://127.0.0.1:4173/?dataset=integration-fixture')
    await expect(page.getByText('Saved segment', { exact: true }).first()).toBeVisible()
    await expect(page.getByTestId('workspace-save-status-slot')).not.toContainText('Unsaved')
    const recovered = await page.request.get(endpoint)
    expect((await recovered.json()).saved.operations).toMatchObject(operations)
  } finally {
    await context.close()
  }
  const cleared = await request.put(endpoint, {
    headers: { 'X-CSRF-Token': csrf.csrf_token, 'If-Match': saved.headers().etag },
    data: {
      source_id: identity.source_id,
      source_revision: identity.source_revision,
      operations: { datasetId: 'integration-fixture', episodeIndex: 0 },
    },
  })
  expect(cleared.ok()).toBe(true)
  const freshContext = await browser.newContext()
  try {
    const page = await freshContext.newPage()
    await page.goto('http://127.0.0.1:4173/?dataset=integration-fixture')
    await expect(page.getByRole('button', { name: 'Save Episode', exact: true })).toBeEnabled()
    await expect(page.getByText('Saved segment', { exact: true })).toHaveCount(0)
    const recovered = await page.request.get(endpoint)
    expect((await recovered.json()).saved.operations.subtasks).toBeNull()
  } finally {
    await freshContext.close()
  }
})

test('P06 real backend recovers an unsaved label after an episode read failure', async ({
  page,
  request,
}) => {
  const dataset = 'http://127.0.0.1:18000/api/datasets/integration-fixture'
  await page.clock.setFixedTime(new Date())
  let failRead = true
  await page.route('**/api/datasets/integration-fixture/episodes/0', async (route) => {
    if (failRead) {
      await route.fulfill({ status: 503, json: { detail: 'Synthetic episode read failure' } })
    } else {
      await route.continue()
    }
  })
  await page.goto('http://127.0.0.1:4173/?dataset=integration-fixture')
  await expect(page.getByRole('alert')).toContainText('Error loading episode', { timeout: 20_000 })
  expect((await (await request.get(`${dataset}/labels`)).json()).episodes['0'] ?? []).toEqual([])
  failRead = false
  const retry = page.getByRole('button', { name: 'Retry episode load' })
  await retry.focus()
  await retry.press('Enter')
  const label = page
    .getByTestId('trajectory-labels-panel')
    .getByRole('button', { name: 'FAILURE', exact: true })
  await expect(label).toHaveAttribute('aria-pressed', 'false')
  await label.click()
  await expect(page.getByTestId('workspace-save-status-slot')).toContainText('Unsaved')
  expect((await (await request.get(`${dataset}/labels`)).json()).episodes['0'] ?? []).toEqual([])
  failRead = true
  await page.clock.setFixedTime(new Date(Date.now() + 60_000))
  const cancelledNavigation = page.waitForEvent('dialog')
  page.once('dialog', (dialog) => dialog.dismiss())
  await page.getByRole('button', { name: 'Next Episode', exact: true }).click()
  const cancelledDialog = await cancelledNavigation
  expect(cancelledDialog.type()).toBe('confirm')
  expect(cancelledDialog.message()).toContain('Leave with unsaved episode changes')
  await expect(page.getByRole('heading', { name: 'Episode 0', exact: true })).toBeVisible()
  await expect(label).toHaveAttribute('aria-pressed', 'true')
  const acceptedNavigation = page.waitForEvent('dialog')
  page.once('dialog', (dialog) => dialog.accept())
  await page.getByRole('button', { name: /Episode 1/ }).click()
  expect((await acceptedNavigation).type()).toBe('confirm')
  await expect(page.getByRole('heading', { name: 'Episode 1', exact: true })).toBeVisible()
  await page.getByRole('button', { name: /Episode 0/ }).click()
  await expect(
    page.getByRole('status').filter({ hasText: 'Could not refresh episode' }),
  ).toBeVisible({ timeout: 20_000 })
  await expect(label).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByRole('button', { name: 'Save Episode', exact: true })).toBeEnabled()
  failRead = false
  await page.getByRole('button', { name: 'Retry episode load' }).press('Enter')
  await expect(
    page.getByText('Could not refresh episode. Showing previously loaded data.'),
  ).toHaveCount(0)
  await expect(label).toHaveAttribute('aria-pressed', 'true')
  failRead = true
  await page.reload()
  await expect(page.getByRole('alert')).toContainText('Error loading episode', { timeout: 20_000 })
  failRead = false
  await page.getByRole('button', { name: 'Retry episode load' }).press('Enter')
  await expect(label).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByTestId('workspace-save-status-slot')).toContainText('Unsaved')
  await page.getByRole('button', { name: 'Save Episode', exact: true }).click()
  await expect
    .poll(async () => (await (await request.get(`${dataset}/labels`)).json()).episodes['0'])
    .toEqual(['FAILURE'])
  await label.click()
  await page.getByRole('button', { name: 'Save Episode', exact: true }).click()
  await expect
    .poll(async () => (await (await request.get(`${dataset}/labels`)).json()).episodes['0'])
    .toEqual([])
})

test('P06 real echo judge displays evidence without writes and withdraws explicit application', async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(60_000)
  await page.clock.setFixedTime(new Date())
  const backend = 'http://127.0.0.1:18000'
  const datasetId = `integration-judge-${randomUUID()}`
  const dataDirectory = testInfo.config.metadata.dataDirectory as string
  cpSync(join(dataDirectory, 'integration-judge-fixture'), join(dataDirectory, datasetId), {
    recursive: true,
  })
  expect((await request.get(`${backend}/api/datasets?refresh=true`)).ok()).toBe(true)
  const dataset = `${backend}/api/datasets/${datasetId}`
  expect((await (await request.get(`${dataset}/capabilities`)).json()).vlm_judge_enabled).toBe(true)
  const labelsBefore = (await (await request.get(`${dataset}/labels`)).json()).episodes
  const resultsUrl = `${backend}/api/judge/results?dataset_id=${datasetId}&episode_index=0`
  expect((await (await request.get(resultsUrl)).json()).items).toEqual([])
  await page.goto(`http://127.0.0.1:4173/?dataset=${datasetId}`)
  await expect(page.getByRole('heading', { name: 'Episode 0', exact: true })).toBeVisible()
  await page.getByRole('tab', { name: 'Episode Analyzer' }).click()
  const runJudge = page.getByRole('button', { name: 'Run judge', exact: true })
  await expect(runJudge).toBeEnabled()
  await runJudge.click()
  const assessment = page.getByRole('region', { name: 'Judge assessment' })
  await expect(assessment).toContainText('current', { timeout: 20_000 })
  expect((await (await request.get(`${dataset}/labels`)).json()).episodes).toEqual(labelsBefore)
  const evidence = (await (await request.get(resultsUrl)).json()).items
  expect(evidence).toHaveLength(1)
  await page.reload()
  await page.getByRole('tab', { name: 'Episode Analyzer' }).click()
  await expect(assessment).toContainText('current')
  expect((await (await request.get(resultsUrl)).json()).items).toEqual(evidence)
  await page.getByRole('tab', { name: 'Trajectory Viewer' }).click()
  const draftLabel = page
    .getByTestId('trajectory-labels-panel')
    .getByRole('button', { name: 'FAILURE', exact: true })
  await draftLabel.click()
  await expect(page.getByTestId('workspace-save-status-slot')).toContainText('Unsaved')
  await page.getByRole('tab', { name: 'Episode Analyzer' }).click()
  let failEvidence = true
  await page.route('**/api/judge/results?**', async (route) => {
    if (failEvidence)
      await route.fulfill({ status: 503, json: { detail: 'Synthetic evidence read failure' } })
    else await route.continue()
  })
  await page.clock.setFixedTime(new Date(Date.now() + 600_000))
  await page.evaluate(() => {
    window.dispatchEvent(new Event('offline'))
    window.dispatchEvent(new Event('online'))
  })
  await expect(page.getByRole('button', { name: 'Retry evidence', exact: true })).toBeVisible()
  failEvidence = false
  await page.getByRole('button', { name: 'Retry evidence', exact: true }).press('Enter')
  await expect(page.getByRole('button', { name: 'Retry evidence', exact: true })).toHaveCount(0)
  await page.getByRole('tab', { name: 'Trajectory Viewer' }).click()
  await expect(draftLabel).toHaveAttribute('aria-pressed', 'true')
  page.once('dialog', (dialog) => dialog.accept())
  await page.getByRole('button', { name: 'Discard changes', exact: true }).click()
  await expect(page.getByTestId('workspace-save-status-slot')).not.toContainText('Unsaved')
  await page.getByRole('tab', { name: 'Episode Analyzer' }).click()
  expect((await (await request.get(resultsUrl)).json()).items).toEqual(evidence)
  await page.setViewportSize({ width: 320, height: 800 })
  await assessment.scrollIntoViewIfNeeded()
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth),
  ).toBeLessThanOrEqual(1)
  expect(
    (await new AxeBuilder({ page }).include('[aria-label="Judge assessment"]').analyze())
      .violations,
  ).toEqual([])
  await testInfo.attach('judge-320px', {
    body: await page.screenshot({ path: testInfo.outputPath('judge-320px.png') }),
    contentType: 'image/png',
  })
  await assessment.getByRole('button', { name: /Apply.*label/i }).press('Enter')
  await expect
    .poll(async () => (await (await request.get(`${dataset}/labels`)).json()).episodes['0'])
    .toContain('SUCCESS')
  await page.getByRole('button', { name: 'Dataset workspace', exact: true }).click()
  await page.getByRole('button', { name: 'Preview label removal', exact: true }).press('Enter')
  await page.getByRole('checkbox', { name: 'I reviewed this removal preview' }).check()
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth),
  ).toBeLessThanOrEqual(1)
  expect(
    (await new AxeBuilder({ page }).include('[aria-label="Dataset workspace"]').analyze())
      .violations,
  ).toEqual([])
  await testInfo.attach('withdrawal-320px', {
    body: await page.screenshot({ path: testInfo.outputPath('withdrawal-320px.png') }),
    contentType: 'image/png',
  })
  await page.setViewportSize({ width: 1280, height: 800 })
  await page.addStyleTag({
    content:
      'html { font-size: 200% !important; } * { line-height: 1.5 !important; letter-spacing: 0.12em !important; word-spacing: 0.16em !important; }',
  })
  await page
    .getByRole('button', { name: 'Confirm label removal', exact: true })
    .scrollIntoViewIfNeeded()
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth),
  ).toBeLessThanOrEqual(1)
  await testInfo.attach('withdrawal-enlarged-text', {
    body: await page.screenshot({ path: testInfo.outputPath('withdrawal-enlarged-text.png') }),
    contentType: 'image/png',
  })
  await page.getByRole('button', { name: 'Confirm label removal', exact: true }).press('Enter')
  await expect
    .poll(async () => (await (await request.get(`${dataset}/labels`)).json()).episodes['0'] ?? [])
    .toEqual([])
  await page.getByRole('button', { name: 'Return to episode', exact: true }).click()
  await page.getByRole('tab', { name: 'Episode Analyzer' }).click()
  await expect(assessment).toContainText('withdrawn')
  expect((await (await request.get(resultsUrl)).json()).items[0].applicability).toBe('withdrawn')
  await page.reload()
  await page.getByRole('tab', { name: 'Episode Analyzer' }).click()
  await expect(assessment).toContainText('withdrawn')
  expect((await (await request.get(`${dataset}/labels`)).json()).episodes['0'] ?? []).toEqual([])
  expect((await (await request.get(resultsUrl)).json()).items).toHaveLength(1)
  await page.getByRole('button', { name: /Episode 1/ }).click()
  await expect(page.getByRole('heading', { name: 'Episode 1', exact: true })).toBeVisible()
  await expect(runJudge).toBeEnabled()
  await runJudge.press('Enter')
  await expect(assessment).toContainText('current')
  const secondResultsUrl = resultsUrl.replace('episode_index=0', 'episode_index=1')
  const originalRun = (await (await request.get(secondResultsUrl)).json()).items[0].run_id
  expect(originalRun).toEqual(expect.any(String))
  await page.getByRole('tab', { name: 'Trajectory Viewer' }).click()
  await page.getByRole('button', { name: 'Add Instruction', exact: true }).click()
  await page.locator('#lang-instruction').fill('Changed saved instruction for stale evidence')
  await page.getByRole('button', { name: 'Save Episode', exact: true }).press('Enter')
  await expect(page.getByTestId('workspace-save-status-slot')).not.toContainText('Unsaved')
  await page.getByRole('tab', { name: 'Episode Analyzer' }).click()
  const staleEvidence = (await (await request.get(secondResultsUrl)).json()).items
  expect(staleEvidence).toHaveLength(1)
  expect(staleEvidence[0].run_id).toBe(originalRun)
  expect(staleEvidence[0].applicability).toBe('stale')
  await expect(assessment).toContainText('stale')
  await expect(assessment.getByRole('button', { name: /Apply.*label/i })).toBeDisabled()
  expect((await (await request.get(`${dataset}/labels`)).json()).episodes['1'] ?? []).toEqual([])
})

test('P06 real echo grounding blocks unmounted drafts before approval and separate targets', async ({
  page,
  request,
}) => {
  const backend = 'http://127.0.0.1:18000'
  const dataset = `${backend}/api/datasets/integration-grounding-fixture`
  const csrf = await (await request.get(`${backend}/api/csrf-token`)).json()
  const labels = await request.get(`${dataset}/labels`)
  const seeded = await request.put(`${dataset}/episodes/0/labels`, {
    headers: {
      'X-CSRF-Token': csrf.csrf_token,
      ...(labels.headers().etag ? { 'If-Match': labels.headers().etag } : { 'If-None-Match': '*' }),
    },
    data: { labels: ['SUCCESS'], intent: 'human-edit' },
  })
  expect(seeded.ok()).toBe(true)
  const submissions: string[] = []
  page.on('request', (outgoing) => {
    if (outgoing.method() === 'POST' && new URL(outgoing.url()).pathname === '/api/judge/jobs')
      submissions.push(outgoing.url())
  })
  await page.goto('http://127.0.0.1:4173/?dataset=integration-grounding-fixture')
  const label = page
    .getByTestId('trajectory-labels-panel')
    .getByRole('button', { name: 'SUCCESS', exact: true })
  await expect(label).toHaveAttribute('aria-pressed', 'true')
  await label.click()
  await expect(page.getByTestId('workspace-save-status-slot')).toContainText('Unsaved')
  page.once('dialog', (dialog) => dialog.accept())
  await page.getByRole('button', { name: /Episode 1/ }).click()
  await expect(page.getByRole('heading', { name: 'Episode 1', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Dataset workspace', exact: true }).click()
  await page.setViewportSize({ width: 320, height: 800 })
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth),
  ).toBeLessThanOrEqual(1)
  await page.getByRole('textbox', { name: 'Grounding episodes', exact: true }).fill('0')
  await page.getByRole('textbox', { name: 'Target episodes', exact: true }).fill('1')
  await expect(
    page.getByRole('button', { name: 'Evaluate grounding episodes', exact: true }),
  ).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Judge targets', exact: true })).toBeDisabled()
  expect(submissions).toEqual([])
  await page.reload()
  await expect(label).toHaveAttribute('aria-pressed', 'false')
  page.once('dialog', (dialog) => dialog.accept())
  await page.getByRole('button', { name: /Episode 1/ }).click()
  await expect(page.getByRole('heading', { name: 'Episode 1', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Dataset workspace', exact: true }).click()
  await page.getByRole('textbox', { name: 'Grounding episodes', exact: true }).fill('0')
  await expect(
    page.getByRole('button', { name: 'Evaluate grounding episodes', exact: true }),
  ).toBeDisabled()
  expect(submissions).toEqual([])
  await page.getByRole('button', { name: 'Return to episode', exact: true }).click()
  await page.getByRole('button', { name: /Episode 0/ }).click()
  await expect(label).toHaveAttribute('aria-pressed', 'false')
  await label.click()
  await page.getByRole('button', { name: 'Save Episode', exact: true }).click()
  await expect(page.getByTestId('workspace-save-status-slot')).not.toContainText('Unsaved')
  await page.getByRole('button', { name: 'Dataset workspace', exact: true }).click()
  await page.getByRole('textbox', { name: 'Grounding episodes', exact: true }).fill('0')
  await page.getByRole('textbox', { name: 'Target episodes', exact: true }).fill('1')
  await page
    .getByRole('button', { name: 'Evaluate grounding episodes', exact: true })
    .press('Enter')
  await expect(page.getByRole('heading', { name: 'Job review: succeeded' })).toBeVisible({
    timeout: 20_000,
  })
  await page
    .getByRole('checkbox', { name: 'I reviewed disagreements and inconclusive results' })
    .check()
  await page.getByRole('button', { name: 'Approve configuration', exact: true }).press('Enter')
  await expect(page.getByRole('button', { name: 'Judge targets', exact: true })).toBeEnabled()
  await page.getByRole('button', { name: 'Judge targets', exact: true }).press('Enter')
  const results = `${backend}/api/judge/results?dataset_id=integration-grounding-fixture&episode_index=1`
  await expect
    .poll(async () => (await (await request.get(results)).json()).items.length, { timeout: 20_000 })
    .toBe(1)
  expect(submissions).toHaveLength(2)
  const finalLabels = (await (await request.get(`${dataset}/labels`)).json()).episodes
  expect(finalLabels['0']).toEqual(['SUCCESS'])
  expect(finalLabels['1'] ?? []).toEqual([])
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth),
  ).toBeLessThanOrEqual(1)
})

test('P06 pending save and real label conflict block grounding until acknowledged save', async ({
  page,
  request,
}) => {
  const backend = 'http://127.0.0.1:18000'
  const dataset = `${backend}/api/datasets/integration-fixture`
  const csrf = await (await request.get(`${backend}/api/csrf-token`)).json()
  const baseline = await request.get(`${dataset}/labels`)
  const seed = await request.put(`${dataset}/episodes/0/labels`, {
    headers: {
      'X-CSRF-Token': csrf.csrf_token,
      ...(baseline.headers().etag
        ? { 'If-Match': baseline.headers().etag }
        : { 'If-None-Match': '*' }),
    },
    data: { labels: ['SUCCESS'], intent: 'human-edit' },
  })
  expect(seed.ok()).toBe(true)
  await page.goto('/?dataset=integration-fixture')
  const labels = page.getByTestId('trajectory-labels-panel')
  await expect(labels.getByRole('button', { name: 'SUCCESS', exact: true })).toHaveAttribute(
    'aria-pressed',
    'true',
  )
  await labels.getByRole('button', { name: 'SUCCESS', exact: true }).click()
  await labels.getByRole('button', { name: 'FAILURE', exact: true }).click()
  const gate = Promise.withResolvers<void>()
  await page.route('**/episodes/0/labels', async (route) => {
    await gate.promise
    await route.continue()
  })
  const saving = page.waitForRequest(
    (outgoing) => outgoing.method() === 'PUT' && outgoing.url().endsWith('/episodes/0/labels'),
  )
  await page.getByRole('button', { name: 'Save Episode', exact: true }).click()
  const outgoing = await saving
  expect(outgoing.headers()['if-match']).toBe(seed.headers().etag)
  try {
    await page.getByRole('button', { name: 'Dataset workspace', exact: true }).click()
    await page.getByRole('textbox', { name: 'Grounding episodes', exact: true }).fill('0')
    await expect(
      page.getByRole('button', { name: 'Evaluate grounding episodes', exact: true }),
    ).toBeDisabled()
    const competing = await request.put(`${dataset}/episodes/0/labels`, {
      headers: { 'X-CSRF-Token': csrf.csrf_token, 'If-Match': seed.headers().etag },
      data: { labels: ['PARTIAL'], intent: 'human-edit' },
    })
    expect(competing.ok()).toBe(true)
    const rejected = page.waitForResponse(
      (response) =>
        response.request().method() === 'PUT' && response.url().endsWith('/episodes/0/labels'),
    )
    gate.resolve()
    expect((await rejected).status()).toBe(412)
    await expect(page.getByRole('dialog', { name: 'Resolve saved-data conflict' })).toBeVisible()
    await page.getByRole('button', { name: 'Prefer local conflicts', exact: true }).press('Enter')
    await expect(page.getByRole('dialog', { name: 'Resolve saved-data conflict' })).toHaveCount(0)
    await expect(
      page.getByRole('button', { name: 'Evaluate grounding episodes', exact: true }),
    ).toBeDisabled()
    await page.getByRole('button', { name: 'Return to episode', exact: true }).click()
    await expect(labels.getByRole('button', { name: 'FAILURE', exact: true })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    await expect(labels.getByRole('button', { name: 'PARTIAL', exact: true })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    await labels.getByRole('button', { name: 'PARTIAL', exact: true }).click()
    const acknowledged = page.waitForRequest(
      (next) => next.method() === 'PUT' && next.url().endsWith('/episodes/0/labels'),
    )
    await page.getByRole('button', { name: 'Save Episode', exact: true }).click()
    expect((await acknowledged).headers()['if-match']).toBe(competing.headers().etag)
    await expect
      .poll(async () => (await (await request.get(`${dataset}/labels`)).json()).episodes['0'])
      .toEqual(['FAILURE'])
    await page.getByRole('button', { name: 'Dataset workspace', exact: true }).click()
    const evaluate = page.getByRole('button', { name: 'Evaluate grounding episodes', exact: true })
    await expect(evaluate).toBeEnabled()
    let failSnapshot = true
    await page.route('**/episodes/0/judge/snapshot*', async (route) => {
      if (failSnapshot)
        await route.fulfill({ status: 503, json: { detail: 'Synthetic snapshot failure' } })
      else await route.continue()
    })
    await page.getByRole('button', { name: 'Refresh dataset jobs', exact: true }).press('Enter')
    await expect(
      page.getByText('Saved annotations could not be loaded.', { exact: true }),
    ).toBeVisible()
    await expect(evaluate).toBeDisabled()
    failSnapshot = false
    await page.getByRole('button', { name: 'Refresh dataset jobs', exact: true }).press('Enter')
    await expect(evaluate).toBeEnabled()
    await evaluate.press('Enter')
    await expect(page.getByRole('heading', { name: 'Job review: succeeded' })).toBeVisible({
      timeout: 20_000,
    })
  } finally {
    gate.resolve()
  }
})

for (const status of [401, 403, 404]) {
  test(`P06 cached episode ${status} hides the editor and preserves its draft for retry`, async ({
    page,
  }) => {
    await page.clock.setFixedTime(new Date())
    let denied = false
    await page.route('**/api/datasets/integration-fixture/episodes/0', async (route) => {
      if (denied) await route.fulfill({ status, json: { detail: 'Synthetic access failure' } })
      else await route.continue()
    })
    await page.goto('/?dataset=integration-fixture')
    const label = page
      .getByTestId('trajectory-labels-panel')
      .getByRole('button', { name: 'PARTIAL', exact: true })
    await expect(label).toBeVisible()
    const draftPressed = (await label.getAttribute('aria-pressed')) === 'true' ? 'false' : 'true'
    await label.click()
    await expect(page.getByTestId('workspace-save-status-slot')).toContainText('Unsaved')
    denied = true
    await page.clock.setFixedTime(new Date(Date.now() + 60_000))
    await page.evaluate(() => {
      window.dispatchEvent(new Event('offline'))
      window.dispatchEvent(new Event('online'))
    })
    await expect(page.getByRole('alert')).toContainText('Error loading episode', {
      timeout: 20_000,
    })
    await expect(page.getByRole('button', { name: 'Save Episode', exact: true })).toHaveCount(0)
    await expect(page.getByTestId('trajectory-labels-panel')).toHaveCount(0)
    denied = false
    await page.getByRole('button', { name: 'Retry episode load' }).press('Enter')
    await expect(label).toHaveAttribute('aria-pressed', draftPressed)
    await expect(page.getByTestId('workspace-save-status-slot')).toContainText('Unsaved')
  })
}

test('P06 principal changes isolate persisted label drafts', async ({ page }) => {
  let alternatePrincipal = false
  await page.route('**/api/auth/context', async (route) => {
    const response = await route.fetch()
    const context = await response.json()
    expect(typeof context.scope_id).toBe('string')
    await route.fulfill({
      response,
      json: alternatePrincipal ? { ...context, scope_id: 'isolated-browser-principal' } : context,
    })
  })
  await page.goto('/?dataset=integration-fixture')
  const label = page
    .getByTestId('trajectory-labels-panel')
    .getByRole('button', { name: 'PARTIAL', exact: true })
  await expect(label).toBeVisible()
  const baseline = await label.getAttribute('aria-pressed')
  const draft = baseline === 'true' ? 'false' : 'true'
  await label.click()
  await expect(page.getByTestId('workspace-save-status-slot')).toContainText('Unsaved')
  alternatePrincipal = true
  await page.reload()
  await expect(label).toHaveAttribute('aria-pressed', baseline!)
  await expect(page.getByTestId('workspace-save-status-slot')).not.toContainText('Unsaved')
  alternatePrincipal = false
  await page.reload()
  await expect(label).toHaveAttribute('aria-pressed', draft)
  await expect(page.getByTestId('workspace-save-status-slot')).toContainText('Unsaved')
})

test('P06 changed source requires explicit reconciliation of a recovered draft', async ({
  page,
}) => {
  let sourceChanged = false
  await page.route('**/api/datasets/integration-fixture/episodes/0', async (route) => {
    const response = await route.fetch()
    const episode = await response.json()
    expect(typeof episode.source_revision).toBe('string')
    await route.fulfill({
      response,
      json: sourceChanged
        ? { ...episode, source_revision: `${episode.source_revision}-changed` }
        : episode,
    })
  })
  await page.goto('/?dataset=integration-fixture')
  await page
    .getByTestId('trajectory-labels-panel')
    .getByRole('button', { name: 'PARTIAL', exact: true })
    .click()
  await expect(page.getByTestId('workspace-save-status-slot')).toContainText('Unsaved')
  sourceChanged = true
  await page.reload()
  const conflict = page.getByRole('dialog', { name: 'Resolve saved-data conflict' })
  await expect(conflict).toContainText('The episode source changed')
  await expect(
    conflict.getByRole('button', { name: 'Prefer server conflicts', exact: true }),
  ).toBeEnabled()
  await expect(
    conflict.getByRole('button', { name: 'Prefer local conflicts', exact: true }),
  ).toBeEnabled()
})

for (const resource of ['annotation', 'episode-edit'] as const) {
  test(`P06 unmounted ${resource} draft blocks grounding across reload`, async ({
    page,
    request,
  }) => {
    const backend = 'http://127.0.0.1:18000'
    const dataset = `${backend}/api/datasets/integration-fixture`
    await page.clock.setFixedTime(new Date())
    let failRead = false
    await page.route('**/api/datasets/integration-fixture/episodes/0', async (route) => {
      if (failRead)
        await route.fulfill({ status: 503, json: { detail: 'Synthetic refresh failure' } })
      else await route.continue()
    })
    const csrf = await (await request.get(`${backend}/api/csrf-token`)).json()
    const labels = await request.get(`${dataset}/labels`)
    const saved = await request.put(`${dataset}/episodes/0/labels`, {
      headers: {
        'X-CSRF-Token': csrf.csrf_token,
        ...(labels.headers().etag
          ? { 'If-Match': labels.headers().etag }
          : { 'If-None-Match': '*' }),
      },
      data: { labels: ['SUCCESS'], intent: 'human-edit' },
    })
    expect(saved.ok()).toBe(true)
    const submissions: string[] = []
    page.on('request', (outgoing) => {
      if (outgoing.method() === 'POST' && new URL(outgoing.url()).pathname === '/api/judge/jobs')
        submissions.push(outgoing.url())
    })
    await page.goto('/?dataset=integration-fixture')
    await expect(page.getByRole('button', { name: 'Save Episode', exact: true })).toBeEnabled()
    await page.getByRole('button', { name: 'Dataset workspace', exact: true }).click()
    await page.getByRole('textbox', { name: 'Grounding episodes', exact: true }).fill('0')
    await page.getByRole('textbox', { name: 'Target episodes', exact: true }).fill('1')
    const evaluate = page.getByRole('button', { name: 'Evaluate grounding episodes', exact: true })
    await expect(evaluate).toBeEnabled()
    await page.getByRole('button', { name: 'Return to episode', exact: true }).click()
    const draft =
      resource === 'annotation'
        ? page.locator('#lang-instruction')
        : page.getByTestId('trajectory-graph-panel').getByPlaceholder('Label')
    if (resource === 'episode-edit') await page.getByTitle('Add subtask at current frame').click()
    else await page.getByRole('button', { name: 'Add Instruction', exact: true }).click()
    await draft.fill(`Unsaved ${resource}`)
    await expect(page.getByTestId('workspace-save-status-slot')).toContainText('Unsaved')
    const recoveredDraft =
      resource === 'annotation'
        ? draft
        : page.getByRole('textbox', { name: 'Unsaved episode-edit label', exact: true })
    const frame = page.getByRole('slider', { name: 'Playback frame', exact: true })
    const frameBefore = await frame.inputValue()
    failRead = true
    await page.clock.setFixedTime(new Date(Date.now() + 60_000))
    await page.evaluate(() => {
      window.dispatchEvent(new Event('offline'))
      window.dispatchEvent(new Event('online'))
    })
    await expect(
      page.getByRole('status').filter({ hasText: 'Could not refresh episode' }),
    ).toBeVisible({ timeout: 20_000 })
    await expect(recoveredDraft).toHaveValue(`Unsaved ${resource}`)
    await expect(frame).toHaveValue(frameBefore)
    await expect(page.getByRole('button', { name: 'Save Episode', exact: true })).toBeEnabled()
    failRead = false
    await page.getByRole('button', { name: 'Retry episode load' }).press('Enter')
    await expect(
      page.getByText('Could not refresh episode. Showing previously loaded data.'),
    ).toHaveCount(0)
    await expect(recoveredDraft).toHaveValue(`Unsaved ${resource}`)
    await expect(frame).toHaveValue(frameBefore)
    for (const reload of [false, true]) {
      if (reload) {
        await page.reload()
        await expect(recoveredDraft).toHaveValue(`Unsaved ${resource}`)
      }
      page.once('dialog', (dialog) => dialog.accept())
      await page.getByRole('button', { name: 'Next Episode', exact: true }).click()
      await expect(page.getByRole('heading', { name: 'Episode 1', exact: true })).toBeVisible()
      await expect(page.getByTestId('workspace-save-status-slot')).not.toContainText('Unsaved')
      await page.getByRole('button', { name: 'Dataset workspace', exact: true }).click()
      await page.getByRole('textbox', { name: 'Grounding episodes', exact: true }).fill('0')
      await page.getByRole('textbox', { name: 'Target episodes', exact: true }).fill('1')
      await expect(evaluate).toBeDisabled()
      await expect(page.getByRole('button', { name: 'Judge targets', exact: true })).toBeDisabled()
      expect(submissions).toEqual([])
    }
    await page.getByRole('button', { name: 'Return to episode', exact: true }).click()
    await page.getByRole('button', { name: 'Previous Episode', exact: true }).click()
    await expect(recoveredDraft).toHaveValue(`Unsaved ${resource}`)
    page.once('dialog', (dialog) => dialog.accept())
    await page.getByRole('button', { name: 'Discard changes', exact: true }).click()
    await expect(page.getByTestId('workspace-save-status-slot')).not.toContainText('Unsaved')
    await page.getByRole('button', { name: 'Dataset workspace', exact: true }).click()
    await page.getByRole('textbox', { name: 'Grounding episodes', exact: true }).fill('0')
    await expect(evaluate).toBeEnabled()
    await evaluate.press('Enter')
    await expect(page.getByRole('heading', { name: 'Job review: succeeded' })).toBeVisible({
      timeout: 20_000,
    })
    expect(submissions).toHaveLength(1)
  })
}

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
  await page.goto('/?dataset=a11y-synthetic')
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

  const datasetButton = page.getByRole('button', { name: 'Dataset', exact: true })
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
  await dialog.getByRole('textbox', { name: 'Output Directory' }).fill('synthetic-export')
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
