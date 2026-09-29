// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

import fs from 'node:fs';
import path from 'node:path';

import { chromium, type FullConfig } from '@playwright/test';

export default async function globalSetup(_config: FullConfig): Promise<void> {
  const browser = await chromium.launch({ channel: 'chrome' });
  const version = browser.version();
  await browser.close();

  const outputDirectory = path.resolve(process.env.DOCS_E2E_OUTPUT_DIR ?? 'test-results');
  fs.mkdirSync(outputDirectory, { recursive: true });
  fs.writeFileSync(
    path.join(outputDirectory, 'browser-version.json'),
    `${JSON.stringify({ channel: 'chrome', version }, null, 2)}\n`,
    'utf8',
  );
  console.log(`Google Chrome ${version}`);
}