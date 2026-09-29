// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

import fs from 'node:fs';
import path from 'node:path';

import { expect, test } from '@playwright/test';

import {
  expectNoAccessibilityViolations,
  expectNoUnexpectedPageErrors,
  expectPageReady,
  siteRoute,
  watchUnexpectedPageErrors,
} from './accessibility-helpers';
import { evidence } from './evidence-cell-reporter';
import { representativeRoutes } from './representative-routes';
import { readDeployedRouteManifest } from './route-inventory';

interface MermaidManifest {
  schemaVersion: 1;
  diagrams: Array<{
    description: string;
    ordinal: number;
    route: string;
    sourcePath: string;
    title: string;
  }>;
}

const mermaidManifest = JSON.parse(
  fs.readFileSync(path.resolve(process.cwd(), 'build', 'mermaid-routes.json'), 'utf8'),
) as MermaidManifest;

const taskListRoute = '/contributing/security-review/';
const firstTierCategory = 'T0 - Dev (default)';

test.beforeEach(({ page }) => watchUnexpectedPageErrors(page));
test.afterEach(({ page }) => expectNoUnexpectedPageErrors(
  page,
  page.url().includes('/does-not-exist/') ? [/console: Failed to load resource:.*404/] : [],
));

for (const [name, route] of Object.entries(representativeRoutes)) {
  test(`${name} exposes a coherent template`, async ({ page }) => {
    await page.goto(siteRoute(route));
    await expectPageReady(page);
    await expect(page.getByRole('banner')).toHaveCount(1);
    await expect(page.getByRole('contentinfo')).toHaveCount(1);
    await expect(page.getByRole('heading', { level: 1 })).toHaveCount(1);
    const headingLevels = await page.locator('main h1, main h2, main h3, main h4, main h5, main h6').evaluateAll(
      (headings) => headings.map((heading) => Number(heading.tagName.slice(1))),
    );
    headingLevels.slice(1).forEach((level, index) => {
      expect(level).toBeLessThanOrEqual(headingLevels[index] + 1);
    });
  });
}

test('screen-reader calibration exposes product-shell positive controls', async ({ page }) => {
  await page.goto(siteRoute('/accessibility/screen-reader-calibration'));
  await expectPageReady(page);
  await expect(page.getByRole('banner')).toHaveCount(1);
  await expect(page.getByRole('contentinfo')).toHaveCount(1);
  await expect(page.getByRole('main')).toHaveCount(1);
  await expect(page.getByRole('heading', { level: 1, name: 'Screen reader calibration' })).toHaveCount(1);
  await expect(page.getByRole('heading', { level: 2, name: 'Interaction control' })).toHaveCount(1);
  await expect(page.getByRole('checkbox', { name: 'Accept terms' })).toHaveCount(1);
  await expectNoAccessibilityViolations(page);
});

test('article tables retain native semantics inside named keyboard regions', async ({ page }) => {
  await page.goto(siteRoute(representativeRoutes.tableAndAlert));
  await expectPageReady(page);
  const wrapper = page.locator('.tableWrapper').first();
  await expect(wrapper).toHaveAttribute('tabindex', '0');
  const table = wrapper.locator('table');
  await expect(table).toHaveCount(1);
  await expect(wrapper).toHaveAttribute('aria-labelledby', /\S+/);
  await expect(table).toHaveAttribute('aria-labelledby', await wrapper.getAttribute('aria-labelledby'));
  await expect(table).not.toHaveAttribute('tabindex', /.*/);
  await expectNoAccessibilityViolations(page);
});

test('Mermaid manifest maps every source diagram to a deployed route', () => {
  expect(mermaidManifest.schemaVersion).toBe(1);
  expect(mermaidManifest.diagrams.length).toBeGreaterThan(0);
  const deployedRoutes = new Set(readDeployedRouteManifest().routes);
  for (const diagram of mermaidManifest.diagrams) {
    expect(deployedRoutes.has(diagram.route), diagram.sourcePath).toBe(true);
  }
});

for (const diagram of mermaidManifest.diagrams) {
  for (const colorScheme of ['light', 'dark'] as const) {
    test(`Mermaid ${diagram.sourcePath} diagram ${diagram.ordinal} renders in ${colorScheme} mode`, async ({ page }) => {
      await page.emulateMedia({ colorScheme });
      await page.goto(siteRoute(diagram.route));
      await expectPageReady(page);
      await expect(page.locator('html')).toHaveAttribute('data-theme', colorScheme);
      const svg = page
        .locator('.docusaurus-mermaid-container')
        .nth(diagram.ordinal - 1)
        .locator('svg');
      await expect(svg).toHaveCount(1);
      await expect(svg).toHaveAccessibleName(diagram.title);
      await expect(svg).toHaveAccessibleDescription(diagram.description);
    });
  }
}

test('breadcrumbs retain local layout and current-page semantics', async ({ page }) => {
  await page.goto(siteRoute(representativeRoutes.article));
  await expectPageReady(page);
  const breadcrumbs = page.getByRole('navigation', { name: 'Breadcrumbs' });
  await expect(breadcrumbs).toBeVisible();
  await expect(breadcrumbs).toHaveCSS('margin-bottom', '12.8px');
  const currentPage = breadcrumbs.locator('.breadcrumbs__link[aria-current="page"]');
  await expect(currentPage).toHaveCount(1);
  expect((await currentPage.textContent())?.trim()).toBeTruthy();
});

test('footer headings label their link groups', async ({ page }) => {
  await page.goto(siteRoute(representativeRoutes.article));
  await expectPageReady(page);
  const footer = page.getByRole('contentinfo');
  await expect(footer.getByRole('heading', { level: 2, name: 'Site footer' })).toHaveCount(1);
  const columnHeadings = footer.getByRole('heading', { level: 3 });
  expect(await columnHeadings.count()).toBeGreaterThan(0);
  for (const heading of await columnHeadings.all()) {
    await expect(footer.locator(`ul[aria-labelledby="${await heading.getAttribute('id')}"]`)).toHaveCount(1);
  }
});

test('article TOCs expose desktop naming and a heading-safe mobile disclosure', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(siteRoute(representativeRoutes.article));
  await expectPageReady(page);
  const firstHeading = page.locator('main h1, main h2, main h3, main h4, main h5, main h6').first();
  await expect(firstHeading).toHaveJSProperty('tagName', 'H1');

  const button = page.getByRole('button', { name: 'On this page' });
  await expect(button).toBeVisible();
  await expect(button).toHaveAttribute('aria-expanded', 'false');
  const controlledId = await button.getAttribute('aria-controls');
  expect(controlledId).toBeTruthy();
  await expect(page.locator(`#${controlledId}`)).toHaveAttribute('aria-labelledby', await button.getAttribute('id'));
  await button.click();
  await expect(button).toHaveAttribute('aria-expanded', 'true');

  await page.setViewportSize({ width: 1280, height: 900 });
  const activeToc = page.locator('.table-of-contents:visible').first();
  await expect(activeToc).toHaveAttribute('aria-label', 'In this article');
});

test('dark article theme has no detectable accessibility violations', evidence('dcs08-dark-article', [
  { journeyId: 'DCS08', method: 'PLAYWRIGHT_VISUAL' },
]), async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.goto(siteRoute(representativeRoutes.tableAndAlert));
  await expectPageReady(page);
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await expectNoAccessibilityViolations(page);
});

test('sidebar categories expose named disclosures that toggle by keyboard', evidence('dcs11-sidebar', [
  { journeyId: 'DCS11', method: 'PLAYWRIGHT_KEYBOARD' },
  { journeyId: 'DCS11', method: 'PLAYWRIGHT_TREE' },
]), async ({ page }) => {
  await page.goto(siteRoute(representativeRoutes.hub));
  await expectPageReady(page);
  const collapsed = page.getByRole('button', { name: `Expand sidebar category '${firstTierCategory}'` });
  const expanded = page.getByRole('button', { name: `Collapse sidebar category '${firstTierCategory}'` });
  await expect(collapsed).toHaveAttribute('aria-expanded', 'false');

  await collapsed.focus();
  await page.keyboard.press('Enter');
  await expect(expanded).toHaveAttribute('aria-expanded', 'true');
  await expect(expanded).toBeFocused();

  await page.keyboard.press('Space');
  await expect(collapsed).toHaveAttribute('aria-expanded', 'false');
  await expect(collapsed).toBeFocused();
});

test('the documentation sidebar hides and restores through keyboard controls', async ({ page }) => {
  await page.goto(siteRoute(representativeRoutes.article));
  await expectPageReady(page);
  const menu = page.locator('.theme-doc-sidebar-menu');
  await expect(menu).toBeVisible();

  const collapse = page.getByRole('button', { name: 'Collapse sidebar', exact: true });
  await collapse.focus();
  await page.keyboard.press('Enter');
  await expect(menu).toBeHidden();

  const expand = page.getByRole('button', { name: 'Expand sidebar', exact: true });
  await expect(expand).toBeVisible();
  await expand.focus();
  await page.keyboard.press('Enter');
  await expect(menu).toBeVisible();
  await expect(page.getByRole('button', { name: 'Collapse sidebar', exact: true })).toBeVisible();
});

test('article tables scroll by keyboard inside their named region', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(siteRoute(representativeRoutes.tableAndAlert));
  await expectPageReady(page);
  const wrapper = page.locator('.tableWrapper').first();
  await expect(wrapper).toHaveRole('group');
  await expect(wrapper).toHaveAttribute('tabindex', '0');
  expect(await wrapper.evaluate((element) => element.scrollWidth - element.clientWidth)).toBeGreaterThan(0);

  const labelledBy = await wrapper.getAttribute('aria-labelledby');
  expect(labelledBy).toBeTruthy();
  const expectedName = await page.locator(`#${labelledBy}`).evaluate((heading) => {
    const hashLink = heading.querySelector('a.hash-link');
    const ownText = Array.from(heading.childNodes)
      .filter((node) => node !== hashLink)
      .map((node) => node.textContent ?? '')
      .join('')
      .trim();
    return `${ownText}${hashLink?.getAttribute('aria-label') ?? ''}`;
  });
  await expect(wrapper).toHaveAccessibleName(expectedName);

  await wrapper.focus();
  await expect(wrapper).toBeFocused();
  await page.keyboard.press('ArrowRight');
  await page.keyboard.press('ArrowRight');
  await expect.poll(() => wrapper.evaluate((element) => element.scrollLeft)).toBeGreaterThan(0);
  await page.keyboard.press('ArrowLeft');
  await page.keyboard.press('ArrowLeft');
  await expect.poll(() => wrapper.evaluate((element) => element.scrollLeft)).toBe(0);
});

test('task list checkboxes expose read-only state and label relationships', async ({ page }) => {
  await page.goto(siteRoute(taskListRoute));
  await expectPageReady(page);
  const tasks = page.locator('li.task-list-item');
  const taskCount = await tasks.count();
  expect(taskCount).toBeGreaterThan(0);
  const checkboxes = page.locator('li.task-list-item input[type="checkbox"]');
  await expect(checkboxes).toHaveCount(taskCount);

  for (const checkbox of await checkboxes.all()) {
    await expect(checkbox).toBeDisabled();
    await expect(checkbox).not.toBeChecked();
    const labelId = await checkbox.getAttribute('aria-labelledby');
    expect(labelId).toBeTruthy();
    const labelText = (await page.locator(`#${labelId}`).textContent())?.trim();
    expect(labelText).toBeTruthy();
    await expect(checkbox).toHaveAccessibleName(labelText!);
  }
});

test('responsive table of contents toggles by keyboard and resists pointer cancellation', evidence('dcs03-pointer-cancel', [
  { journeyId: 'DCS03', method: 'PLAYWRIGHT_POINTER' },
]), async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(siteRoute(representativeRoutes.article));
  await expectPageReady(page);
  const button = page.getByRole('button', { name: 'On this page' });
  await expect(button).toHaveAttribute('aria-expanded', 'false');

  const box = await button.boundingBox();
  expect(box).not.toBeNull();
  await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2);
  await page.mouse.down();
  await page.mouse.move(box!.x + box!.width / 2, Math.max(box!.y - 200, 0));
  await page.mouse.up();
  await expect(button).toHaveAttribute('aria-expanded', 'false');

  await button.focus();
  await page.keyboard.press('Space');
  await expect(button).toHaveAttribute('aria-expanded', 'true');
  await expect(button).toBeFocused();
  const region = page.locator(`#${await button.getAttribute('aria-controls')}`);
  await expect(region).toHaveRole('region');
  await expect(region.getByRole('link').first()).toBeVisible();

  await page.keyboard.press('Enter');
  await expect(button).toHaveAttribute('aria-expanded', 'false');
  await expect(button).toBeFocused();
});

test('the color mode control reports its current state without changing route', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'light' });
  await page.goto(siteRoute(representativeRoutes.article));
  await expectPageReady(page);
  const routeBefore = page.url();
  const toggle = page.locator('.navbar button[class*="toggleButton"]').first();
  await expect(toggle).toHaveAccessibleName('Switch between dark and light mode (currently system mode)');

  await toggle.focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await expect(page.locator('html')).toHaveAttribute('data-theme-choice', 'light');
  await expect(toggle).toHaveAccessibleName('Switch between dark and light mode (currently light mode)');

  await page.keyboard.press('Enter');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await expect(page.locator('html')).toHaveAttribute('data-theme-choice', 'dark');
  await expect(toggle).toHaveAccessibleName('Switch between dark and light mode (currently dark mode)');
  await expect(toggle).toBeFocused();
  expect(page.url()).toBe(routeBefore);
});

test('the repeated help mechanism keeps a stable position and names its external handoff', async ({ page }) => {
  const issuesHref = 'https://github.com/microsoft/physical-ai-toolchain/issues';
  const positions: number[] = [];

  for (const route of [representativeRoutes.home, representativeRoutes.article]) {
    await page.goto(siteRoute(route));
    await expectPageReady(page);
    const footer = page.getByRole('contentinfo');
    const hrefs = await footer.getByRole('link').evaluateAll((links) =>
      links.map((link) => link.getAttribute('href')),
    );
    positions.push(hrefs.indexOf(issuesHref));

    const issues = footer.locator(`a[href="${issuesHref}"]`);
    await expect(issues).toHaveCount(1);
    await expect(issues).toHaveAttribute('target', '_blank');
    await expect(issues).toHaveAttribute('rel', 'noopener noreferrer');
    await expect(issues.locator('svg')).toHaveAttribute('aria-label', '(opens in new tab)');
  }

  expect(positions[0]).toBeGreaterThanOrEqual(0);
  expect(positions[1]).toBe(positions[0]);
});
