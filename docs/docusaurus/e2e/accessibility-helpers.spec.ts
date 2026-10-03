// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

import fs from 'node:fs';

import { expect, test, type Page, type Route } from '@playwright/test';

import {
  expectedMermaidDiagrams,
  expectPageReady,
  type MermaidManifest,
  parseMermaidManifest,
  readMermaidManifest,
  siteRoute,
} from './accessibility-helpers';

const BASE = '/physical-ai-toolchain/';
const PROBE = `${BASE}__readiness`;
// Negative readiness checks observe a bounded window instead of waiting for the test timeout.
const OBSERVATION_MS = 500;

const syntheticManifest: MermaidManifest = {
  baseUrl: BASE,
  diagrams: [
    { description: 'First guide diagram', ordinal: 1, route: '/guide', sourcePath: 'guide.md', title: 'Guide one' },
    { description: 'Second guide diagram', ordinal: 2, route: '/guide', sourcePath: 'guide.md', title: 'Guide two' },
    { description: 'Home diagram', ordinal: 1, route: '/', sourcePath: 'index.md', title: 'Home' },
    { description: 'Probe diagram', ordinal: 1, route: '/__readiness/diagram', sourcePath: 'probe.md', title: 'Probe' },
    { description: 'Next diagram', ordinal: 1, route: '/__readiness/next', sourcePath: 'next.md', title: 'Next' },
    { description: 'Missing diagram', ordinal: 1, route: '/__readiness/missing', sourcePath: 'gap.md', title: 'Gap' },
  ],
  schemaVersion: 1,
};

interface Tracked {
  error?: unknown;
  settled: boolean;
}

function track(promise: Promise<void>): Tracked {
  const state: Tracked = { settled: false };
  promise.then(
    () => {
      state.settled = true;
    },
    (error: unknown) => {
      state.error = error;
    },
  );
  return state;
}

async function expectStillPending(page: Page, state: Tracked): Promise<void> {
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(resolve)));
  await new Promise((resolve) => setTimeout(resolve, OBSERVATION_MS));
  expect(state.error).toBeUndefined();
  expect(state.settled).toBe(false);
}

function probePage(route: string, body: string, hydrated: boolean): string {
  return `<!doctype html><html lang="en"${hydrated ? ' data-has-hydrated="true"' : ''}><head><title>Readiness probe</title>` +
    `<link rel="canonical" href="https://docs.invalid${PROBE}/${route}"></head><body>${body}</body></html>`;
}

async function serveProbe(page: Page, route: string, body: string, hydrated: boolean): Promise<void> {
  await page.route(`**${PROBE}/${route}`, (request) =>
    request.fulfill({ body: probePage(route, body, hydrated), contentType: 'text/html' }),
  );
  await page.goto(`${PROBE}/${route}`);
}

test('the Mermaid manifest reader validates its schema and resolves routes under the base path', ({}, testInfo) => {
  const live = readMermaidManifest();
  expect(live.schemaVersion).toBe(1);
  expect(live.diagrams.length).toBeGreaterThan(0);

  const valid = { baseUrl: BASE, diagrams: [syntheticManifest.diagrams[0]], schemaVersion: 1 };
  const invalid: unknown[] = [
    null,
    { ...valid, schemaVersion: 2 },
    { ...valid, baseUrl: 'physical-ai-toolchain' },
    { ...valid, diagrams: {} },
    { ...valid, diagrams: [{ ...valid.diagrams[0], route: '/guide/' }] },
    { ...valid, diagrams: [{ ...valid.diagrams[0], ordinal: 0 }] },
    { ...valid, diagrams: [{ ...valid.diagrams[0], title: ' ' }] },
    { ...valid, diagrams: [valid.diagrams[0], valid.diagrams[0]] },
    { ...valid, diagrams: [{ ...valid.diagrams[0], ordinal: 2 }] },
  ];
  expect(() => parseMermaidManifest(valid, 'valid')).not.toThrow();
  for (const document of invalid) {
    expect(() => parseMermaidManifest(document, 'probe'), JSON.stringify(document)).toThrow(/probe is invalid/);
  }

  expect(() => readMermaidManifest(testInfo.outputPath('missing.json'))).toThrow(/missing/);
  const malformedPath = testInfo.outputPath('malformed.json');
  fs.writeFileSync(malformedPath, '{', 'utf8');
  expect(() => readMermaidManifest(malformedPath)).toThrow();

  const origin = 'http://127.0.0.1';
  const cases: Array<[string, number]> = [
    [`${BASE}guide`, 2],
    [`${BASE}guide/`, 2],
    [`${BASE}guide/?q=query#section`, 2],
    [BASE, 1],
    [BASE.slice(0, -1), 1],
    [`${BASE}does-not-exist/`, 0],
  ];
  for (const [pathname, expected] of cases) {
    expect(expectedMermaidDiagrams(`${origin}${pathname}`, syntheticManifest), pathname).toBe(expected);
  }
  for (const outside of ['/other/guide', '/physical-ai-toolchain-preview/guide']) {
    expect(() => expectedMermaidDiagrams(`${origin}${outside}`, syntheticManifest)).toThrow(/outside/);
  }
});

test('readiness waits for hydration, fonts, diagrams, and a committed client-side route', async ({ page }) => {
  let holdFont: (route: Route) => void = () => undefined;
  const fontRequest = new Promise<Route>((resolve) => {
    holdFont = resolve;
  });
  await page.route(`**${PROBE}/probe.woff2`, (route) => holdFont(route));
  await serveProbe(page, 'diagram', '<main><h1>Original</h1></main>', false);
  await page.evaluate((url) => {
    const face = new FontFace('ReadinessProbe', `url(${url})`);
    document.fonts.add(face);
    void face.load().catch(() => undefined);
  }, `${PROBE}/probe.woff2`);
  const font = await fontRequest;

  const initial = track(expectPageReady(page, syntheticManifest));
  await expectStillPending(page, initial);
  await page.evaluate(() => document.documentElement.setAttribute('data-has-hydrated', 'true'));
  await expectStillPending(page, initial);
  await page.evaluate(() => {
    const container = document.createElement('div');
    container.className = 'docusaurus-mermaid-container';
    container.innerHTML = '<svg role="img" aria-label="Probe"></svg>';
    document.querySelector('main')?.append(container);
  });
  await expectStillPending(page, initial);
  await font.fulfill({ body: '', status: 404 });
  await expect.poll(() => initial.error ?? initial.settled).toBe(true);

  // The old route keeps one visible main and an equal diagram count while the destination is pending.
  await page.evaluate((url) => history.pushState({}, '', url), `${PROBE}/next`);
  const transition = track(expectPageReady(page, syntheticManifest));
  await expectStillPending(page, transition);
  await page.evaluate((canonical) => {
    document.querySelector('link[rel="canonical"]')?.setAttribute('href', canonical);
    const heading = document.querySelector('main h1');
    if (heading) heading.textContent = 'Next';
  }, `https://docs.invalid${PROBE}/next`);
  await expect.poll(() => transition.error ?? transition.settled).toBe(true);
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Next');
});

test('readiness completes on real zero-diagram routes, transitions, search results, and the not-found page', async ({
  page,
}) => {
  await page.goto(siteRoute('/'));
  await expectPageReady(page);
  expect(expectedMermaidDiagrams(page.url())).toBe(0);
  const homeHeading = await page.getByRole('heading', { level: 1 }).textContent();

  const link = page.getByRole('link', { name: 'Getting Started' }).first();
  const href = await link.getAttribute('href');
  await link.click();
  await page.waitForURL((url) => url.pathname === href);
  await expectPageReady(page);
  expect(expectedMermaidDiagrams(page.url())).toBe(0);
  await expect(page.getByRole('heading', { level: 1 })).not.toHaveText(homeHeading ?? '');
  expect(new URL((await page.locator('link[rel="canonical"]').getAttribute('href'))!).pathname).toBe(href);

  // Search results arrive asynchronously; readiness must not return before the page announces them.
  await page.goto(siteRoute('/search/?q=training'));
  await expectPageReady(page);
  expect(await page.locator('article[class*="searchResultItem"]').count()).toBeGreaterThan(0);

  const response = await page.goto(siteRoute('/does-not-exist/'));
  expect(response?.status()).toBe(404);
  await expectPageReady(page);
  expect(expectedMermaidDiagrams(page.url())).toBe(0);
});

test('readiness never succeeds without hydration, main content, or an expected diagram', async ({ context }) => {
  const probe = async (route: string, body: string, hydrated: boolean): Promise<Page> => {
    const page = await context.newPage();
    await serveProbe(page, route, body, hydrated);
    return page;
  };

  const noHydrationPage = await probe('no-hydration', '<main><h1>Not hydrated</h1></main>', false);
  await expectStillPending(noHydrationPage, track(expectPageReady(noHydrationPage, syntheticManifest)));

  const missingPage = await probe('missing', '<main><h1>Missing diagram</h1></main>', true);
  await expectStillPending(missingPage, track(expectPageReady(missingPage, syntheticManifest)));

  const noMainPage = await probe('no-main', '<div><h1>No main</h1></div>', true);
  await expect(expectPageReady(noMainPage, syntheticManifest)).rejects.toThrow(/toHaveCount/);
  const twoMainsPage = await probe('two-mains', '<main>One</main><main>Two</main>', true);
  await expect(expectPageReady(twoMainsPage, syntheticManifest)).rejects.toThrow(/toHaveCount/);
});
