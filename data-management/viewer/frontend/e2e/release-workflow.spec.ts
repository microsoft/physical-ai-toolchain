import { execFile } from 'node:child_process'
import { readdir } from 'node:fs/promises'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { promisify } from 'node:util'

import { expect, type Locator, type Page, type Response, test } from '@playwright/test'

const execFileAsync = promisify(execFile)
const repositoryRoot = fileURLToPath(new URL('../../../../', import.meta.url))
const backendDirectory = join(repositoryRoot, 'data-management/viewer/backend')
const datasetId = 'release-fixture'

function dataDirectory(): string {
  return (test.info().config.metadata as { dataDirectory: string }).dataDirectory
}

/**
 * Builds the environment shared by release worker and store processes, mirroring the API server.
 *
 * @returns Process environment whose job root matches the server under test.
 */
function releaseEnvironment(): NodeJS.ProcessEnv {
  const root = dataDirectory()
  return {
    ...process.env,
    UV_PROJECT_ENVIRONMENT: process.env.UV_PROJECT_ENVIRONMENT ?? join(backendDirectory, '.venv'),
    DATA_DIR: root,
    STORAGE_BACKEND: 'local',
    DATAVIEWER_AUTH_DISABLED: 'true',
    VLM_JUDGE_ENABLED: 'false',
    RELEASE_JOB_ROOT: join(root, 'release-jobs'),
    RELEASE_JOB_STORAGE: '',
    RELEASE_DESTINATIONS: JSON.stringify({ local: join(root, 'release-output') }),
    CURATION_RELEASE_AZUREML_ENABLED: 'false',
    PYTHONPATH: '.',
  }
}

/**
 * Runs one release worker pass as a separate process, the same way an operator would.
 * The worker environment mirrors the API server so both read the same job root.
 */
async function runReleaseWorkerOnce(): Promise<string> {
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
      env: releaseEnvironment(),
    },
  )
  return `${stdout}${stderr}`
}

/**
 * Runs a short Python snippet in the release backend environment and returns its stdout.
 *
 * @param code Python source executed with `python -c`.
 * @param args Extra arguments exposed to the snippet as `sys.argv[1:]`.
 * @param extraEnv Variables layered over the release environment for this snippet only.
 * @returns Standard output of the snippet.
 */
async function runReleasePython(
  code: string,
  args: string[],
  extraEnv: Record<string, string> = {},
): Promise<string> {
  const { stdout } = await execFileAsync(
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
      '-c',
      code,
      ...args,
    ],
    {
      cwd: backendDirectory,
      timeout: 180_000,
      env: { ...releaseEnvironment(), ...extraEnv },
    },
  )
  return stdout
}

/**
 * Seeds an overcommitted reservation with a fixture-only capacity probe.
 * The running API retains its real free-space probe; no disk is filled.
 *
 * @param stamp Unique token used for the fixture job's idempotency key and attempt identity.
 * @returns Identifier of the fixture job that holds the reservation.
 */
async function reserveAllAdmissibleCapacity(stamp: string): Promise<string> {
  const script = [
    'import json, os',
    'from pathlib import Path',
    'from physical_ai_curation.release.jobs import JobRequest, ReleaseJobStore, ReleaseRuntimeSettings, _statvfs_free_bytes',
    'from physical_ai_curation.release.publication import PublicationAttempt',
    'settings = ReleaseRuntimeSettings.from_environment(os.environ)',
    'output = Path(os.environ["RELEASE_OUTPUT_DIR"])',
    'output.mkdir(parents=True, exist_ok=True)',
    'available = _statvfs_free_bytes(output) + 1024**3',
    'store = ReleaseJobStore(Path(os.environ["RELEASE_JOB_ROOT"]), settings=settings, free_bytes=lambda path: available * 2 + settings.reserve_bytes)',
    'accepted = store.accept(JobRequest(',
    '    scope="localuser",',
    '    principal="localuser",',
    '    idempotency_key=os.environ["E2E_STAMP"],',
    '    attempt=PublicationAttempt(package_id=os.environ["E2E_STAMP"], attempt_id=os.environ["E2E_STAMP"], dataset_name=os.environ["E2E_STAMP"]),',
    '    destination=output,',
    '    frozen_request=b"{}",',
    '    reserved_bytes=max(1, available),',
    '    route="local",',
    '))',
    'print(json.dumps({"job_id": accepted.job_id}))',
  ].join('\n')
  const stdout = await runReleasePython(script, [], {
    E2E_STAMP: stamp,
    RELEASE_OUTPUT_DIR: join(dataDirectory(), 'release-output'),
  })
  return (JSON.parse(stdout) as { job_id: string }).job_id
}

/**
 * Cancels and settles a never-started, effect-free capacity fixture through the real store.
 *
 * @param jobId Identifier returned by {@link reserveAllAdmissibleCapacity}.
 */
async function cancelFixtureJob(jobId: string, scope = 'localuser'): Promise<void> {
  const script = [
    'import os, sys',
    'from pathlib import Path',
    'from physical_ai_curation.release.jobs import ReleaseJobStore, ReleaseRuntimeSettings',
    'store = ReleaseJobStore(Path(os.environ["RELEASE_JOB_ROOT"]), settings=ReleaseRuntimeSettings.from_environment(os.environ))',
    'store.request_cancel(sys.argv[1], scope=sys.argv[2], principal=sys.argv[2])',
    'store.settle_cancellation(sys.argv[1], 0, output_removed=True)',
  ].join('\n')
  await runReleasePython(script, [jobId, scope])
}

/**
 * Runs worker passes until the queue reports no outcomes.
 * Each pass settles at most one job, so earlier jobs in the shared fixture must not block the job under test.
 */
async function drainReleaseWorker(): Promise<void> {
  for (let pass = 0; pass < 20; pass += 1) {
    const output = await runReleaseWorkerOnce()
    if (output.includes('"outcomes": []')) return
  }
  throw new Error('release worker queue did not drain within 20 passes')
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

    const processing = dialog.getByRole('checkbox', { name: /video processing/i })
    if (await processing.count()) await processing.check()
    const localDisk = dialog.getByRole('checkbox', { name: /local disk/i })
    if (await localDisk.count()) await localDisk.check()

    await dialog.getByRole('button', { name: 'Submit release' }).click()
    await expect(dialog).toBeHidden({ timeout: 30_000 })

    const jobs = page.getByRole('list', { name: 'Release jobs' })
    await expect(jobs.getByRole('button', { name: /Close job/ })).toHaveCount(1)

    const workerOutput = await runReleaseWorkerOnce()
    expect(workerOutput).toBeTruthy()

    const verified = page.getByRole('region', { name: 'Verified result' })
    await expect(verified.getByRole('button', { name: /Download/ }).first()).toBeVisible({
      timeout: 30_000,
    })

    await page.reload()
    await page.getByRole('tab', { name: 'Releases' }).click()
    await expect(
      page.getByRole('list', { name: 'Release jobs' }).getByRole('button', { name: /Close job/ }),
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

/**
 * Fills the release dialog with a name, the declaration, and the given episodes, without previewing.
 *
 * @param page - Page on the release workspace.
 * @param releaseName - Unique release name.
 * @param episodes - Episode indices to select.
 * @returns The open release dialog locator.
 */
async function startReleaseDraft(
  page: Page,
  releaseName: string,
  episodes: number[],
): Promise<Locator> {
  await openReleases(page)
  await page.getByRole('button', { name: 'Create release' }).click()
  const dialog = page.getByRole('dialog', { name: 'Release curated dataset' })
  await expect(dialog).toBeVisible()
  await dialog.getByLabel('Release name').fill(releaseName)
  await dialog.getByRole('checkbox', { name: /I declare/i }).check()
  for (const episode of episodes) {
    await dialog.getByRole('checkbox', { name: `Episode ${episode}` }).check()
  }
  return dialog
}

/** Submits the current dialog and returns the job identity from the accepted POST response. */
async function submitDraft(page: Page, dialog: Locator): Promise<string> {
  const accepted = submissionOutcome(page)
  await dialog.getByRole('button', { name: 'Submit release' }).click()
  const response = await accepted
  expect(response.status(), await response.text()).toBe(202)
  const body = (await response.json()) as { job_id: string }
  await expect(dialog).toBeHidden({ timeout: 30_000 })
  return body.job_id
}

/** Waits for the dialog's direct POST to the job submission endpoint. */
function submissionOutcome(page: Page): Promise<Response> {
  return page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === '/api/releases/v1/jobs' &&
      response.request().method() === 'POST',
  )
}

/** Counts direct job submissions issued by the dialog. */
function countSubmissions(page: Page): () => number {
  let submissions = 0
  page.on('request', (request) => {
    if (
      request.method() === 'POST' &&
      new URL(request.url()).pathname === '/api/releases/v1/jobs'
    ) {
      submissions += 1
    }
  })
  return () => submissions
}

/**
 * Previews the draft and checks every acknowledgment the preview requires.
 *
 * @param dialog - Open release dialog with a complete draft.
 */
async function previewAndAcknowledge(dialog: Locator): Promise<void> {
  await dialog.getByRole('button', { name: 'Preview release' }).click()
  await expect(dialog.getByText('Reserved bytes')).toBeVisible({ timeout: 30_000 })
  const processing = dialog.getByRole('checkbox', { name: /video processing/i })
  if (await processing.count()) await processing.check()
  const localDisk = dialog.getByRole('checkbox', { name: /local disk/i })
  if (await localDisk.count()) await localDisk.check()
}

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

test.describe('release capacity and catalog boundaries through the real API', () => {
  test('a shortage of admissible capacity is refused without output and recovers after the holding job is cancelled', async ({
    page,
  }) => {
    test.setTimeout(300_000)
    const spec = releaseSpec('e2e-shortage', [0, 1])
    const dialog = await startReleaseDraft(page, spec.dataset_name, [0, 1])
    await previewAndAcknowledge(dialog)
    const outputBefore = await listOutput()
    const holdingJobId = await reserveAllAdmissibleCapacity(spec.attempt_id)
    try {
      const response = submissionOutcome(page)
      await dialog.getByRole('button', { name: 'Submit release' }).click()
      const refused = await response
      expect(refused.status()).toBe(507)
      expect(await refused.json()).toMatchObject({
        detail: { code: 'insufficient capacity for the reserved estimate' },
      })
      await expect(dialog.getByRole('alert')).toContainText('insufficient capacity')
      expect(await listOutput()).toEqual(outputBefore)
    } finally {
      await cancelFixtureJob(holdingJobId)
    }
    await previewAndAcknowledge(dialog)
    const jobId = await submitDraft(page, dialog)
    await drainReleaseWorker()
    expect(await jobStateViaApi(page, jobId)).toBe('committed')
  })

  test('a committed release is absent from the ordinary dataset catalog and listed under its source', async ({
    page,
  }) => {
    test.setTimeout(300_000)
    const releaseName = await commitReleaseViaApi(page, 'e2e-catalog', [0, 1])

    const catalog = await page.request.get('/api/datasets')
    expect(catalog.ok()).toBeTruthy()
    const catalogText = await catalog.text()
    expect(catalogText).toContain(datasetId)
    expect(catalogText).not.toContain(releaseName)

    await openReleases(page)
    const sources = page.getByRole('list', { name: 'Source releases' })
    await expect(sources.getByRole('button', { name: new RegExp(releaseName) })).toBeVisible()
  })
})

test.describe('release API contract against the real backend', () => {
  test('a committed source release is browsable read-only with episodes and camera media', async ({
    page,
  }) => {
    test.setTimeout(300_000)
    const releaseName = await commitReleaseViaApi(page, 'e2e-source', [0, 1])

    await openReleases(page)
    await page.getByRole('tab', { name: 'Episodes', exact: true }).click()
    await page
      .locator('aside')
      .getByRole('button', { name: /Episode 1/ })
      .click()
    await expect(page.getByRole('heading', { name: 'Episode 1', exact: true })).toBeVisible()
    await page.getByRole('tab', { name: 'Releases', exact: true }).click()
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
    const mutation = await page.request.put(detailReply.url(), {
      headers: await csrfHeaders(page),
      data: { description: 'Must remain immutable' },
    })
    expect(mutation.status()).toBe(405)
    const sourceTab = page.getByRole('tab', { name: 'Episodes', exact: true })
    await sourceTab.focus()
    await page.keyboard.press('Enter')
    await expect(page.getByRole('heading', { name: 'Episode 1', exact: true })).toBeVisible()
    expect(new URL(page.url()).searchParams.get('dataset')).toBe(datasetId)
  })

  test('a label saved through the real API leaves the advisory preview valid', async ({ page }) => {
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
    expect(refreshed.digest).toBe(preview.digest)

    const submitted = await submitViaApi(page, spec, preview)
    expect(submitted.status).toBe(202)
    expect(await jobStateViaApi(page, submitted.body.job_id!)).toBe('accepted')
  })

  test('an accepted job cancelled before publication leaves no output after the worker runs', async ({
    page,
  }) => {
    test.setTimeout(300_000)
    await openReleases(page)
    await drainReleaseWorker()
    const before = await listOutput()

    const spec = releaseSpec('e2e-cancel', [0])
    const preview = await previewViaApi(page, spec)
    const submitted = await submitViaApi(page, spec, preview)
    expect(submitted.status).toBe(202)
    const jobId = submitted.body.job_id!

    const headers = await csrfHeaders(page)
    const cancel = await page.request.post(`/api/releases/v1/jobs/${jobId}/cancel`, { headers })
    expect(cancel.status()).toBe(202)

    await drainReleaseWorker()
    expect((await page.request.get(`/api/releases/v1/jobs/${jobId}`)).status()).toBe(404)
    expect(await listOutput()).toEqual(before)
  })
})

test.describe('release dialog and job panel through the real API', () => {
  test('an inaccessible saved job clears its pointer without resubmitting', async ({ page }) => {
    test.setTimeout(180_000)
    const submissionCount = countSubmissions(page)
    const dialog = await startReleaseDraft(page, `e2e-denied-${Date.now()}`, [0])
    await previewAndAcknowledge(dialog)
    const ownJobId = await submitDraft(page, dialog)
    const panel = page.getByRole('region', { name: `Job ${ownJobId.slice(0, 8)}` })
    await expect(panel.getByRole('status')).toContainText('Accepted')
    await expect(panel.getByRole('status')).toHaveAttribute('aria-live', 'polite')
    await cancelFixtureJob(ownJobId)
    const foreignJobId = (
      await runReleasePython(
        [
          'import os',
          'from pathlib import Path',
          'from physical_ai_curation.release.jobs import JobRequest, ReleaseJobStore',
          'from physical_ai_curation.release.publication import PublicationAttempt',
          'store = ReleaseJobStore(Path(os.environ["RELEASE_JOB_ROOT"]))',
          'job = store.accept(JobRequest(scope="foreign-scope", principal="foreign-scope", idempotency_key="foreign-test-request", attempt=PublicationAttempt(package_id="foreign-package", attempt_id="foreign-attempt", dataset_name="foreign-release"), destination=Path(os.environ["DATA_DIR"]) / "release-output", frozen_request=b"{}", reserved_bytes=1))',
          'print(job.job_id)',
        ].join('\n'),
        [],
      )
    ).trim()
    try {
      await page.evaluate(
        ({ dataset, jobId }) => {
          const key = Object.keys(localStorage).find(
            (candidate) =>
              candidate.startsWith('release-active-job:v2:') &&
              JSON.parse(candidate.slice('release-active-job:v2:'.length))[0] === dataset,
          )
          if (!key) throw new Error('accepted job pointer was not persisted')
          localStorage.setItem(key, jobId)
        },
        { dataset: datasetId, jobId: foreignJobId },
      )
      const refusal = page.waitForResponse(
        (response) => new URL(response.url()).pathname === `/api/releases/v1/jobs/${foreignJobId}`,
      )
      await page.reload()
      await page.getByRole('tab', { name: 'Releases' }).click()
      expect((await refusal).status()).toBe(404)
      await expect(panel).toBeHidden()
      await expect(
        page.getByRole('region', { name: `Job ${foreignJobId.slice(0, 8)}` }),
      ).toBeHidden()
      expect(
        await page.evaluate(
          (jobId) =>
            Object.keys(localStorage).some(
              (key) =>
                key.startsWith('release-active-job:v2:') && localStorage.getItem(key) === jobId,
            ),
          foreignJobId,
        ),
      ).toBe(false)
      const create = page.getByRole('button', { name: 'Create release' })
      await expect(create).toBeEnabled()
      await create.focus()
      await page.keyboard.press('Enter')
      await expect(page.getByRole('dialog', { name: 'Release curated dataset' })).toBeVisible()
      await page.keyboard.press('Escape')
      await expect(create).toBeFocused()
      expect(submissionCount()).toBe(1)
    } finally {
      await cancelFixtureJob(foreignJobId, 'foreign-scope')
    }
  })

  test('a phone-width release completes from selection to verified result without overflow', async ({
    page,
  }) => {
    test.setTimeout(300_000)
    await page.setViewportSize({ width: 390, height: 844 })
    const releaseName = `e2e-mobile-${Date.now()}`

    const dialog = await startReleaseDraft(page, releaseName, [0, 1])
    const jobs = page.getByRole('list', { name: 'Release jobs' })
    await previewAndAcknowledge(dialog)
    const jobId = await submitDraft(page, dialog)

    await expect(
      jobs.getByRole('button', { name: `Close job ${jobId.slice(0, 8)}` }),
    ).toHaveAttribute('aria-expanded', 'true')
    await drainReleaseWorker()

    const verified = page.getByRole('region', { name: 'Verified result' })
    await expect(verified.getByRole('button', { name: /Download/ }).first()).toBeVisible({
      timeout: 30_000,
    })
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    )
    expect(overflow).toBeLessThanOrEqual(0)
  })

  test('a saved label leaves the advisory preview usable for submission', async ({ page }) => {
    test.setTimeout(180_000)
    const dialog = await startReleaseDraft(page, `e2e-ui-stale-${Date.now()}`, [0, 1])
    await previewAndAcknowledge(dialog)

    const headers = await csrfHeaders(page)
    const current = await page.request.get(`/api/datasets/${datasetId}/labels`)
    const etag = current.headers()['etag']
    const saved = await page.request.put(`/api/datasets/${datasetId}/episodes/0/labels`, {
      headers: {
        ...headers,
        ...(etag ? { 'If-Match': etag } : { 'If-None-Match': '*' }),
        'X-Curation-Intent': 'human-edit',
      },
      data: { labels: [`e2e-ui-stale-${Date.now()}`], intent: 'human-edit' },
    })
    expect(saved.ok(), await saved.text()).toBeTruthy()

    await expect(dialog.getByText('Reserved bytes')).toBeVisible()
    await expect(dialog.getByRole('button', { name: 'Submit release' })).toBeEnabled()
    const jobId = await submitDraft(page, dialog)
    await expect(page.getByRole('region', { name: `Job ${jobId.slice(0, 8)}` })).toBeVisible()
    expect(await jobStateViaApi(page, jobId)).toBe('accepted')
  })

  test('reloading while a job is accepted restores the job without a second submission', async ({
    page,
  }) => {
    test.setTimeout(300_000)
    const submissionCount = countSubmissions(page)
    const dialog = await startReleaseDraft(page, `e2e-reload-${Date.now()}`, [0])
    const jobs = page.getByRole('list', { name: 'Release jobs' })
    await previewAndAcknowledge(dialog)
    const jobId = await submitDraft(page, dialog)
    const jobButton = jobs.getByRole('button', { name: `Close job ${jobId.slice(0, 8)}` })
    await expect(jobButton).toHaveAttribute('aria-expanded', 'true')

    await page.reload()
    await page.getByRole('tab', { name: 'Releases' }).click()
    await expect(page.getByRole('heading', { name: `Job ${jobId.slice(0, 8)}` })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Create release' })).toBeDisabled()
    await expect(jobButton).toBeVisible()
    expect(await jobStateViaApi(page, jobId)).toBe('accepted')
    expect(submissionCount()).toBe(1)

    await drainReleaseWorker()
    const verified = page.getByRole('region', { name: 'Verified result' })
    await expect(verified.getByRole('button', { name: /Download/ }).first()).toBeVisible({
      timeout: 30_000,
    })
    await expect(jobButton).toBeVisible()
    expect(await jobStateViaApi(page, jobId)).toBe('committed')
    expect(submissionCount()).toBe(1)
  })

  test('cancelling from the active job panel stops the job and leaves no output', async ({
    page,
  }) => {
    test.setTimeout(300_000)
    await drainReleaseWorker()
    const before = await listOutput()
    const dialog = await startReleaseDraft(page, `e2e-ui-cancel-${Date.now()}`, [0])
    await previewAndAcknowledge(dialog)
    const jobId = await submitDraft(page, dialog)

    const panel = page.getByRole('region', { name: `Job ${jobId.slice(0, 8)}` })
    const cancel = panel.getByRole('button', { name: 'Cancel job' })
    await expect(cancel).toBeEnabled({ timeout: 30_000 })
    await cancel.click()
    await expect(cancel).toBeDisabled()

    await drainReleaseWorker()
    await expect(page.getByRole('button', { name: 'Refresh status' })).toHaveCount(0)
    expect(await listOutput()).toEqual(before)
  })
})

test.describe('release read and control admission through the real HTTP service', () => {
  test('concurrent source reads are admitted or refused with bounded status and the control lane stays responsive', async ({
    page,
  }) => {
    test.setTimeout(120_000)
    const readUrl = `/api/releases/v1/sources/${encodeURIComponent(datasetId)}/releases`
    const statusUrl = `/api/releases/v1/jobs/${'0'.repeat(32)}`

    const reads = Array.from({ length: 12 }, () =>
      page.request.get(readUrl, { failOnStatusCode: false }),
    )
    const startedAt = Date.now()
    const status = await page.request.get(statusUrl, { failOnStatusCode: false })
    const statusElapsedMs = Date.now() - startedAt
    const readStatuses = (await Promise.all(reads)).map((response) => response.status())

    expect(status.status()).toBe(404)
    expect(statusElapsedMs).toBeLessThan(10_000)
    for (const code of readStatuses) {
      expect([200, 503, 504]).toContain(code)
    }
    expect(readStatuses).toContain(200)
  })
})
