import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { defineConfig, devices } from '@playwright/test'

const dataDirectory = mkdtempSync(join(tmpdir(), 'dataviewer-e2e-'))
process.once('exit', () => rmSync(dataDirectory, { recursive: true, force: true }))
const isolatedEnvironment = {
  DATAVIEWER_AUTH_DISABLED: 'true',
  STORAGE_BACKEND: 'local',
  DATA_DIR: dataDirectory,
  VLM_JUDGE_ENABLED: 'false',
  VLM_JUDGE_BACKEND: 'echo',
  VLM_JUDGE_CACHE_DIR: join(dataDirectory, 'cache'),
  VLM_JUDGE_JOB_DIR: join(dataDirectory, 'jobs'),
}
const seedDataset = `uv run --directory ../backend --no-sync --frozen python -c "import os; from pathlib import Path; from tests.api.test_vlm_judge_router import _build_dataset; _build_dataset(Path(os.environ['DATA_DIR']) / 'integration-fixture', instruction='Place the block')"`

export default defineConfig({
  testDir: './e2e',
  testIgnore: '**/auth-redirect.spec.ts',
  outputDir: '../../../artifacts/accessibility/current/playwright',
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: [
    ['list'],
    ['junit', { outputFile: '../../../artifacts/accessibility/current/playwright-results.xml' }],
  ],
  use: {
    baseURL: 'http://127.0.0.1:4173',
    channel: 'chrome',
    locale: 'en-US',
    timezoneId: 'UTC',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  projects: [
    {
      name: 'desktop-chrome',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 800 } },
    },
  ],
  webServer: [
    {
      command: 'npm run build && npm run preview -- --host 127.0.0.1 --port 4173 --strictPort',
      url: 'http://127.0.0.1:4173',
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        VITE_API_BASE_URL: 'http://127.0.0.1:18000',
        VITE_AZURE_CLIENT_ID: '',
        VITE_AZURE_TENANT_ID: '',
      },
    },
    {
      command: `${seedDataset} && uv run --project ../backend --no-sync --frozen python -m uvicorn src.api.main:app --app-dir ../backend --log-config ../backend/logging.json --host 127.0.0.1 --port 18000`,
      url: 'http://127.0.0.1:18000/docs',
      reuseExistingServer: false,
      timeout: 180_000,
      env: isolatedEnvironment,
    },
    {
      command:
        'uv run --project ../backend --no-sync --frozen python -m uvicorn evaluation.vlm_judge.api:app --host 127.0.0.1 --port 18001',
      url: 'http://127.0.0.1:18001/docs',
      reuseExistingServer: false,
      timeout: 180_000,
      env: isolatedEnvironment,
    },
    {
      command:
        'uv run --project ../backend --no-sync --frozen python -m uvicorn evaluation.vlm_judge.openai_shim:build_echo_app --factory --host 127.0.0.1 --port 18002',
      url: 'http://127.0.0.1:18002/docs',
      reuseExistingServer: false,
      timeout: 180_000,
      env: { ...isolatedEnvironment, VLM_SHIM_ALLOW_REMOTE_IMAGES: 'false' },
    },
  ],
})
