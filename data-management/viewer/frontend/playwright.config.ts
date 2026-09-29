import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
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
    },
    {
      command:
        'uv run --project ../backend --frozen python -m uvicorn src.api.main:app --app-dir ../backend --host 127.0.0.1 --port 8000',
      url: 'http://127.0.0.1:8000/docs',
      reuseExistingServer: !process.env.CI,
      timeout: 180_000,
      env: { DATAVIEWER_AUTH_DISABLED: 'true', DATA_DIR: '.' },
    },
    {
      command:
        'uv run --no-project --with fastapi==0.141.1 --with uvicorn==0.52.4 --with pydantic==2.13.5 --with Pillow==12.3.0 --with av==15.1.0 python -m uvicorn vlm_judge.api:app --app-dir ../../../evaluation --host 127.0.0.1 --port 8001',
      url: 'http://127.0.0.1:8001/docs',
      reuseExistingServer: !process.env.CI,
      timeout: 180_000,
      env: { VLM_JUDGE_BACKEND: 'echo', VLM_JUDGE_CACHE_DIR: '' },
    },
    {
      command:
        'uv run --no-project --with fastapi==0.141.1 --with uvicorn==0.52.4 --with pydantic==2.13.5 --with Pillow==12.3.0 python -m uvicorn vlm_judge.openai_shim:build_echo_app --factory --app-dir ../../../evaluation --host 127.0.0.1 --port 8002',
      url: 'http://127.0.0.1:8002/docs',
      reuseExistingServer: !process.env.CI,
      timeout: 180_000,
      env: { VLM_SHIM_ALLOW_REMOTE_IMAGES: 'false' },
    },
  ],
})
