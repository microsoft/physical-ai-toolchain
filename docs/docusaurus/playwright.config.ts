// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

import { defineConfig, devices } from '@playwright/test';

const isCI = Boolean(process.env.CI);
const isExtendedEvidence = process.env.DOCS_E2E_EXTENDED === '1';
const port = Number(process.env.DOCS_E2E_PORT ?? 3001);
if (!Number.isInteger(port) || port < 1 || port > 65_535) {
  throw new Error(`DOCS_E2E_PORT must be an integer from 1 through 65535; received ${process.env.DOCS_E2E_PORT}`);
}
const host = '127.0.0.1';
const baseURL = `http://${host}:${port}/physical-ai-toolchain/`;
const outputDirectory = process.env.DOCS_E2E_OUTPUT_DIR ?? 'test-results';
const reportDirectory = process.env.DOCS_E2E_REPORT_DIR ?? 'playwright-report';

export default defineConfig({
  testDir: './e2e',
  globalSetup: './e2e/global-setup.ts',
  fullyParallel: true,
  forbidOnly: isCI,
  globalTimeout: 60 * 60_000,
  outputDir: outputDirectory,
  retries: isCI ? 2 : 0,
  timeout: 30_000,
  workers: isCI ? 2 : 4,
  reporter: [
    ['./e2e/route-inventory.ts'],
    ['./e2e/evidence-cell-reporter.ts'],
    ['list'],
    ...(isCI ? [['github'] as const] : []),
    ['json', { outputFile: `${outputDirectory}/playwright-results.json` }],
    ['html', { open: 'never', outputFolder: reportDirectory }],
  ],
  use: {
    baseURL,
    screenshot: 'only-on-failure',
    trace: isCI ? 'on-first-retry' : 'retain-on-failure',
    video: 'off',
  },
  projects: [
    {
      name: 'system-chrome',
      use: { ...devices['Desktop Chrome'], channel: 'chrome' },
    },
    ...(isExtendedEvidence
      ? [
          {
            name: 'linux-webkit',
            testIgnore: '**/site-crawl.spec.ts',
            use: { ...devices['Desktop Safari'] },
          },
        ]
      : []),
  ],
  webServer: {
    command: 'npm run serve:ci',
    url: baseURL,
    reuseExistingServer: process.env.DOCS_E2E_FAST === '1',
    timeout: 240_000,
  },
});
