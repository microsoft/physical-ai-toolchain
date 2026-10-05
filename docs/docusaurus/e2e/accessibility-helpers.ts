// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

import fs from 'node:fs';
import path from 'node:path';

import AxeBuilder from '@axe-core/playwright';
import { expect, type Page } from '@playwright/test';

import { normalizeRoute } from './route-inventory';

export interface MermaidDiagram {
  description: string;
  ordinal: number;
  route: string;
  sourcePath: string;
  title: string;
}

export interface MermaidManifest {
  baseUrl: string;
  diagrams: MermaidDiagram[];
  schemaVersion: 1;
}

export const mermaidManifestPath = path.resolve(process.cwd(), 'build', 'mermaid-routes.json');

let cachedMermaidManifest: MermaidManifest | undefined;

function nonEmptyString(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0;
}

function invalidManifest(source: string, reason: string): never {
  throw new Error(`Mermaid manifest ${source} is invalid: ${reason}`);
}

export function parseMermaidManifest(raw: unknown, source = mermaidManifestPath): MermaidManifest {
  if (typeof raw !== 'object' || raw === null) invalidManifest(source, 'expected an object');
  const document = raw as Partial<MermaidManifest>;
  if (document.schemaVersion !== 1) invalidManifest(source, `unsupported schemaVersion ${String(document.schemaVersion)}`);
  const { baseUrl, diagrams } = document;
  if (!nonEmptyString(baseUrl) || !baseUrl.startsWith('/') || !baseUrl.endsWith('/')) {
    invalidManifest(source, `baseUrl must start and end with "/"; received ${String(baseUrl)}`);
  }
  if (!Array.isArray(diagrams)) invalidManifest(source, 'diagrams must be an array');

  const ordinalsByRoute = new Map<string, number[]>();
  for (const diagram of diagrams) {
    if (
      !nonEmptyString(diagram?.route) ||
      normalizeRoute(diagram.route) !== diagram.route ||
      !Number.isInteger(diagram.ordinal) ||
      diagram.ordinal < 1 ||
      !nonEmptyString(diagram.sourcePath) ||
      !nonEmptyString(diagram.title) ||
      !nonEmptyString(diagram.description)
    ) {
      invalidManifest(
        source,
        `${String(diagram?.sourcePath)} diagram ${String(diagram?.ordinal)} needs a normalized route, positive ordinal, source path, title, and description`,
      );
    }
    const ordinals = ordinalsByRoute.get(diagram.route) ?? [];
    if (ordinals.includes(diagram.ordinal)) invalidManifest(source, `${diagram.route} repeats ordinal ${diagram.ordinal}`);
    ordinalsByRoute.set(diagram.route, [...ordinals, diagram.ordinal]);
  }
  for (const [route, ordinals] of ordinalsByRoute) {
    const sorted = [...ordinals].sort((left, right) => left - right);
    if (sorted.some((ordinal, index) => ordinal !== index + 1)) {
      invalidManifest(source, `${route} ordinals must run from 1 without gaps; received ${sorted.join(', ')}`);
    }
  }
  return { baseUrl, diagrams, schemaVersion: 1 };
}

export function readMermaidManifest(filePath = mermaidManifestPath): MermaidManifest {
  if (filePath === mermaidManifestPath && cachedMermaidManifest) {
    return cachedMermaidManifest;
  }
  if (!fs.existsSync(filePath)) {
    throw new Error(`Mermaid manifest is missing: ${filePath}. Run npm run generate:test-manifests.`);
  }
  const manifest = parseMermaidManifest(JSON.parse(fs.readFileSync(filePath, 'utf8')), filePath);
  if (filePath === mermaidManifestPath) {
    cachedMermaidManifest = manifest;
  }
  return manifest;
}

export function expectedMermaidDiagrams(url: string, manifest: MermaidManifest = readMermaidManifest()): number {
  const { pathname } = new URL(url);
  const base = manifest.baseUrl;
  if (pathname !== base.slice(0, -1) && !pathname.startsWith(base)) {
    throw new Error(`${url} is outside the documentation base path ${base}`);
  }
  const route = normalizeRoute(pathname, base);
  return manifest.diagrams.filter((diagram) => diagram.route === route).length;
}

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

// Docusaurus keeps rendering the previous route until the next one loads, and its canonical link follows the
// rendered location. Matching it to the address bar proves a client-side transition has committed.
export async function waitForRenderedRoute(page: Page): Promise<void> {
  await page.locator('html[data-has-hydrated="true"]').waitFor({ state: 'attached' });
  await page.waitForFunction(() => {
    const canonical = document.querySelector('link[rel="canonical"]')?.getAttribute('href');
    if (!canonical) {
      return false;
    }
    const normalize = (pathname: string): string => pathname.replace(/\/+$/, '') || '/';
    return normalize(new URL(canonical, location.href).pathname) === normalize(location.pathname);
  });
}

export async function expectPageReady(page: Page, manifest?: MermaidManifest): Promise<void> {
  await waitForRenderedRoute(page);
  await page.evaluate(async () => {
    await document.fonts.ready;
  });
  const main = page.locator('main, [role="main"]');
  await expect(main).toHaveCount(1);
  await expect(main).toBeVisible();

  const expected = expectedMermaidDiagrams(page.url(), manifest);
  const containers = page.locator('.docusaurus-mermaid-container');
  const diagrams = containers.locator('svg');
  if (expected > 0) {
    await diagrams.nth(expected - 1).waitFor({ state: 'attached' });
  }
  await expect(containers, `Mermaid containers on ${page.url()}`).toHaveCount(expected);
  await expect(diagrams).toHaveCount(expected);

  // The search page loads its index after hydration, then announces a settled result count for a non-empty query.
  const searchStatus = page.locator('[id^="search-results-status-"]');
  if (new URL(page.url()).searchParams.get('q')?.trim() && (await searchStatus.count()) > 0) {
    await page.waitForFunction(() =>
      Boolean(document.querySelector('[id^="search-results-status-"]')?.textContent?.trim()),
    );
  }
}