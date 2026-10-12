import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  testMatch: '**/auth-redirect.spec.ts',
  outputDir: '../../../artifacts/auth/current/playwright',
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  workers: 1,
  reporter: 'list',
  use: {
    baseURL: 'http://localhost:5173',
    channel: 'chrome',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'desktop-chrome', use: { ...devices['Desktop Chrome'] } }],
})
