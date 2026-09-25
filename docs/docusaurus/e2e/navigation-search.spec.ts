// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

import { expect, test } from '@playwright/test';

import labelData from '../src/data/labelRegistry.cjs';
import {
  expectNoUnexpectedPageErrors,
  expectPageReady,
  siteRoute,
  watchUnexpectedPageErrors,
} from './accessibility-helpers';
import { evidence } from './evidence-cell-reporter';
test.beforeEach(({ page }) => watchUnexpectedPageErrors(page));
test.afterEach(({ page }) => expectNoUnexpectedPageErrors(page));

test('skip link and route changes move focus to main content', evidence('dcs02-navigation', [
  { journeyId: 'DCS02', method: 'PLAYWRIGHT_KEYBOARD' },
  { journeyId: 'DCS02', method: 'PLAYWRIGHT_TREE' },
]), async ({ page }) => {
  await page.goto(siteRoute('/'));
  await expectPageReady(page);
  await expect(page.locator('main')).not.toBeFocused();
  await page.keyboard.press('Tab');
  const skipLink = page.getByText(/skip to main content/i);
  await expect(skipLink).toBeFocused();
  await page.locator('main').evaluate((main) => {
    main.addEventListener('focus', () => main.setAttribute('data-skip-focus-received', 'true'), { once: true });
  });
  await skipLink.press('Enter');
  await expect(page.locator('main')).toHaveAttribute('data-skip-focus-received', 'true');

  await page.getByRole('link', { name: 'Getting Started' }).first().click();
  await expect(page.locator('main')).toBeFocused();

  const hashLink = page.locator('.table-of-contents:visible a').first();
  await hashLink.focus();
  await hashLink.click();
  await expect(page.locator('main')).not.toBeFocused();

  await page.goBack();
  await expectPageReady(page);
  await expect(page.locator('main')).not.toBeFocused();
});

test('local search exposes a complete keyboard and announcement trace', evidence('dcs05-search', [
  { journeyId: 'DCS05', method: 'PLAYWRIGHT_KEYBOARD' },
  { journeyId: 'DCS05', method: 'PLAYWRIGHT_LIVE_REGION' },
  { journeyId: 'DCS05', method: 'PLAYWRIGHT_TREE' },
]), async ({ page }) => {
  await page.goto(siteRoute('/'));
  await expectPageReady(page);
  const search = page.locator('input.navbar__search-input');
  await expect(search).toHaveAccessibleName('Search documentation');
  await expect(search).not.toHaveAttribute('role', 'combobox');
  await expect(search).not.toHaveAttribute('aria-expanded', /.*/);
  await expect(search).not.toHaveAttribute('aria-activedescendant', /.*/);
  await expect(search).toHaveAccessibleDescription('Keyboard shortcut: Control plus K');
  await search.click();
  await expect(search).toBeFocused();
  await expect(search).toHaveAttribute('aria-autocomplete');
  await search.pressSequentially('training');
  await expect(search).toHaveAttribute('role', 'combobox');
  const listbox = page.getByRole('listbox');
  await expect(listbox).toBeVisible();
  await expect(search, 'opening populated results must preserve user focus').toBeFocused();
  const controls = await search.getAttribute('aria-controls');
  expect(controls).toBeTruthy();
  await expect(listbox).toHaveAttribute('id', controls!);
  const footer = listbox.locator('[class*="hitFooter"] a');
  await expect(footer).toHaveAttribute('role', 'option');
  await expect(footer).toHaveAttribute('tabindex', '-1');
  const options = listbox.getByRole('option');
  const resultCount = (await options.count()) - 1;
  expect(resultCount).toBeGreaterThan(0);
  await expect(page.getByRole('status')).toHaveText(
    `${resultCount} result${resultCount === 1 ? '' : 's'} for training`,
  );

  await search.press('ArrowDown');
  const lastResult = options.nth(resultCount - 1);
  const expectedLastResultId = await lastResult.getAttribute('id');
  expect(expectedLastResultId).toBeTruthy();
  for (let index = 0; index < resultCount; index += 1) {
    if (await search.getAttribute('aria-activedescendant') === expectedLastResultId) {
      break;
    }
    await search.press('ArrowDown');
  }
  await expect(search).toHaveAttribute('aria-activedescendant', expectedLastResultId!);
  await search.press('ArrowDown');
  await expect(search).toHaveAttribute('aria-activedescendant', await footer.getAttribute('id'));
  await expect(footer).toHaveAttribute('aria-selected', 'true');
  await expect(listbox.locator('[aria-selected="true"]')).toHaveCount(1);

  await search.press('ArrowUp');
  await expect(search).toHaveAttribute('aria-activedescendant', expectedLastResultId!);
  await expect(listbox.locator('[aria-selected="true"]')).toHaveCount(1);

  await search.press('Escape');
  await expect(search).toHaveAttribute('aria-expanded', 'false');
  await expect(search).not.toHaveAttribute('aria-activedescendant', /.*/);
  await expect(page.getByRole('status')).toBeEmpty();

  await search.fill('training');
  await expect(listbox).toBeVisible();
  await search.press('Tab');
  await expect(page.getByRole('button', { name: 'Clear search' })).toBeFocused();
  await page.keyboard.press('Tab');
  expect(await page.evaluate(() => document.activeElement?.tagName)).not.toBe('BODY');

  await search.click();
  await search.press('Shift+Tab');
  expect(await page.evaluate(() => document.activeElement?.tagName)).not.toBe('BODY');
  await expect(search).not.toBeFocused();
});

test('local search does not reclaim user-owned focus asynchronously', async ({ page }) => {
  await page.goto(siteRoute('/'));
  await expectPageReady(page);
  const search = page.locator('input.navbar__search-input');
  await search.click();
  await expect(search).toHaveAttribute('aria-autocomplete');

  const target = page.getByRole('link', { name: 'Getting Started' }).first();
  await target.evaluate((element) => HTMLElement.prototype.focus.call(element));
  await expect(target).toBeFocused();
  await search.evaluate((input) => (input as HTMLInputElement).focus());
  await expect(target).toBeFocused();
});

test('SearchPage restores bypass focus order and announces only settled results', evidence('dcs06-search-page', [
  { journeyId: 'DCS06', method: 'PLAYWRIGHT_KEYBOARD' },
  { journeyId: 'DCS06', method: 'PLAYWRIGHT_LIVE_REGION' },
  { journeyId: 'DCS06', method: 'PLAYWRIGHT_TREE' },
]), async ({ page }) => {
  await page.goto(siteRoute('/search/?q=training'));
  await expectPageReady(page);
  const searchStatus = page.locator('[id^="search-results-status-"]');
  await expect(searchStatus).toHaveText(/\d+ documents? found/);

  expect(await page.evaluate(() => ({
    activeClass: document.activeElement?.className ?? '',
    activeName: document.activeElement?.getAttribute('name') ?? '',
    activeTag: document.activeElement?.tagName ?? '',
  }))).toEqual({ activeClass: '', activeName: '', activeTag: 'BODY' });
  await page.keyboard.press('Tab');
  await expect(page.getByText(/skip to main content/i)).toBeFocused();

  await page.goto(siteRoute('/search/?q=98765432101234567890'));
  await expectPageReady(page);
  await expect(page.locator('[id^="search-results-status-"]')).toHaveText('No documents found');
});

test('tier navigation is keyboard operable and ordered', async ({ page }) => {
  await page.goto(siteRoute('/documentation/'));
  await expectPageReady(page);
  const sidebarItems = page.locator('.theme-doc-sidebar-menu > li');
  await expect(sidebarItems).toHaveCount(10);
  const labels = await sidebarItems.evaluateAll((items) =>
    items.map((item) =>
      item.querySelector(':scope > .menu__list-item-collapsible > .menu__link, :scope > .menu__link')?.textContent?.trim() ?? '',
    ),
  );
  expect(labels).toEqual([
    labelData.labelRegistry.documentation,
    ...labelData.tierNavigation.map(({ label }) => label),
    labelData.labelRegistry.lifecycle,
    labelData.labelRegistry.recipes,
    labelData.labelRegistry.referenceAndGovernance,
  ]);

  const tierDropdown = page.locator('.navbar__item.dropdown').filter({ hasText: labelData.labelRegistry.tiers });
  await tierDropdown.hover();
  const tierLinks = tierDropdown.locator('.dropdown__menu a');
  await expect(tierLinks).toHaveCount(labelData.tierNavigation.length);
  expect(await tierLinks.allTextContents()).toEqual(labelData.tierNavigation.map(({ label }) => label));
  expect(await tierLinks.evaluateAll((links) => links.map((link) => link.getAttribute('href')))).toEqual(
    labelData.tierNavigation.map(({ route }) => `/physical-ai-toolchain${route}`),
  );
});

test('local search numbers every option and settles on the first result', async ({ page }) => {
  await page.goto(siteRoute('/'));
  await expectPageReady(page);
  const search = page.locator('input.navbar__search-input');
  await search.click();
  await expect(search).toBeFocused();
  await expect(search).toHaveAttribute('aria-autocomplete');
  await search.fill('training');
  const listbox = page.getByRole('listbox');
  await expect(listbox).toBeVisible();
  const status = page.locator('.searchA11yWrapper [role="status"]');
  await expect(status).not.toBeEmpty();

  const options = listbox.getByRole('option');
  const optionCount = await options.count();
  expect(optionCount).toBeGreaterThan(1);
  expect(await options.evaluateAll((items) => items.map((item) => item.getAttribute('aria-posinset')))).toEqual(
    Array.from({ length: optionCount }, (_, index) => String(index + 1)),
  );
  expect(await options.evaluateAll((items) => items.map((item) => item.getAttribute('aria-setsize')))).toEqual(
    Array.from({ length: optionCount }, () => String(optionCount)),
  );

  await expect(options.first()).toHaveAttribute('aria-selected', 'true');
  await expect(listbox.locator('[aria-selected="true"]')).toHaveCount(1);
  await expect(search).toHaveAttribute('aria-activedescendant', (await options.first().getAttribute('id'))!);
  await expect(status).toHaveText(`${optionCount - 1} results for training`);

  const footer = listbox.getByRole('option', { name: 'See all results' });
  await expect(footer).toHaveAttribute('aria-posinset', String(optionCount));
  await expect(footer).toHaveAttribute('aria-selected', 'false');
});

test('local search reports an exact zero-result status without moving focus', async ({ page }) => {
  await page.goto(siteRoute('/'));
  await expectPageReady(page);
  const search = page.locator('input.navbar__search-input');
  await search.click();
  await expect(search).toBeFocused();
  await expect(search).toHaveAttribute('aria-autocomplete');
  await search.fill('98765432101234567890');
  const listbox = page.getByRole('listbox');
  await expect(listbox).toBeVisible();
  await expect(listbox.getByText('No results', { exact: true })).toBeVisible();
  await expect(listbox.getByRole('option')).toHaveCount(0);
  await expect(page.locator('.searchA11yWrapper [role="status"]')).toHaveText(
    '0 results for 98765432101234567890',
  );
  await expect(search).toHaveAttribute('aria-expanded', 'true');
  await expect(search).not.toHaveAttribute('aria-activedescendant', /.*/);
  await expect(search).toBeFocused();
});

test('local search clear control returns the field to its collapsed rest state', async ({ page }) => {
  await page.goto(siteRoute('/'));
  await expectPageReady(page);
  const search = page.locator('input.navbar__search-input');
  await search.click();
  await expect(search).toBeFocused();
  await expect(search).toHaveAttribute('aria-autocomplete');
  await search.fill('training');
  await expect(page.getByRole('listbox')).toBeVisible();

  const clear = page.getByRole('button', { name: 'Clear search' });
  await expect(clear).toHaveAccessibleName('Clear search');
  await clear.click();
  await expect(search).toHaveValue('');
  await expect(search).toHaveAttribute('aria-expanded', 'false');
  await expect(search).not.toHaveAttribute('aria-activedescendant', /.*/);
  await expect(page.locator('.searchA11yWrapper [role="status"]')).toBeEmpty();
  await expect(page.locator('[role="listbox"] [aria-selected="true"]')).toHaveCount(0);
});

test('local search reapplies its semantic contract to a reattached input', async ({ page }) => {
  await page.goto(siteRoute('/'));
  await expectPageReady(page);
  const search = page.locator('input.navbar__search-input');
  await search.evaluate((input) => input.setAttribute('data-search-instance', 'first'));
  await page.getByRole('link', { name: 'Getting Started' }).first().click();
  await expectPageReady(page);

  await expect(search).not.toHaveAttribute('data-search-instance', /.*/);
  await expect(search).toHaveAccessibleName('Search documentation');
  await expect(search).toHaveAccessibleDescription('Keyboard shortcut: Control plus K');
  await expect(search).not.toHaveAttribute('role', 'combobox');
  await expect(search).not.toHaveAttribute('aria-expanded', /.*/);
  await expect(search).toHaveValue('');
  await expect(page.locator('.searchA11yWrapper [role="status"]')).toBeEmpty();
});

test('SearchPage and navbar search own separate status regions', async ({ page }) => {
  await page.goto(siteRoute('/search/?q=training'));
  await expectPageReady(page);
  const results = page.locator('article[class*="searchResultItem"]');
  const resultCount = await results.count();
  expect(resultCount).toBeGreaterThan(0);

  await expect(page.getByRole('status')).toHaveCount(2);
  await expect(page.locator('[id^="search-results-status-"]')).toHaveText(`${resultCount} documents found`);
  await expect(page.locator('.searchA11yWrapper [role="status"]')).toBeEmpty();
  await expect(page.locator('input[name="q"]')).toHaveAccessibleName('Search');
  await expect(page.locator('input.navbar__search-input')).toHaveAccessibleName('Search documentation');
});

test('search and recover process opens a result and returns to a clean rest state', async ({ page }) => {
  await page.goto(siteRoute('/'));
  await expectPageReady(page);
  const search = page.locator('input.navbar__search-input');
  await search.click();
  await expect(search).toBeFocused();
  await expect(search).toHaveAttribute('aria-autocomplete');
  await search.fill('quickstart');
  const firstOption = page.getByRole('listbox').getByRole('option').first();
  await expect(firstOption).toHaveAttribute('aria-selected', 'true');
  const resultTitle = (await firstOption.locator('[class*="hitTitle"]').textContent())?.trim();
  expect(resultTitle).toBeTruthy();

  await search.press('Enter');
  await expectPageReady(page);
  await expect(page.getByRole('heading', { level: 1 })).toHaveText(resultTitle!);
  await expect(search).toHaveValue('');
  await expect(page.locator('.searchA11yWrapper [role="status"]')).toBeEmpty();

  await page.goBack();
  await expectPageReady(page);
  await expect(search).toHaveValue('');
  await expect(search).not.toHaveAttribute('aria-expanded', /.*/);
});

test('SearchPage recovers from a no-result query without duplicate announcements', async ({ page }) => {
  await page.goto(siteRoute('/'));
  await expectPageReady(page);
  const search = page.locator('input.navbar__search-input');
  await search.click();
  await expect(search).toBeFocused();
  await expect(search).toHaveAttribute('aria-autocomplete');
  await search.fill('training');
  await page.getByRole('listbox').getByRole('option', { name: 'See all results' }).click();
  await expectPageReady(page);
  expect(new URL(page.url()).searchParams.get('q')).toBe('training');

  const status = page.locator('[id^="search-results-status-"]');
  const query = page.locator('input[name="q"]');
  await expect(status).toHaveText(`${await page.locator('article[class*="searchResultItem"]').count()} documents found`);

  await query.fill('98765432101234567890');
  await expect(status).toHaveText('No documents found');
  await expect(page.locator('article[class*="searchResultItem"]')).toHaveCount(0);

  await query.fill('training');
  const recoveredResults = page.locator('article[class*="searchResultItem"]');
  await expect(recoveredResults.first()).toBeVisible();
  await expect(status).toHaveText(`${await recoveredResults.count()} documents found`);
  await expect(page.getByRole('status')).toHaveCount(2);
});

test('tiers disclosure toggles, traverses, and closes without trapping focus', evidence('dcs03-tiers', [
  { journeyId: 'DCS03', method: 'PLAYWRIGHT_KEYBOARD' },
  { journeyId: 'DCS03', method: 'PLAYWRIGHT_TREE' },
]), async ({ page }) => {
  await page.goto(siteRoute('/documentation/'));
  await expectPageReady(page);
  const trigger = page.locator('.navbar__item.dropdown > a').filter({ hasText: labelData.labelRegistry.tiers });
  await expect(trigger).toHaveRole('button');
  await expect(trigger).toHaveAttribute('aria-haspopup', 'true');
  await expect(trigger).toHaveAttribute('aria-expanded', 'false');

  await trigger.focus();
  await page.keyboard.press('Enter');
  await expect(trigger).toHaveAttribute('aria-expanded', 'true');
  await expect(trigger).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(trigger).toHaveAttribute('aria-expanded', 'false');
  await page.keyboard.press('Enter');
  await expect(trigger).toHaveAttribute('aria-expanded', 'true');

  const firstTier = page.locator('.navbar__item.dropdown .dropdown__menu a').first();
  await page.keyboard.press('Tab');
  await expect(firstTier).toBeFocused();
  await expect(trigger).toHaveAttribute('aria-expanded', 'true');
  await page.keyboard.press('Shift+Tab');
  await expect(trigger).toBeFocused();

  await page.locator('a.navbar__brand').focus();
  await expect(trigger).toHaveAttribute('aria-expanded', 'false');

  await trigger.focus();
  await page.keyboard.press('Enter');
  await page.keyboard.press('Tab');
  await page.keyboard.press('Enter');
  await expectPageReady(page);
  expect(new URL(page.url()).pathname).toBe(`/physical-ai-toolchain${labelData.tierNavigation[0].route}`);
  await expect(page.locator('main')).toBeFocused();
});

test('responsive navigation opens, activates a tier, and closes without trapping focus', evidence('dcs04-responsive-nav', [
  { journeyId: 'DCS04', method: 'PLAYWRIGHT_KEYBOARD' },
  { journeyId: 'DCS04', method: 'PLAYWRIGHT_TREE' },
]), async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(siteRoute('/'));
  await expectPageReady(page);
  const toggle = page.getByRole('button', { name: 'Toggle navigation bar' });
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await toggle.focus();
  await page.keyboard.press('Enter');
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  const sidebar = page.locator('.navbar-sidebar');
  await expect(sidebar).toBeVisible();

  const caret = sidebar.locator('.menu__list-item-collapsible button.menu__caret');
  await expect(caret).toHaveAttribute('aria-expanded', 'false');
  await expect(caret).toHaveAccessibleName('Expand the dropdown');
  await caret.focus();
  await page.keyboard.press('Enter');
  await expect(caret).toHaveAttribute('aria-expanded', 'true');
  await expect(caret).toHaveAccessibleName('Collapse the dropdown');

  const tierLink = sidebar.locator('a.menu__link').filter({ hasText: labelData.tierNavigation[0].label }).first();
  await tierLink.focus();
  await page.keyboard.press('Enter');
  await expectPageReady(page);
  expect(new URL(page.url()).pathname).toBe(`/physical-ai-toolchain${labelData.tierNavigation[0].route}`);
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await expect(sidebar).toBeHidden();
  await expect(page.locator('main')).toBeFocused();

  await toggle.focus();
  await page.keyboard.press('Enter');
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  const close = sidebar.getByRole('button', { name: 'Close navigation bar' });
  await close.focus();
  await page.keyboard.press('Enter');
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await expect(sidebar).toBeHidden();
  await page.keyboard.press('Tab');
  expect(await page.evaluate(() => document.activeElement?.tagName)).not.toBe('BODY');
});

test('browse and read process reaches an article and returns through site navigation', evidence('dcs07-browse-read', [
  { journeyId: 'DCS07', method: 'PLAYWRIGHT_KEYBOARD' },
  { journeyId: 'DCS07', method: 'PLAYWRIGHT_TREE' },
]), async ({ page }) => {
  await page.goto(siteRoute('/'));
  await expectPageReady(page);
  const gettingStarted = page.getByRole('link', { name: 'Getting Started' }).first();
  const gettingStartedHref = await gettingStarted.getAttribute('href');
  await gettingStarted.click();
  await expectPageReady(page);
  expect(new URL(page.url()).pathname).toBe(gettingStartedHref);
  await expect(page.locator('main')).toBeFocused();

  const sidebar = page.getByRole('navigation', { name: 'Docs sidebar' });
  const quickstart = sidebar.getByRole('link', { name: labelData.labelRegistry.quickstart });
  const quickstartHref = await quickstart.getAttribute('href');
  await quickstart.click();
  await expectPageReady(page);
  expect(new URL(page.url()).pathname).toBe(quickstartHref);
  await expect(page.locator('main')).toBeFocused();
  await expect(page.getByRole('heading', { level: 1 })).toHaveCount(1);

  const tocLink = page.locator('.table-of-contents:visible a').first();
  const tocHash = await tocLink.getAttribute('href');
  await tocLink.click();
  expect(new URL(page.url()).hash).toBe(tocHash);

  const pagination = page.getByRole('navigation', { name: 'Docs pages' });
  const nextLink = pagination.locator('.pagination-nav__link--next');
  const nextHref = await nextLink.getAttribute('href');
  await nextLink.focus();
  await page.keyboard.press('Enter');
  await expectPageReady(page);
  expect(new URL(page.url()).pathname).toBe(nextHref);
  await expect(page.locator('main')).toBeFocused();

  const breadcrumbs = page.getByRole('navigation', { name: 'Breadcrumbs' });
  await breadcrumbs.getByRole('link', { name: 'Home page' }).click();
  await expectPageReady(page);
  expect(new URL(page.url()).pathname).toBe('/physical-ai-toolchain/');
  await expect(page.locator('main')).toBeFocused();
});
