// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

import AxeBuilder from '@axe-core/playwright';
import { expect, type Page } from '@playwright/test';

const unexpectedPageErrors = new WeakMap<Page, string[]>();

export function watchUnexpectedPageErrors(page: Page): void {
  const errors: string[] = [];
  unexpectedPageErrors.set(page, errors);
  page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`));
  page.on('console', (message) => {
    if (message.type() === 'error') {
      errors.push(`console: ${message.text()}`);
    }
  });
}

export function expectNoUnexpectedPageErrors(page: Page, allowed: RegExp[] = []): void {
  const errors = (unexpectedPageErrors.get(page) ?? []).filter(
    (error) => !allowed.some((pattern) => pattern.test(error)),
  );
  expect(errors).toEqual([]);
}

export function siteRoute(route: string): string {
  return route === '/' ? './' : route.replace(/^\//, '');
}

export async function analyzeAccessibility(page: Page) {
  return new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa', 'best-practice'])
    .analyze();
}

export async function expectNoAccessibilityViolations(page: Page): Promise<void> {
  const results = await analyzeAccessibility(page);
  expect(results.violations).toEqual([]);
}

export async function expectPageReady(page: Page): Promise<void> {
  await page.waitForLoadState('networkidle');
  const main = page.locator('main, [role="main"]');
  await expect(main).toHaveCount(1);
  await expect(main).toBeVisible();
}