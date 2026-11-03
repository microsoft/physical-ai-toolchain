import { execFile } from 'node:child_process'
import { readdir } from 'node:fs/promises'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { promisify } from 'node:util'

import { expect, type Page, test } from '@playwright/test'

const execFileAsync = promisify(execFile)
const repositoryRoot = fileURLToPath(new URL('../../../../', import.meta.url))
const backendDirectory = join(repositoryRoot, 'data-management/viewer/backend')
const datasetId = 'release-fixture'

function dataDirectory(): string {
  return (test.info().config.metadata as { dataDirectory: string }).dataDirectory
}

/**
 * Runs one release worker pass as a separate process, the same way an operator would.
 * The worker environment mirrors the API server so both read the same job root.
 */
async function runReleaseWorkerOnce(): Promise<string> {
  const root = dataDirectory()
  const { stdout, stderr } = await execFileAsync(
    'uv',
    [
      'run',
      '--project',
      repositoryRoot,
      '--all-packages',
      '--no-sync',
      '--frozen',
      '--directory',
      backendDirectory,
      'python',
      '-m',
      'src.api.release_worker',
      '--once',
      '--limit',
      '1',
      '--worker-id',
      'e2e-worker',
    ],
    {
      cwd: backendDirectory,
      timeout: 180_000,
      env: {
        ...process.env,
        DATA_DIR: root,
        STORAGE_BACKEND: 'local',
        DATAVIEWER_AUTH_DISABLED: 'true',
        VLM_JUDGE_ENABLED: 'false',
        RELEASE_JOB_ROOT: join(root, 'release-jobs'),
        RELEASE_DESTINATIONS: JSON.stringify({ local: join(root, 'release-output') }),
        PYTHONPATH: '.',
      },
    },
  )
  return `${stdout}${stderr}`
}

async function openReleases(page: Page): Promise<void> {
  await page.goto(`/?dataset=${encodeURIComponent(datasetId)}`)
  await page.getByRole('tab', { name: 'Releases' }).click()
  await expect(page.getByRole('region', { name: 'Releases', exact: true })).toBeVisible()
}

test.describe('curated dataset release workflow', () => {
  test('submits a release, runs the worker, and shows the verified result without duplicates', async ({
    page,
  }) => {
    test.setTimeout(300_000)
    const releaseName = `e2e-release-${Date.now()}`

    await openReleases(page)
    await page.getByRole('button', { name: 'Create release' }).click()

    const dialog = page.getByRole('dialog', { name: 'Release curated dataset' })
    await expect(dialog).toBeVisible()
    await dialog.getByLabel('Release name').fill(releaseName)
    await dialog.getByRole('checkbox', { name: /I declare/i }).check()
    await dialog.getByRole('checkbox', { name: 'Episode 0' }).check()
    await dialog.getByRole('checkbox', { name: 'Episode 1' }).check()

    await dialog.getByRole('button', { name: 'Preview release' }).click()
    await expect(dialog.getByText('Reserved bytes')).toBeVisible({ timeout: 30_000 })

    const processing = dialog.getByRole('checkbox', { name: /processing action/i })
    if (await processing.count()) await processing.check()
    const localDisk = dialog.getByRole('checkbox', { name: /local disk/i })
    if (await localDisk.count()) await localDisk.check()

    await dialog.getByRole('button', { name: 'Submit release' }).click()
    await expect(dialog).toBeHidden({ timeout: 30_000 })

    const jobs = page.getByRole('list', { name: 'Release jobs' })
    await expect(jobs.getByRole('button', { name: /Open job/ })).toHaveCount(1)

    const workerOutput = await runReleaseWorkerOnce()
    expect(workerOutput).toBeTruthy()

    await page.getByRole('button', { name: 'Refresh status' }).click()
    const verified = page.getByRole('region', { name: 'Verified result' })
    await expect(verified.getByRole('button', { name: /Download/ }).first()).toBeVisible()

    await page.reload()
    await page.getByRole('tab', { name: 'Releases' }).click()
    await expect(
      page.getByRole('list', { name: 'Release jobs' }).getByRole('button', { name: /Open job/ }),
    ).toHaveCount(1)
  })

  test('keyboard-only dialog use returns focus to Create release after Escape', async ({
    page,
  }) => {
    await openReleases(page)
    const createButton = page.getByRole('button', { name: 'Create release' })
    await expect(createButton).toBeEnabled({ timeout: 30_000 })

    await createButton.focus()
    await page.keyboard.press('Enter')
    const dialog = page.getByRole('dialog', { name: 'Release curated dataset' })
    await expect(dialog).toBeVisible()

    const releaseName = dialog.getByLabel('Release name')
    for (
      let step = 0;
      step < 40 && !(await releaseName.evaluate((node) => node === document.activeElement));
      step += 1
    ) {
      await page.keyboard.press('Tab')
    }
    await expect(releaseName).toBeFocused()
    await page.keyboard.type('keyboard-release')
    await expect(releaseName).toHaveValue('keyboard-release')

    await page.keyboard.press('Escape')
    await expect(dialog).toBeHidden()
    await expect(createButton).toBeFocused()
  })

  test('narrow viewports reflow without horizontal page scrolling', async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 320, height: 800 })
    await openReleases(page)
    await expect(page.getByRole('button', { name: 'Create release' })).toBeVisible()

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    )
    expect(overflow).toBeLessThanOrEqual(0)
    await testInfo.attach('releases-320px', {
      body: await page.screenshot(),
      contentType: 'image/png',
    })
  })
})

test.describe('mobile release workflow', () => {
  test.use({ viewport: { width: 390, height: 844 } })

  test('opens the release dialog within a phone-width viewport', async ({ page }, testInfo) => {
    await openReleases(page)
    await page.getByRole('button', { name: 'Create release' }).click()

    const dialog = page.getByRole('dialog', { name: 'Release curated dataset' })
    await expect(dialog).toBeVisible()
    const box = await dialog.boundingBox()
    expect(box).not.toBeNull()
    expect(box!.x).toBeGreaterThanOrEqual(0)
    expect(box!.x + box!.width).toBeLessThanOrEqual(390)

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    )
    expect(overflow).toBeLessThanOrEqual(0)
    await testInfo.attach('release-dialog-390px', {
      body: await page.screenshot(),
      contentType: 'image/png',
    })
  })
})

interface ReleaseSpecPayload {
  destination: string
  package_id: string
  attempt_id: string
  dataset_name: string
  dataset_id: string
  episode_indices: number[]
  resources: unknown[]
  finalized: boolean
  description: string
}

interface PreviewBody {
  digest: string
  summary: { processing_digest?: string }
}

function releaseSpec(label: string, episodeIndices: number[]): ReleaseSpecPayload {
  const stamp = `${label}-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`
  return {
    destination: 'local',
    package_id: stamp,
    attempt_id: stamp,
    dataset_name: stamp,
    dataset_id: datasetId,
    episode_indices: episodeIndices,
    resources: [],
    finalized: true,
    description: 'E2E release contract',
  }
}

/** Reads the CSRF cookie set by the API and returns the matching request header. */
async function csrfHeaders(page: Page): Promise<Record<string, string>> {
  await page.request.get('/api/csrf-token')
  const cookie = (await page.context().cookies()).find((entry) => entry.name === 'csrf_token')
  expect(cookie, 'csrf_token cookie must be set').toBeDefined()
  return { 'X-CSRF-Token': cookie!.value }
}

async function previewViaApi(page: Page, spec: ReleaseSpecPayload): Promise<PreviewBody> {
  const response = await page.request.post('/api/releases/v1/preview', {
    headers: await csrfHeaders(page),
    data: spec,
  })
  expect(response.ok(), await response.text()).toBeTruthy()
  return (await response.json()) as PreviewBody
}

async function submitViaApi(
  page: Page,
  spec: ReleaseSpecPayload,
  preview: PreviewBody,
): Promise<{ status: number; body: { job_id?: string; detail?: { code?: string } } }> {
  const headers = await csrfHeaders(page)
  const response = await page.request.post('/api/releases/v1/jobs', {
    headers: { ...headers, 'Idempotency-Key': `${spec.attempt_id}-key` },
    data: {
      ...spec,
      preview_digest: preview.digest,
      processing_digest: preview.summary.processing_digest ?? '',
      local_disk_acknowledged: true,
    },
  })
  const text = await response.text()
  return { status: response.status(), body: text ? JSON.parse(text) : {} }
}

async function jobStateViaApi(page: Page, jobId: string): Promise<string> {
  const response = await page.request.get(`/api/releases/v1/jobs/${jobId}`)
  expect(response.ok(), await response.text()).toBeTruthy()
  const body = (await response.json()) as { state?: string; status?: string }
  return body.state ?? body.status ?? ''
}

async function listOutput(): Promise<string[]> {
  try {
    return (await readdir(join(dataDirectory(), 'release-output'))).sort()
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return []
    throw error
  }
}

/** Commits one release through the real API and worker, so read-only tests have a source to browse. */
async function commitReleaseViaApi(
  page: Page,
  label: string,
  episodeIndices: number[],
): Promise<string> {
  const spec = releaseSpec(label, episodeIndices)
  const preview = await previewViaApi(page, spec)
  const submitted = await submitViaApi(page, spec, preview)
  expect(submitted.status).toBe(202)
  const jobId = submitted.body.job_id!
  await runReleaseWorkerOnce()
  expect(await jobStateViaApi(page, jobId)).toBe('committed')
  return spec.dataset_name
}

test.describe('release API contract against the real backend', () => {
  test('a committed source release is browsable read-only with episodes and camera media', async ({
    page,
  }) => {
    test.setTimeout(300_000)
    const releaseName = await commitReleaseViaApi(page, 'e2e-source', [0, 1])

    await openReleases(page)
    const sources = page.getByRole('list', { name: 'Source releases' })
    const detailResponse = page.waitForResponse(
      (response) =>
        /\/api\/releases\/v1\/sources\/[^/]+\/releases\/[0-9a-f]{64}$/.test(
          new URL(response.url()).pathname,
        ) && response.request().method() === 'GET',
    )
    await sources.getByRole('button', { name: new RegExp(releaseName) }).click()
    const detailReply = await detailResponse
    expect(detailReply.status(), `${detailReply.url()}: ${await detailReply.text()}`).toBe(200)

    await expect(page.getByRole('heading', { name: 'Release details (read-only)' })).toBeVisible()
    const detail = page.locator('section', {
      has: page.getByRole('heading', { name: 'Release details (read-only)' }),
    })
    await expect(detail.locator('input, textarea, select')).toHaveCount(0)
    await expect(detail.getByText('Annotations', { exact: true })).toBeVisible()
    await expect(detail.getByText('Labels', { exact: true })).toBeVisible()

    const episodes = page.getByRole('list', { name: 'Release episodes' })
    await episodes.getByRole('button', { name: 'Episode 0' }).click()
    await expect(page.getByText('Instruction', { exact: true })).toBeVisible()

    const cameras = page.getByRole('group', { name: 'Cameras' })
    await cameras.getByRole('button').first().click()
    const video = page.locator('video[aria-label^="Camera "][aria-label$="for episode 0"]')
    await expect(video).toBeVisible({ timeout: 30_000 })
    await expect(page.getByText('Media unavailable for this camera.')).toHaveCount(0)
  })

  test('a label saved through the real API makes an earlier preview stale', async ({ page }) => {
    test.setTimeout(180_000)
    await openReleases(page)
    const spec = releaseSpec('e2e-stale', [0, 1])
    const preview = await previewViaApi(page, spec)

    const headers = await csrfHeaders(page)
    const labelsUrl = `/api/datasets/${datasetId}/labels`
    const current = await page.request.get(labelsUrl)
    expect(current.ok()).toBeTruthy()
    const etag = current.headers()['etag']
    const label = `e2e-stale-${Date.now()}`
    const saved = await page.request.put(`/api/datasets/${datasetId}/episodes/0/labels`, {
      headers: {
        ...headers,
        ...(etag ? { 'If-Match': etag } : { 'If-None-Match': '*' }),
        'X-Curation-Intent': 'human-edit',
      },
      data: { labels: [label], intent: 'human-edit' },
    })
    expect(saved.ok(), await saved.text()).toBeTruthy()

    const refreshed = await previewViaApi(page, spec)
    expect(refreshed.digest).not.toBe(preview.digest)

    const stale = await submitViaApi(page, spec, preview)
    expect(stale.status).toBe(409)
    expect(stale.body.detail?.code).toBe('stale_preview')
  })

  test('an accepted job cancelled before publication leaves no output after the worker runs', async ({
    page,
  }) => {
    test.setTimeout(300_000)
    await openReleases(page)
    const before = await listOutput()

    const spec = releaseSpec('e2e-cancel', [0])
    const preview = await previewViaApi(page, spec)
    const submitted = await submitViaApi(page, spec, preview)
    expect(submitted.status).toBe(202)
    const jobId = submitted.body.job_id!

    const headers = await csrfHeaders(page)
    const cancel = await page.request.post(`/api/releases/v1/jobs/${jobId}/cancel`, { headers })
    expect(cancel.status()).toBe(202)

    await runReleaseWorkerOnce()
    expect(await jobStateViaApi(page, jobId)).toBe('cancelled')
    expect(await listOutput()).toEqual(before)
  })
})
