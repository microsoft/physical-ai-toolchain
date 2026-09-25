// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

import { expect, test, type Page, type TestInfo } from '@playwright/test';

import {
  expectNoUnexpectedPageErrors,
  expectPageReady,
  siteRoute,
  watchUnexpectedPageErrors,
} from './accessibility-helpers';
import { evidence } from './evidence-cell-reporter';
import { representativeRoutes } from './representative-routes';

const layoutRoutes = [
  representativeRoutes.home,
  representativeRoutes.hub,
  representativeRoutes.article,
  representativeRoutes.search,
  representativeRoutes.notFound,
  representativeRoutes.tableAndAlert,
  representativeRoutes.mermaid,
];
const contentRoutes = [
  representativeRoutes.home,
  representativeRoutes.article,
  representativeRoutes.tableAndAlert,
  representativeRoutes.mermaid,
];

function parseRgb(color: string): [number, number, number] {
  const channels = color.match(/[\d.]+/g)?.slice(0, 3).map(Number);
  if (!channels || channels.length !== 3) {
    throw new Error(`Unsupported computed color: ${color}`);
  }
  return channels as [number, number, number];
}

function contrastRatio(foreground: string, background: string): number {
  const luminance = (color: string) => {
    const channels = parseRgb(color).map((channel) => {
      const normalized = channel / 255;
      return normalized <= 0.04045
        ? normalized / 12.92
        : ((normalized + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
  };
  const values = [luminance(foreground), luminance(background)].sort((left, right) => right - left);
  return (values[0] + 0.05) / (values[1] + 0.05);
}

test.beforeEach(({ page }) => watchUnexpectedPageErrors(page));
test.afterEach(({ page }, testInfo) => expectNoUnexpectedPageErrors(
  page,
  testInfo.title.includes('layouts reflow') || testInfo.title.includes('not-found')
    ? [/console: Failed to load resource:.*404/]
    : [],
));

test('representative layouts reflow without page-level overflow at 320 CSS pixels', evidence('dcs04-dcs09-reflow', [
  { journeyId: 'DCS04', method: 'PLAYWRIGHT_VISUAL' },
  { journeyId: 'DCS09', method: 'PLAYWRIGHT_VISUAL' },
]), async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 800 });
  for (const route of layoutRoutes) {
    await page.goto(siteRoute(route));
    await expectPageReady(page);
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow, route).toBeLessThanOrEqual(1);
  }
});

test('representative content remains visible and unclipped at 200 percent text size', async ({ page }) => {
  for (const route of contentRoutes) {
    await page.goto(siteRoute(route));
    await expectPageReady(page);
    const baselineSize = await page.evaluate(() => Number.parseFloat(getComputedStyle(document.documentElement).fontSize));
    await page.addStyleTag({ content: 'html { font-size: 200% !important; }' });
    const resizedSize = await page.evaluate(() => Number.parseFloat(getComputedStyle(document.documentElement).fontSize));
    expect(resizedSize, `${route} computed root text size`).toBeCloseTo(baselineSize * 2, 1);
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
    const control = page.locator('main a:visible, main button:visible, main input:visible').first();
    await expect(control).toBeVisible();
    const controlBox = await control.boundingBox();
    const viewport = page.viewportSize();
    expect(controlBox).not.toBeNull();
    expect(controlBox!.x).toBeGreaterThanOrEqual(0);
    expect(controlBox!.x + controlBox!.width).toBeLessThanOrEqual(viewport!.width + 1);
  }
});

test('representative search task remains functional through 200 percent text resize', evidence('dcs09-text-resize-task', [
  { journeyId: 'DCS09', method: 'PLAYWRIGHT_VISUAL' },
  { journeyId: 'DCS09', method: 'PLAYWRIGHT_KEYBOARD' },
]), async ({ page }, testInfo) => {
  const checkpoints = [];
  for (const scale of [1, 1.5, 2]) {
    await page.goto(siteRoute('/'));
    await expectPageReady(page);
    const baselineSize = await page.evaluate(() => Number.parseFloat(getComputedStyle(document.documentElement).fontSize));
    if (scale > 1) {
      await page.addStyleTag({ content: `html { font-size: ${scale * 100}% !important; }` });
    }
    const computedSize = await page.evaluate(() => Number.parseFloat(getComputedStyle(document.documentElement).fontSize));
    expect(computedSize, `${scale * 100}% computed root text size`).toBeCloseTo(baselineSize * scale, 1);

    const search = page.locator('input.navbar__search-input');
    await search.click();
    await search.fill('training');
    const listbox = page.getByRole('listbox');
    await expect(listbox).toBeVisible();
    await search.press('ArrowDown');
    await expect(listbox.locator('[aria-selected="true"]')).toHaveCount(1);
    await search.press('Escape');
    await expect(search).toHaveAttribute('aria-expanded', 'false');
    checkpoints.push({ scale, baselineSize, computedSize, task: 'search-open-select-close' });
  }
  await testInfo.attach('text-resize-checkpoints', {
    body: Buffer.from(JSON.stringify({ condition: 'css-text-resize', checkpoints }, null, 2)),
    contentType: 'application/json',
  });
});

test('representative content tolerates WCAG text spacing overrides', async ({ page }) => {
  for (const route of contentRoutes) {
    await page.goto(siteRoute(route));
    await expectPageReady(page);
    await page.addStyleTag({
      content: `
        body * {
          line-height: 1.5 !important;
          letter-spacing: 0.12em !important;
          word-spacing: 0.16em !important;
        }
        p { margin-bottom: 2em !important; }
      `,
    });
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow, route).toBeLessThanOrEqual(1);
  }
});

test('reduced motion removes meaningful animation and smooth scrolling', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  for (const route of contentRoutes) {
    await page.goto(siteRoute(route));
    await expectPageReady(page);
    const motion = await page.locator('main *').first().evaluate((element) => {
      const styles = getComputedStyle(element);
      return {
        animationDuration: Number.parseFloat(styles.animationDuration),
        scrollBehavior: getComputedStyle(document.documentElement).scrollBehavior,
        transitionDuration: Number.parseFloat(styles.transitionDuration),
      };
    });
    expect(motion.scrollBehavior, route).toBe('auto');
    expect(motion.animationDuration, route).toBeLessThanOrEqual(0.001);
    expect(motion.transitionDuration, route).toBeLessThanOrEqual(0.001);
  }
});

test('representative routes expose visible keyboard focus', evidence('dcs09-dcs10-focus', [
  { journeyId: 'DCS09', method: 'PLAYWRIGHT_KEYBOARD' },
  { journeyId: 'DCS10', method: 'PLAYWRIGHT_KEYBOARD' },
]), async ({ page }) => {
  for (const route of contentRoutes) {
    await page.goto(siteRoute(route));
    await expectPageReady(page);
    const link = page.locator('main a:visible').first();
    await link.focus();
    await expect(link).toBeFocused();
    const styles = await link.evaluate((element) => {
      const computed = getComputedStyle(element);
      return { outlineStyle: computed.outlineStyle, outlineWidth: computed.outlineWidth };
    });
    expect(styles.outlineStyle, route).not.toBe('none');
    expect(Number.parseFloat(styles.outlineWidth), route).toBeGreaterThanOrEqual(2);
  }
});

test('representative content links keep a non-color distinction', async ({ page }) => {
  for (const route of contentRoutes) {
    await page.goto(siteRoute(route));
    await expectPageReady(page);
    const links = route === representativeRoutes.home
      ? page.locator('main article a:visible')
      : page.locator('.markdown p a:visible, .markdown li a:visible, .markdown td a:visible');
    expect(await links.count(), route).toBeGreaterThan(0);
    for (const link of await links.all()) {
      const decoration = await link.evaluate((element) => getComputedStyle(element).textDecorationLine);
      expect(decoration, `${route}: ${await link.textContent()}`).toContain('underline');
    }
  }
});

test('home hero keeps high-contrast text over its gradient', async ({ page }) => {
  await page.goto(siteRoute(representativeRoutes.home));
  await expectPageReady(page);
  const hero = page.locator('main section').first();
  await expect(hero).toHaveCSS('background-image', /linear-gradient/);
  await expect(hero.getByRole('heading', { level: 1 })).toHaveCSS('color', 'rgb(255, 255, 255)');
  await expect(hero.locator('p')).toHaveCSS('color', 'rgb(255, 255, 255)');
});

test('expanded mobile search avoids brand overlap and preserves selected contrast', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 800 });
  await page.goto(siteRoute('/'));
  await expectPageReady(page);
  const search = page.locator('input.navbar__search-input');
  await search.click();
  await expect(search).toHaveAttribute('aria-autocomplete');
  await expect(search).toHaveCSS('background-image', 'none');
  await search.fill('training');
  await expect(page.getByRole('listbox')).toBeVisible();

  const overlap = await page.evaluate(() => {
    const brand = document.querySelector('.navbar__brand')?.getBoundingClientRect();
    const input = document.querySelector('input.navbar__search-input')?.getBoundingClientRect();
    return Boolean(brand && input && brand.right >= input.left && brand.left <= input.right &&
      brand.bottom >= input.top && brand.top <= input.bottom);
  });
  expect(overlap).toBe(false);

  await search.press('ArrowDown');
  const selected = page.locator('[role="listbox"] [role="option"][aria-selected="true"]');
  await expect(selected).toHaveCount(1);
  const colors = await selected.evaluate((element) => {
    const computed = getComputedStyle(element);
    const mark = element.querySelector('mark');
    return {
      background: computed.backgroundColor,
      foreground: computed.color,
      mark: mark ? getComputedStyle(mark).color : computed.color,
    };
  });
  expect(contrastRatio(colors.foreground, colors.background)).toBeGreaterThanOrEqual(4.5);
  expect(contrastRatio(colors.mark, colors.background)).toBeGreaterThanOrEqual(4.5);
});

test('forced colors preserve active and focus cues', evidence('dcs11-forced-colors', [
  { journeyId: 'DCS11', method: 'PLAYWRIGHT_VISUAL' },
]), async ({ page }) => {
  await page.emulateMedia({ forcedColors: 'active' });
  await page.goto(siteRoute('/documentation/'));
  await expectPageReady(page);
  const current = page.locator('.menu__link--active').first();
  await expect(current).toBeVisible();
  const link = page.locator('a:visible').first();
  await link.focus();
  await expect(link).toBeFocused();

  const search = page.locator('input.navbar__search-input');
  await search.click();
  await expect(search).toHaveAttribute('aria-autocomplete');
  await search.fill('training');
  await search.press('ArrowDown');
  const selected = page.locator('[role="listbox"] [role="option"][aria-selected="true"]');
  await expect(selected).toHaveCount(1);
  expect(await selected.evaluate((element) => getComputedStyle(element).forcedColorAdjust)).toBe('none');
  const descendantColors = await selected.locator('*').evaluateAll((elements) =>
    elements.map((element) => getComputedStyle(element).color),
  );
  expect(new Set(descendantColors).size).toBe(1);
});

test('mobile navigation contains keyboard focus and restores the toggle on close', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(siteRoute('/'));
  await expectPageReady(page);
  const menuButton = page.getByRole('button', { name: 'Toggle navigation bar' });
  await menuButton.focus();
  await menuButton.press('Enter');
  await expect(menuButton).toHaveAttribute('aria-expanded', 'true');
  let focusEnteredSidebar = false;
  for (let index = 0; index < 3 && !focusEnteredSidebar; index += 1) {
    await page.keyboard.press('Tab');
    focusEnteredSidebar = await page.evaluate(() =>
      document.querySelector('.navbar-sidebar')?.contains(document.activeElement) ?? false,
    );
  }
  expect(focusEnteredSidebar).toBe(true);

  await expect(page.locator('main')).toHaveAttribute('inert', '');
  await expect(page.locator('footer')).toHaveAttribute('inert', '');

  const sidebarControls = page.locator([
    '.navbar-sidebar__brand a:visible',
    '.navbar-sidebar__brand button:visible',
    '.navbar-sidebar__item:not([inert]) a:visible',
    '.navbar-sidebar__item:not([inert]) button:visible',
  ].join(', '));
  const controlCount = await sidebarControls.count();
  expect(controlCount).toBeGreaterThan(1);

  await sidebarControls.last().focus();
  await page.keyboard.press('Tab');
  await expect(sidebarControls.first()).toBeFocused();

  await sidebarControls.first().focus();
  await page.keyboard.press('Shift+Tab');
  await expect(sidebarControls.last()).toBeFocused();

  await page.keyboard.press('Escape');
  await expect(page.locator('.navbar-sidebar')).toBeHidden();
  await expect(menuButton).toBeFocused();
  await expect(page.locator('main')).not.toHaveAttribute('inert', '');
  await expect(page.locator('footer')).not.toHaveAttribute('inert', '');
});

test('primary mobile controls meet the 24 CSS-pixel target minimum', evidence('dcs10-targets', [
  { journeyId: 'DCS10', method: 'PLAYWRIGHT_POINTER' },
  { journeyId: 'DCS10', method: 'PLAYWRIGHT_VISUAL' },
]), async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(siteRoute('/'));
  await expectPageReady(page);
  const controls = [
    page.getByRole('button', { name: 'Toggle navigation bar' }),
    page.locator('input.navbar__search-input'),
  ];

  for (const control of controls) {
    const box = await control.boundingBox();
    expect(box).not.toBeNull();
    expect(box?.width).toBeGreaterThanOrEqual(24);
    expect(box?.height).toBeGreaterThanOrEqual(24);
  }
});

test('search clear and footer options meet the 24 CSS-pixel target minimum', async ({ page }) => {
  await page.goto(siteRoute('/'));
  await expectPageReady(page);
  const search = page.locator('input.navbar__search-input');
  await search.click();
  await expect(search).toHaveAttribute('aria-autocomplete');
  await search.fill('training');
  await expect(page.getByRole('listbox')).toBeVisible();

  const targets = [
    page.getByRole('button', { name: 'Clear search' }),
    page.getByRole('listbox').getByRole('option', { name: 'See all results' }),
  ];
  for (const target of targets) {
    const box = await target.boundingBox();
    expect(box).not.toBeNull();
    expect(Math.min(box?.width ?? 0, box?.height ?? 0)).toBeGreaterThanOrEqual(24);
  }
});

test('mobile navigation backdrop is a measured equivalent close control', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installProbe(page);
  await page.goto(siteRoute('/'));
  await expectPageReady(page);
  const menuButton = page.getByRole('button', { name: 'Toggle navigation bar' });
  await menuButton.click();
  await expect(page.locator('.navbar-sidebar')).toBeVisible();

  const closeButton = page.locator('button.navbar-sidebar__close');
  const closeBox = await closeButton.boundingBox();
  expect(closeBox).not.toBeNull();
  expect(Math.min(closeBox?.width ?? 0, closeBox?.height ?? 0)).toBeLessThan(24);

  const backdrop = page.locator('.navbar-sidebar__backdrop');
  const backdropBox = await backdrop.boundingBox();
  expect(backdropBox).not.toBeNull();
  expect(backdropBox?.width).toBeGreaterThanOrEqual(24);
  expect(backdropBox?.height).toBeGreaterThanOrEqual(24);
  const targetReport = await measurePointerTargets(page);
  expect(targetReport.equivalentControls).toEqual([
    expect.objectContaining({
      control: expect.stringMatching(/navbar-sidebar__close/),
      equivalent: expect.stringMatching(/navbar-sidebar__backdrop/),
      state: 'navbar-sidebar--show',
      action: 'close-mobile-navigation',
    }),
  ]);
  await backdrop.click({ position: { x: Math.max((backdropBox?.width ?? 0) - 10, 1), y: 20 } });
  await expect(page.locator('.navbar-sidebar')).toBeHidden();

  await menuButton.click();
  await expect(page.locator('.navbar-sidebar')).toBeVisible();
});

/*
 * DCS08-DCS10 adaptive, focus, and pointer geometry.
 *
 * The probe below is injected into every page under test and owns exhaustive
 * enumeration: visible text and interactive containers, narrow-object
 * exceptions, rendered motion, keyboard focus stops, and pointer targets.
 * Node-side tests own the reviewed template/state inventory and the pass rules.
 */

interface ProbeFinding {
  type: string;
  element: string;
  detail: string;
}

interface ProbeGeometryAudit {
  pageOverflowPx: number;
  textContainerCount: number;
  controlCount: number;
  controls: string[];
  narrowObjectExceptions: string[];
  findings: ProbeFinding[];
  motionFindings: ProbeFinding[];
}

interface ProbeFocusGroup {
  id: string;
  selector: string;
}

interface ProbeFocusCandidate {
  key: string;
  group: string;
  description: string;
}

interface ProbeRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

interface ProbeFocusStop {
  key: string | null;
  group: string | null;
  description: string;
  outlineStyle: string;
  outlineWidth: number;
  outlineColor: string;
  boxShadow: string;
  boxShadowColor: string | null;
  backgroundColor: string;
  rect: ProbeRect;
  viewport: { width: number; height: number };
  sampledPoints: number;
  coveredPoints: number;
  obscuredBy: string | null;
}

interface ProbeTargetReport {
  targetCount: number;
  inlineExceptions: string[];
  spacingExceptions: string[];
  equivalentControls: Array<{
    control: string;
    equivalent: string;
    state: string;
    action: string;
    width: number;
    height: number;
  }>;
  undersized: Array<{ description: string; width: number; height: number; nearest: number }>;
}

interface AccessibilityProbe {
  groups: ProbeFocusGroup[];
  audit(options: {
    tolerance: number;
    includeMotion: boolean;
    motionThresholdSeconds: number;
  }): ProbeGeometryAudit;
  tagFocusCandidates(options: {
    groups: ProbeFocusGroup[];
    excludeSelectors: string[];
    scopeSelector: string | null;
  }): { candidates: ProbeFocusCandidate[]; unexplained: string[] };
  startFocusTrace(): void;
  readFocusTrace(): ProbeFocusStop[];
  measureTargets(options: { minimum: number }): ProbeTargetReport;
}

declare global {
  interface Window {
    __a11yProbe: AccessibilityProbe;
  }
}

function installAccessibilityProbe(): void {
  const NARROW_OBJECT_SELECTOR =
    'table, pre, svg, figure, .tableWrapper, .docusaurus-mermaid-container, .theme-code-block, .mermaid, .tabs-container';
  const CONTROL_SELECTOR = [
    'a[href]',
    'button',
    'input:not([type="hidden"])',
    'select',
    'textarea',
    'summary',
    '[role="button"]',
    '[role="link"]',
    '[role="checkbox"]',
    '[role="tab"]',
    '[role="option"]',
    '[role="menuitem"]',
    '[tabindex]',
  ].join(', ');
  const TARGET_SELECTOR = [
    'a[href]',
    'button:not([disabled])',
    'input:not([disabled]):not([type="hidden"])',
    'select:not([disabled])',
    'textarea:not([disabled])',
    'summary',
    '[role="button"]',
    '[role="link"]',
    '[role="checkbox"]:not([aria-disabled="true"])',
    '[role="tab"]',
    '[role="option"]',
    '[role="menuitem"]',
  ].join(', ');
  const FOCUSABLE_SELECTOR =
    'a[href], button, input:not([type="hidden"]), select, textarea, summary, iframe, [tabindex]';
  const OFFSCREEN_BY_DESIGN_SELECTOR = '[class*="skipToContent"], .srOnly, .sr-only';

  const trace: ProbeFocusStop[] = [];
  // Forward and reverse traversal visit the same controls, so each control is
  // measured once per tagging pass.
  const measured = new Map<Element, ProbeFocusStop>();
  let excludedSelectors: string[] = [];

  const classList = (element: Element): string[] => {
    const raw = element.getAttribute('class');
    return raw ? raw.trim().split(/\s+/).filter(Boolean) : [];
  };

  // Only direct text nodes are read so describing a large container stays cheap.
  const shortText = (element: Element): string => {
    let text = '';
    for (const node of Array.from(element.childNodes)) {
      if (node.nodeType === Node.TEXT_NODE) {
        text += node.textContent ?? '';
      }
      if (text.length > 64) {
        break;
      }
    }
    if (!text.trim()) {
      text = element.getAttribute('aria-label') ?? element.getAttribute('title') ?? '';
    }
    return text.replace(/\s+/g, ' ').trim().slice(0, 48);
  };

  const describe = (element: Element): string => {
    const tag = element.tagName.toLowerCase();
    const id = element.id ? `#${element.id}` : '';
    const classes = classList(element).slice(0, 2);
    const text = shortText(element);
    return `${tag}${id}${classes.length ? `.${classes.join('.')}` : ''}${text ? ` "${text}"` : ''}`;
  };

  const describeContainer = (element: Element): string => {
    const classes = classList(element).slice(0, 2);
    return `${element.tagName.toLowerCase()}${element.id ? `#${element.id}` : ''}${classes.length ? `.${classes.join('.')}` : ''}`;
  };

  const matchesSafely = (element: Element, selector: string): boolean => {
    try {
      return element.matches(selector);
    } catch {
      return false;
    }
  };

  const closestSafely = (element: Element, selector: string): Element | null => {
    try {
      return element.closest(selector);
    } catch {
      return null;
    }
  };

  const isRendered = (element: Element, style: CSSStyleDeclaration, rect: DOMRect): boolean => {
    if (!element.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) {
      return false;
    }
    if (style.display === 'none' || style.visibility === 'hidden') {
      return false;
    }
    if (element.getClientRects().length === 0) {
      return false;
    }
    if (rect.width <= 1 || rect.height <= 1) {
      return false;
    }
    if (Number.parseFloat(style.opacity) === 0) {
      return false;
    }
    if (style.clipPath === 'inset(50%)') {
      return false;
    }
    if (style.clip !== 'auto' && style.clip.replace(/\s/g, '') === 'rect(0px,0px,0px,0px)') {
      return false;
    }
    return true;
  };

  const hasDirectText = (element: Element): boolean =>
    Array.from(element.childNodes).some(
      (node) => node.nodeType === Node.TEXT_NODE && (node.textContent ?? '').trim().length > 0,
    );

  // Content translated completely outside a clipping container is an off-canvas
  // panel rather than presented content, so it is excluded from every check. The
  // document body is skipped because an open-overlay scroll lock clips it too.
  const isClippedAway = (element: Element, rect: DOMRect): boolean => {
    let node = element.parentElement;
    while (node && node !== document.body) {
      const style = getComputedStyle(node);
      const bounds = node.getBoundingClientRect();
      if (
        (style.overflowX === 'hidden' || style.overflowX === 'clip') &&
        (rect.right <= bounds.left + 1 || rect.left >= bounds.right - 1)
      ) {
        return true;
      }
      if (
        (style.overflowY === 'hidden' || style.overflowY === 'clip') &&
        (rect.bottom <= bounds.top + 1 || rect.top >= bounds.bottom - 1)
      ) {
        return true;
      }
      node = node.parentElement;
    }
    return false;
  };

  const controlKey = (element: Element): string => {
    const name =
      element.getAttribute('aria-label') ??
      element.getAttribute('title') ??
      element.getAttribute('placeholder') ??
      (element.textContent ?? '').replace(/\s+/g, ' ').trim();
    return [
      element.tagName.toLowerCase(),
      element.getAttribute('role') ?? '',
      name.slice(0, 60),
      element.getAttribute('href') ?? '',
    ].join('|');
  };

  const horizontalScrollAncestor = (element: Element): Element | null => {
    let node = element.parentElement;
    while (node && node !== document.body) {
      const style = getComputedStyle(node);
      if (
        (style.overflowX === 'auto' || style.overflowX === 'scroll') &&
        node.scrollWidth > node.clientWidth + 1
      ) {
        return node;
      }
      node = node.parentElement;
    }
    return null;
  };

  const isPositioned = (element: Element): boolean => {
    let node: Element | null = element;
    while (node && node !== document.body) {
      const position = getComputedStyle(node).position;
      if (position === 'absolute' || position === 'fixed' || position === 'sticky') {
        return true;
      }
      node = node.parentElement;
    }
    return false;
  };

  const maxSeconds = (value: string): number =>
    value.split(',').reduce((largest, part) => {
      const parsed = Number.parseFloat(part);
      return Number.isFinite(parsed) ? Math.max(largest, parsed) : largest;
    }, 0);

  const resolveBackground = (element: Element): string => {
    let node: Element | null = element;
    while (node) {
      const color = getComputedStyle(node).backgroundColor;
      const alpha = color.startsWith('rgba') ? Number.parseFloat(color.split(',')[3] ?? '1') : 1;
      if (color && color !== 'transparent' && alpha > 0) {
        return color;
      }
      node = node.parentElement;
    }
    return 'rgb(255, 255, 255)';
  };

  const resolveGroup = (element: Element, groups: ProbeFocusGroup[]): string | null => {
    for (const group of groups) {
      if (matchesSafely(element, group.selector)) {
        return group.id;
      }
    }
    return null;
  };

  const audit = (options: {
    tolerance: number;
    includeMotion: boolean;
    motionThresholdSeconds: number;
  }): ProbeGeometryAudit => {
    const root = document.documentElement;
    const viewportWidth = root.clientWidth;
    const findings: ProbeFinding[] = [];
    const motionFindings: ProbeFinding[] = [];
    const narrowObjectExceptions: string[] = [];
    const controls: string[] = [];

    const rendered: Array<{ element: Element; style: CSSStyleDeclaration; rect: DOMRect }> = [];
    for (const element of Array.from(document.body.querySelectorAll('*'))) {
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      if (!isRendered(element, style, rect) || isClippedAway(element, rect)) {
        continue;
      }
      rendered.push({ element, style, rect });
    }

    const candidates = rendered.filter(
      ({ element }) => hasDirectText(element) || matchesSafely(element, CONTROL_SELECTOR),
    );
    let textContainerCount = 0;

    for (const { element, style, rect } of candidates) {
      if (hasDirectText(element)) {
        textContainerCount += 1;
      }
      if (matchesSafely(element, CONTROL_SELECTOR)) {
        controls.push(controlKey(element));
      }
      if (closestSafely(element, OFFSCREEN_BY_DESIGN_SELECTOR)) {
        continue;
      }

      const narrowObject = closestSafely(element, NARROW_OBJECT_SELECTOR);
      if (rect.right > viewportWidth + options.tolerance || rect.left < -options.tolerance) {
        const container = horizontalScrollAncestor(element);
        const containerIsNarrowObject =
          Boolean(narrowObject) ||
          Boolean(container && closestSafely(container, NARROW_OBJECT_SELECTOR)) ||
          Boolean(container && container.querySelector(NARROW_OBJECT_SELECTOR));
        if (containerIsNarrowObject) {
          narrowObjectExceptions.push(describe(narrowObject ?? container ?? element));
        } else if (container) {
          findings.push({
            type: 'local-overflow',
            element: describe(element),
            detail: `requires horizontal scrolling inside ${describe(container)}`,
          });
        } else {
          findings.push({
            type: 'off-viewport',
            element: describe(element),
            detail: `left ${Math.round(rect.left)} right ${Math.round(rect.right)} viewport ${viewportWidth}`,
          });
        }
      }

      const clips =
        style.overflow === 'hidden' ||
        style.overflow === 'clip' ||
        style.overflowX === 'hidden' ||
        style.overflowY === 'hidden';
      if (
        clips &&
        hasDirectText(element) &&
        style.display !== 'inline' &&
        !narrowObject &&
        (element.scrollWidth > element.clientWidth + 1 || element.scrollHeight > element.clientHeight + 1)
      ) {
        findings.push({
          type: 'clipped',
          element: describe(element),
          detail: `content ${element.scrollWidth}x${element.scrollHeight} fits ${element.clientWidth}x${element.clientHeight}`,
        });
      }
    }

    // Multi-fragment inline boxes report a union rect that overlaps unrelated lines,
    // and SVG internals own their own scaled coordinate space.
    const overlapCandidates = candidates.filter(
      ({ element }) =>
        !isPositioned(element) &&
        element.getClientRects().length === 1 &&
        !closestSafely(element, 'svg'),
    );
    for (let first = 0; first < overlapCandidates.length; first += 1) {
      for (let second = first + 1; second < overlapCandidates.length; second += 1) {
        const left = overlapCandidates[first];
        const right = overlapCandidates[second];
        if (left.element.contains(right.element) || right.element.contains(left.element)) {
          continue;
        }
        const width = Math.min(left.rect.right, right.rect.right) - Math.max(left.rect.left, right.rect.left);
        const height = Math.min(left.rect.bottom, right.rect.bottom) - Math.max(left.rect.top, right.rect.top);
        if (width <= 1 || height <= 1) {
          continue;
        }
        const smallest = Math.min(
          left.rect.width * left.rect.height,
          right.rect.width * right.rect.height,
        );
        if (width * height > smallest * 0.25) {
          findings.push({
            type: 'overlap',
            element: describe(left.element),
            detail: `overlaps ${describe(right.element)} by ${Math.round(width)}x${Math.round(height)}`,
          });
        }
      }
    }

    if (options.includeMotion) {
      const threshold = options.motionThresholdSeconds;
      if (getComputedStyle(root).scrollBehavior === 'smooth') {
        motionFindings.push({ type: 'scroll-behavior', element: 'html', detail: 'smooth' });
      }
      for (const { element, style } of rendered) {
        const layers: Array<[string, CSSStyleDeclaration]> = [
          ['', style],
          ['::before', getComputedStyle(element, '::before')],
          ['::after', getComputedStyle(element, '::after')],
        ];
        for (const [pseudo, computed] of layers) {
          if (computed.animationName !== 'none' && maxSeconds(computed.animationDuration) > threshold) {
            motionFindings.push({
              type: 'animation',
              element: `${describe(element)}${pseudo}`,
              detail: `${computed.animationName} ${computed.animationDuration}`,
            });
          }
          if (computed.transitionProperty !== 'none' && maxSeconds(computed.transitionDuration) > threshold) {
            motionFindings.push({
              type: 'transition',
              element: `${describe(element)}${pseudo}`,
              detail: `${computed.transitionProperty} ${computed.transitionDuration}`,
            });
          }
        }
        if (style.scrollBehavior === 'smooth') {
          motionFindings.push({ type: 'scroll-behavior', element: describe(element), detail: 'smooth' });
        }
      }
      for (const animation of document.getAnimations()) {
        const effect = animation.effect;
        const duration = effect ? effect.getTiming().duration : 0;
        const seconds = typeof duration === 'number' ? duration / 1000 : 0;
        if (seconds > threshold) {
          const target = effect && 'target' in effect ? (effect as KeyframeEffect).target : null;
          motionFindings.push({
            type: 'running-animation',
            element: target ? describe(target) : 'detached effect',
            detail: `${seconds}s`,
          });
        }
      }
    }

    return {
      pageOverflowPx: root.scrollWidth - root.clientWidth,
      textContainerCount,
      controlCount: controls.length,
      controls,
      narrowObjectExceptions: Array.from(new Set(narrowObjectExceptions)),
      findings,
      motionFindings,
    };
  };

  const tagFocusCandidates = (options: {
    groups: ProbeFocusGroup[];
    excludeSelectors: string[];
    scopeSelector: string | null;
  }): { candidates: ProbeFocusCandidate[]; unexplained: string[] } => {
    window.__a11yProbe.groups = options.groups;
    measured.clear();
    excludedSelectors = options.excludeSelectors;
    for (const tagged of Array.from(document.querySelectorAll('[data-a11y-focus-key]'))) {
      tagged.removeAttribute('data-a11y-focus-key');
    }
    const scope = options.scopeSelector ? document.querySelector(options.scopeSelector) : null;
    if (options.scopeSelector && !scope) {
      throw new Error(`Focus scope not found: ${options.scopeSelector}`);
    }
    const candidates: ProbeFocusCandidate[] = [];
    const unexplained: string[] = [];
    let index = 0;
    for (const element of Array.from((scope ?? document).querySelectorAll(FOCUSABLE_SELECTOR))) {
      if ((element as HTMLElement & { disabled?: boolean }).disabled) {
        continue;
      }
      if (element.getAttribute('tabindex') === '-1') {
        continue;
      }
      if (closestSafely(element, '[aria-hidden="true"], [inert]')) {
        continue;
      }
      if (options.excludeSelectors.some((selector) => matchesSafely(element, selector))) {
        continue;
      }
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      if (!isRendered(element, style, rect) || isClippedAway(element, rect)) {
        continue;
      }
      const group = resolveGroup(element, options.groups);
      if (!group) {
        unexplained.push(describe(element));
        continue;
      }
      const key = `f${index}`;
      index += 1;
      element.setAttribute('data-a11y-focus-key', key);
      candidates.push({ key, group, description: describe(element) });
    }
    return { candidates, unexplained };
  };

  // Keyboard focus is the measuring moment: it establishes the focus-visible
  // styles and the scroll position the browser actually uses.
  const recordFocusStop = (element: Element): void => {
    const cached = measured.get(element);
    if (cached) {
      trace.push(cached);
      return;
    }
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    const left = Math.max(rect.left, 0);
    const top = Math.max(rect.top, 0);
    const right = Math.min(rect.right, window.innerWidth);
    const bottom = Math.min(rect.bottom, window.innerHeight);
    const points: Array<[number, number]> =
      right - left > 0 && bottom - top > 0
        ? [
            [left + 2, top + 2],
            [right - 2, top + 2],
            [left + 2, bottom - 2],
            [right - 2, bottom - 2],
            [(left + right) / 2, (top + bottom) / 2],
          ]
        : [];
    let sampledPoints = 0;
    let coveredPoints = 0;
    let obscuredBy: string | null = null;
    for (const [x, y] of points) {
      sampledPoints += 1;
      const hit = document.elementFromPoint(x, y);
      if (!hit || hit === element || element.contains(hit) || hit.contains(element)) {
        continue;
      }
      // Decoration painted inside the control's own box cannot hide its focus ring.
      const hitRect = hit.getBoundingClientRect();
      if (
        hitRect.left >= rect.left - 1 &&
        hitRect.right <= rect.right + 1 &&
        hitRect.top >= rect.top - 1 &&
        hitRect.bottom <= rect.bottom + 1
      ) {
        continue;
      }
      coveredPoints += 1;
      let overlay: Element | null = hit;
      while (overlay && overlay !== document.body) {
        const position = getComputedStyle(overlay).position;
        if (position === 'fixed' || position === 'sticky' || position === 'absolute') {
          break;
        }
        overlay = overlay.parentElement;
      }
      obscuredBy =
        overlay && overlay !== document.body
          ? `${describe(hit)} within ${describeContainer(overlay)}`
          : describe(hit);
    }
    const stop: ProbeFocusStop = {
      key: element.getAttribute('data-a11y-focus-key'),
      group: resolveGroup(element, window.__a11yProbe.groups),
      description: describe(element),
      outlineStyle: style.outlineStyle,
      outlineWidth: Number.parseFloat(style.outlineWidth),
      outlineColor: style.outlineColor,
      boxShadow: style.boxShadow,
      boxShadowColor: /rgba?\([^)]*\)/.exec(style.boxShadow)?.[0] ?? null,
      backgroundColor: resolveBackground(element.parentElement ?? element),
      rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
      viewport: { width: window.innerWidth, height: window.innerHeight },
      sampledPoints,
      coveredPoints,
      obscuredBy,
    };
    measured.set(element, stop);
    trace.push(stop);
  };

  const measureTargets = (options: { minimum: number }): ProbeTargetReport => {
    const targets: Array<{ element: Element; rect: DOMRect; style: CSSStyleDeclaration }> = [];
    for (const element of Array.from(document.querySelectorAll(TARGET_SELECTOR))) {
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      if (!isRendered(element, style, rect) || isClippedAway(element, rect)) {
        continue;
      }
      if (closestSafely(element, OFFSCREEN_BY_DESIGN_SELECTOR)) {
        continue;
      }
      targets.push({ element, rect, style });
    }

    const inlineExceptions: string[] = [];
    const spacingExceptions: string[] = [];
    const equivalentControls: ProbeTargetReport['equivalentControls'] = [];
    const undersized: ProbeTargetReport['undersized'] = [];
    const radius = options.minimum / 2;

    for (const target of targets) {
      if (Math.min(target.rect.width, target.rect.height) >= options.minimum) {
        continue;
      }
      if (target.element.matches('button.navbar-sidebar__close')) {
        const navbar = closestSafely(target.element, 'nav.navbar-sidebar--show');
        const backdrop = document.querySelector('.navbar-sidebar__backdrop');
        if (navbar && backdrop) {
          const backdropStyle = getComputedStyle(backdrop);
          const backdropRect = backdrop.getBoundingClientRect();
          if (
            isRendered(backdrop, backdropStyle, backdropRect) &&
            Math.min(backdropRect.width, backdropRect.height) >= options.minimum
          ) {
            equivalentControls.push({
              control: describe(target.element),
              equivalent: describe(backdrop),
              state: 'navbar-sidebar--show',
              action: 'close-mobile-navigation',
              width: Math.round(backdropRect.width * 10) / 10,
              height: Math.round(backdropRect.height * 10) / 10,
            });
            continue;
          }
        }
      }
      const inline =
        target.style.display.startsWith('inline') &&
        target.element.tagName === 'A' &&
        Boolean(closestSafely(target.element, 'p, li, td, th, dd, dt, h1, h2, h3, h4, h5, h6, blockquote'));
      if (inline) {
        inlineExceptions.push(describe(target.element));
        continue;
      }
      const centerX = target.rect.left + target.rect.width / 2;
      const centerY = target.rect.top + target.rect.height / 2;
      let nearest = Number.POSITIVE_INFINITY;
      for (const other of targets) {
        if (other === target || other.element.contains(target.element) || target.element.contains(other.element)) {
          continue;
        }
        const otherUndersized = Math.min(other.rect.width, other.rect.height) < options.minimum;
        if (otherUndersized) {
          const otherX = other.rect.left + other.rect.width / 2;
          const otherY = other.rect.top + other.rect.height / 2;
          nearest = Math.min(nearest, Math.hypot(centerX - otherX, centerY - otherY));
          continue;
        }
        const clampedX = Math.max(other.rect.left, Math.min(centerX, other.rect.right));
        const clampedY = Math.max(other.rect.top, Math.min(centerY, other.rect.bottom));
        nearest = Math.min(nearest, Math.hypot(centerX - clampedX, centerY - clampedY) + radius);
      }
      if (nearest >= options.minimum) {
        spacingExceptions.push(describe(target.element));
        continue;
      }
      undersized.push({
        description: describe(target.element),
        width: Math.round(target.rect.width * 10) / 10,
        height: Math.round(target.rect.height * 10) / 10,
        nearest: Number.isFinite(nearest) ? Math.round(nearest * 10) / 10 : -1,
      });
    }

    return { targetCount: targets.length, inlineExceptions, spacingExceptions, equivalentControls, undersized };
  };

  window.__a11yProbe = {
    groups: [],
    audit,
    tagFocusCandidates,
    startFocusTrace: () => {
      trace.length = 0;
      // A scoped pass starts on an already-focused trigger, which fires no event.
      const active = document.activeElement;
      if (active instanceof Element && active !== document.body && active.hasAttribute('data-a11y-focus-key')) {
        recordFocusStop(active);
      }
    },
    readFocusTrace: () => trace.slice(),
    measureTargets,
  };

  document.addEventListener(
    'focusin',
    (event) => {
      const target = event.target;
      if (!(target instanceof Element) || target === document.body) {
        return;
      }
      // Controls that appear only while scrolling are outside the reviewed inventory.
      if (excludedSelectors.some((selector) => matchesSafely(target, selector))) {
        return;
      }
      recordFocusStop(target);
    },
    true,
  );
}

const adaptiveRoutes = {
  ...representativeRoutes,
  taskList: '/contributing/deployment-validation/',
  mermaidProxy: '/osmo-proxy/',
} as const;

const DESKTOP_VIEWPORT = { width: 1280, height: 720 };
const MOBILE_VIEWPORT = { width: 390, height: 844 };
const GEOMETRY_TOLERANCE_PX = 1;
const REDUCED_MOTION_THRESHOLD_SECONDS = 0.001;
const MINIMUM_TARGET_PX = 24;
const MINIMUM_FOCUS_CONTRAST = 3;
const EVIDENCE_STATES = new Set(['article', 'table-and-alert', 'mermaid-chunking', 'mobile-navigation-open']);
const DYNAMIC_FOCUS_SELECTORS = ['[class*="backToTopButton"]'];

const TEXT_SPACING_CSS = `
  body * {
    line-height: 1.5 !important;
    letter-spacing: 0.12em !important;
    word-spacing: 0.16em !important;
  }
  p { margin-bottom: 2em !important; }
`;

interface AdaptiveCondition {
  readonly id: string;
  readonly viewport?: { width: number; height: number };
  readonly style?: string;
  readonly colorScheme: 'light' | 'dark';
  readonly forcedColors: 'active' | 'none';
  readonly reducedMotion: 'reduce' | 'no-preference';
  readonly comparesControlLoss: boolean;
  readonly enumeratesMotion: boolean;
  readonly requiresMobileViewport: boolean;
}

function condition(overrides: Partial<AdaptiveCondition> & { id: string }): AdaptiveCondition {
  return {
    colorScheme: 'light',
    forcedColors: 'none',
    reducedMotion: 'no-preference',
    comparesControlLoss: false,
    enumeratesMotion: false,
    requiresMobileViewport: false,
    ...overrides,
  };
}

const resizeConditions = [
  condition({
    id: 'text-resize-200',
    style: 'html { font-size: 200% !important; }',
    comparesControlLoss: true,
  }),
  condition({ id: 'text-spacing', style: TEXT_SPACING_CSS, comparesControlLoss: true }),
];

const reflowConditions = [
  condition({ id: 'reflow-320', viewport: { width: 320, height: 800 }, requiresMobileViewport: true }),
  condition({
    id: 'text-spacing-at-320',
    viewport: { width: 320, height: 800 },
    style: TEXT_SPACING_CSS,
    comparesControlLoss: true,
    requiresMobileViewport: true,
  }),
  condition({ id: 'orientation-portrait', viewport: MOBILE_VIEWPORT, requiresMobileViewport: true }),
  condition({
    id: 'orientation-landscape',
    viewport: { width: 844, height: 390 },
    requiresMobileViewport: true,
  }),
];

const preferenceConditions = [
  condition({ id: 'theme-light' }),
  condition({ id: 'theme-dark', colorScheme: 'dark' }),
  condition({ id: 'forced-colors', forcedColors: 'active' }),
  condition({ id: 'reduced-motion', reducedMotion: 'reduce', enumeratesMotion: true }),
];

async function openDesktopNavigation(page: Page): Promise<void> {
  const toggle = page.locator('.navbar__item.dropdown > .navbar__link').first();
  await toggle.focus();
  await toggle.press('Enter');
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  await expect(page.locator('.navbar__item.dropdown .dropdown__menu a').first()).toBeVisible();
}

async function openMobileNavigation(page: Page): Promise<void> {
  const toggle = page.getByRole('button', { name: 'Toggle navigation bar' });
  await toggle.focus();
  await toggle.press('Enter');
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  await expect(page.locator('.navbar-sidebar')).toBeVisible();
}

async function openTableOfContents(page: Page): Promise<void> {
  const button = page.getByRole('button', { name: 'On this page' });
  await button.focus();
  await button.press('Enter');
  await expect(button).toHaveAttribute('aria-expanded', 'true');
  await expect(page.locator('a.table-of-contents__link').first()).toBeVisible();
}

async function openSearchResults(page: Page): Promise<void> {
  const input = page.locator('input.navbar__search-input');
  await input.click();
  await expect(input).toHaveAttribute('aria-autocomplete');
  await input.fill('training');
  await expect(page.getByRole('listbox')).toBeVisible();
  await expect(input).toBeFocused();
}

interface TemplateState {
  readonly id: string;
  readonly route: string;
  readonly kind: 'responsive' | 'desktop-only' | 'mobile-only';
  readonly open?: (page: Page) => Promise<void>;
  readonly focusScopeSelector?: string;
  readonly desktopGroups: readonly string[];
  readonly mobileGroups: readonly string[];
}

const globalDesktopGroups = [
  'skip-link',
  'navbar-brand',
  'navbar-link',
  'navbar-dropdown-toggle',
  'navbar-search-input',
  'color-mode-toggle',
  'footer-link',
];
const globalMobileGroups = [
  'skip-link',
  'navbar-brand',
  'navbar-search-input',
  'mobile-navigation-toggle',
  'footer-link',
];
const docDesktopGroups = [
  ...globalDesktopGroups,
  'doc-sidebar-link',
  'breadcrumb-link',
  'edit-page-link',
];

const templateStates: readonly TemplateState[] = [
  {
    id: 'home',
    route: adaptiveRoutes.home,
    kind: 'responsive',
    desktopGroups: [...globalDesktopGroups, 'content-link'],
    mobileGroups: [...globalMobileGroups, 'content-link'],
  },
  {
    id: 'documentation-hub',
    route: adaptiveRoutes.hub,
    kind: 'responsive',
    desktopGroups: docDesktopGroups,
    mobileGroups: globalMobileGroups,
  },
  {
    id: 'article',
    route: adaptiveRoutes.article,
    kind: 'responsive',
    desktopGroups: [...docDesktopGroups, 'toc-link', 'hash-link', 'code-scroll-region'],
    mobileGroups: [...globalMobileGroups, 'toc-disclosure', 'code-scroll-region'],
  },
  {
    id: 'search',
    route: adaptiveRoutes.search,
    kind: 'responsive',
    desktopGroups: [...globalDesktopGroups, 'search-page-input', 'search-page-result-link'],
    mobileGroups: [...globalMobileGroups, 'search-page-input', 'search-page-result-link'],
  },
  {
    id: 'not-found',
    route: adaptiveRoutes.notFound,
    kind: 'responsive',
    desktopGroups: globalDesktopGroups,
    mobileGroups: globalMobileGroups,
  },
  {
    id: 'table-and-alert',
    route: adaptiveRoutes.tableAndAlert,
    kind: 'responsive',
    desktopGroups: [...docDesktopGroups, 'table-scroll-region', 'hash-link'],
    mobileGroups: [...globalMobileGroups, 'table-scroll-region'],
  },
  {
    id: 'task-list',
    route: adaptiveRoutes.taskList,
    kind: 'responsive',
    desktopGroups: [...docDesktopGroups, 'hash-link'],
    mobileGroups: globalMobileGroups,
  },
  {
    id: 'mermaid-chunking',
    route: adaptiveRoutes.mermaid,
    kind: 'responsive',
    desktopGroups: [...docDesktopGroups, 'hash-link'],
    mobileGroups: globalMobileGroups,
  },
  {
    id: 'mermaid-osmo-proxy',
    route: adaptiveRoutes.mermaidProxy,
    kind: 'responsive',
    desktopGroups: [...docDesktopGroups, 'hash-link'],
    mobileGroups: globalMobileGroups,
  },
  {
    id: 'desktop-navigation-open',
    route: adaptiveRoutes.hub,
    kind: 'desktop-only',
    open: openDesktopNavigation,
    focusScopeSelector: '.navbar__item.dropdown',
    desktopGroups: ['navbar-dropdown-toggle', 'navbar-dropdown-link'],
    mobileGroups: [],
  },
  {
    id: 'mobile-navigation-open',
    route: adaptiveRoutes.home,
    kind: 'mobile-only',
    open: openMobileNavigation,
    focusScopeSelector: '.navbar-sidebar',
    desktopGroups: [],
    mobileGroups: ['mobile-sidebar-control'],
  },
  {
    id: 'toc-open',
    route: adaptiveRoutes.article,
    kind: 'mobile-only',
    open: openTableOfContents,
    desktopGroups: [],
    mobileGroups: [...globalMobileGroups, 'toc-disclosure', 'toc-link'],
  },
  {
    id: 'search-results-open',
    route: adaptiveRoutes.home,
    kind: 'responsive',
    open: openSearchResults,
    focusScopeSelector: '.searchA11yWrapper',
    desktopGroups: ['navbar-search-input', 'search-clear'],
    mobileGroups: ['navbar-search-input'],
  },
];

const focusGroups: readonly ProbeFocusGroup[] = [
  { id: 'skip-link', selector: '[class*="skipToContent"]' },
  { id: 'navbar-brand', selector: 'a.navbar__brand' },
  { id: 'navbar-dropdown-link', selector: '.dropdown__menu a' },
  { id: 'navbar-dropdown-toggle', selector: '.navbar__item.dropdown > .navbar__link' },
  { id: 'navbar-search-input', selector: 'input.navbar__search-input' },
  { id: 'search-clear', selector: '.searchA11yWrapper button, button[aria-label="Clear search"]' },
  { id: 'search-result-option', selector: '[role="listbox"] a, [role="listbox"] [role="option"]' },
  { id: 'color-mode-toggle', selector: 'button[class*="toggleButton"], button[class*="colorModeToggle"]' },
  { id: 'mobile-navigation-toggle', selector: 'button.navbar__toggle, button[aria-label="Toggle navigation bar"]' },
  { id: 'mobile-sidebar-control', selector: '.navbar-sidebar a, .navbar-sidebar button' },
  { id: 'navbar-link', selector: '.navbar__link, .navbar__items a, .navbar__items button' },
  {
    id: 'sidebar-collapse-control',
    selector: 'button[class*="collapseSidebarButton"], button[class*="expandButton"], button.menu__caret, .menu__list-item-collapsible button',
  },
  { id: 'doc-sidebar-link', selector: '.theme-doc-sidebar-menu a, a.menu__link' },
  { id: 'breadcrumb-link', selector: 'a.breadcrumbs__link' },
  { id: 'table-scroll-region', selector: '.tableWrapper[tabindex="0"]' },
  { id: 'code-scroll-region', selector: 'pre[tabindex], .theme-code-block pre' },
  { id: 'hash-link', selector: 'a.hash-link' },
  { id: 'toc-disclosure', selector: 'button[class*="tocCollapsibleButton"], button[class*="collapseButton"]' },
  { id: 'toc-link', selector: 'a.table-of-contents__link' },
  { id: 'pagination-link', selector: 'a.pagination-nav__link' },
  { id: 'edit-page-link', selector: 'a.theme-edit-this-page' },
  { id: 'back-to-top', selector: '[class*="backToTopButton"]' },
  { id: 'search-page-input', selector: 'input[name="q"]' },
  { id: 'search-page-result-link', selector: 'article[class*="searchResultItem"] a' },
  { id: 'footer-link', selector: 'footer a, .footer a' },
  {
    id: 'content-link',
    selector:
      'main a[href], main button, main input, main summary, [role="main"] a[href], [role="main"] button, [role="main"] input, [role="main"] summary',
  },
];

function formatFinding(finding: ProbeFinding): string {
  return `${finding.type}: ${finding.element} (${finding.detail})`;
}

async function installProbe(page: Page): Promise<void> {
  await page.addInitScript(installAccessibilityProbe);
}

function viewportFor(state: TemplateState, breakpoint: 'desktop' | 'mobile'): { width: number; height: number } {
  return breakpoint === 'mobile' || state.kind === 'mobile-only' ? MOBILE_VIEWPORT : DESKTOP_VIEWPORT;
}

async function settleAnimations(page: Page): Promise<void> {
  await page.evaluate(async () => {
    await Promise.race([
      Promise.allSettled(document.getAnimations().map((animation) => animation.finished)),
      new Promise((resolve) => {
        window.setTimeout(resolve, 600);
      }),
    ]);
  });
}

async function enterState(
  page: Page,
  state: TemplateState,
  options: {
    viewport: { width: number; height: number };
    colorScheme?: 'light' | 'dark';
    forcedColors?: 'active' | 'none';
    reducedMotion?: 'reduce' | 'no-preference';
  },
): Promise<void> {
  await page.setViewportSize(options.viewport);
  await page.emulateMedia({
    colorScheme: options.colorScheme ?? 'light',
    forcedColors: options.forcedColors ?? 'none',
    reducedMotion: options.reducedMotion ?? 'no-preference',
  });
  await page.goto(siteRoute(state.route));
  await expectPageReady(page);
  if (state.open) {
    await state.open(page);
  }
  await settleAnimations(page);
}

/*
 * Findings that remain open pending qualified-human review. Unlisted findings
 * fail immediately, and a listed entry that no longer reproduces fails as a
 * stale expected entry.
 */
interface ReviewedAdaptiveException {
  readonly id: string;
  readonly producer: 'layout' | 'focus' | 'target';
  readonly signature: RegExp;
  readonly states: readonly string[];
  readonly conditionIds: readonly string[];
  readonly rationale: string;
  readonly owner: string;
  readonly reentryTrigger: string;
}

const desktopTemplateStateIds = [
  'home',
  'documentation-hub',
  'article',
  'search',
  'not-found',
  'table-and-alert',
  'task-list',
  'mermaid-chunking',
  'mermaid-osmo-proxy',
  'desktop-navigation-open',
  'search-results-open',
];

const reviewedAdaptiveExceptions: readonly ReviewedAdaptiveException[] = [
  {
    id: 'navbar-title-truncated-under-text-only-resize',
    producer: 'layout',
    signature: /^clipped: b\.navbar__title\.text--truncate/,
    states: desktopTemplateStateIds,
    conditionIds: ['text-resize-200'],
    rationale:
      'Text-only 200 percent scaling keeps the desktop navbar breakpoint, so the Infima text--truncate site title collapses to an ellipsis. The accessible name and document title keep the full text.',
    owner: 'accessibility owner',
    reentryTrigger:
      'Qualified real browser zoom evidence for SC 1.4.4, or any navbar, breakpoint, or title styling change.',
  },
  {
    id: 'search-hit-preview-truncated-in-reflow',
    producer: 'layout',
    signature: /^clipped: span\.hit(Title|Path)_/,
    states: ['search-results-open'],
    conditionIds: ['reflow-320', 'text-spacing-at-320'],
    rationale:
      'The local search plugin renders fixed-width single-line result previews. The complete title and path remain in the accessible name and on the destination route.',
    owner: 'accessibility owner',
    reentryTrigger:
      'Qualified review of search result preview truncation, or any docusaurus-search-local upgrade.',
  },
];

interface ClassifiedFinding {
  readonly conditionId: string;
  readonly finding: string;
}

function classifyFindings(
  testInfo: TestInfo,
  producer: 'layout' | 'focus' | 'target',
  stateId: string,
  entries: readonly ClassifiedFinding[],
): { unexplained: string[]; reproduced: Set<string> } {
  const unexplained: string[] = [];
  const reproduced = new Set<string>();
  for (const entry of entries) {
    const exception = reviewedAdaptiveExceptions.find(
      (candidate) =>
        candidate.producer === producer &&
        candidate.states.includes(stateId) &&
        candidate.conditionIds.includes(entry.conditionId) &&
        candidate.signature.test(entry.finding),
    );
    if (!exception) {
      unexplained.push(`${entry.conditionId} ${entry.finding}`);
      continue;
    }
    reproduced.add(exception.id);
    testInfo.annotations.push({
      type: 'pending-review-exception',
      description: `${exception.id} (${entry.conditionId}): ${entry.finding}; owner ${exception.owner}; re-entry ${exception.reentryTrigger}`,
    });
  }
  return { unexplained, reproduced };
}

function staleExceptions(
  producer: 'layout' | 'focus' | 'target',
  stateId: string,
  conditionIds: readonly string[],
  reproduced: Set<string>,
): string[] {
  const applicable = new Set(conditionIds);
  return reviewedAdaptiveExceptions
    .filter(
      (entry) =>
        entry.producer === producer &&
        entry.states.includes(stateId) &&
        entry.conditionIds.some((conditionId) => applicable.has(conditionId)) &&
        !reproduced.has(entry.id),
    )
    .map((entry) => entry.id);
}

interface ConditionOutcome {
  readonly conditionId: string;
  readonly findings: string[];
  readonly pageOverflowPx: number;
  readonly textContainerCount: number;
  readonly controlCount: number;
  readonly lostControls: string[];
  readonly narrowObjectExceptions: string[];
}

async function auditState(
  page: Page,
  testInfo: TestInfo,
  state: TemplateState,
  applied: AdaptiveCondition,
): Promise<ConditionOutcome> {
  await enterState(page, state, {
    viewport: applied.viewport ?? viewportFor(state, state.kind === 'mobile-only' ? 'mobile' : 'desktop'),
    colorScheme: applied.colorScheme,
    forcedColors: applied.forcedColors,
    reducedMotion: applied.reducedMotion,
  });

  const baseOptions = {
    tolerance: GEOMETRY_TOLERANCE_PX,
    includeMotion: false,
    motionThresholdSeconds: REDUCED_MOTION_THRESHOLD_SECONDS,
  };
  const reference = applied.comparesControlLoss
    ? await page.evaluate((options) => window.__a11yProbe.audit(options), baseOptions)
    : null;

  if (applied.style) {
    await page.addStyleTag({ content: applied.style });
    await settleAnimations(page);
  }

  const audit = await page.evaluate((options) => window.__a11yProbe.audit(options), {
    ...baseOptions,
    includeMotion: applied.enumeratesMotion,
  });

  if (EVIDENCE_STATES.has(state.id)) {
    await testInfo.attach(`${state.id}-${applied.id}.png`, {
      body: await page.screenshot(),
      contentType: 'image/png',
    });
  }

  const retained = new Set(audit.controls);
  return {
    conditionId: applied.id,
    findings: [...audit.findings, ...audit.motionFindings].map(formatFinding),
    pageOverflowPx: audit.pageOverflowPx,
    textContainerCount: audit.textContainerCount,
    controlCount: audit.controlCount,
    lostControls: reference
      ? Array.from(new Set(reference.controls.filter((control) => !retained.has(control))))
      : [],
    narrowObjectExceptions: audit.narrowObjectExceptions,
  };
}

const conditionFamilies: ReadonlyArray<readonly [string, readonly AdaptiveCondition[]]> = [
  ['text resize and spacing', resizeConditions],
  ['reflow and orientation', reflowConditions],
  ['color and motion preferences', preferenceConditions],
];

for (const state of templateStates) {
  for (const [family, conditions] of conditionFamilies) {
    const applicable = conditions.filter(
      (entry) => !(entry.requiresMobileViewport && state.kind === 'desktop-only'),
    );
    if (applicable.length === 0) {
      continue;
    }
    test(`adaptive layout: ${state.id} under ${family}`, async ({ page }, testInfo) => {
      await installProbe(page);
      const outcomes: ConditionOutcome[] = [];
      for (const applied of applicable) {
        outcomes.push(await auditState(page, testInfo, state, applied));
      }

      const { unexplained, reproduced } = classifyFindings(
        testInfo,
        'layout',
        state.id,
        outcomes.flatMap((outcome) =>
          outcome.findings.map((finding) => ({ conditionId: outcome.conditionId, finding })),
        ),
      );
      for (const outcome of outcomes) {
        for (const narrowObject of outcome.narrowObjectExceptions) {
          testInfo.annotations.push({
            type: 'narrow-object-exception',
            description: `${outcome.conditionId}: ${narrowObject}`,
          });
        }
      }

      expect(unexplained, `${state.id} adaptive layout and motion`).toEqual([]);
      expect(
        outcomes
          .filter((outcome) => outcome.pageOverflowPx > GEOMETRY_TOLERANCE_PX)
          .map((outcome) => `${outcome.conditionId} overflows by ${outcome.pageOverflowPx}px`),
        `${state.id} page-level horizontal overflow`,
      ).toEqual([]);
      expect(
        outcomes.flatMap((outcome) =>
          outcome.lostControls.map((control) => `${outcome.conditionId} lost ${control}`),
        ),
        `${state.id} controls lost by adaptation`,
      ).toEqual([]);
      expect(
        outcomes
          .filter((outcome) => outcome.textContainerCount === 0 || outcome.controlCount === 0)
          .map((outcome) => outcome.conditionId),
        `${state.id} conditions with no enumerated content`,
      ).toEqual([]);
      expect(
        staleExceptions('layout', state.id, applicable.map((entry) => entry.id), reproduced),
        `${state.id} reviewed exceptions that no longer reproduce`,
      ).toEqual([]);
    });
  }
}

interface FocusRun {
  candidates: ProbeFocusCandidate[];
  unexplained: string[];
  forward: ProbeFocusStop[];
  reverse: ProbeFocusStop[];
  stops: ProbeFocusStop[];
}

async function moveFocusToLastTaggedCandidate(
  page: Page,
  candidates: ProbeFocusCandidate[],
): Promise<void> {
  const documentFocusStops = await page.locator(
    'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])',
  ).count();
  let activeKey = await page.evaluate(() => document.activeElement?.getAttribute('data-a11y-focus-key') ?? null);
  for (let step = 0; !activeKey && step < documentFocusStops + 1; step += 1) {
    await page.keyboard.press('Tab');
    activeKey = await page.evaluate(() => document.activeElement?.getAttribute('data-a11y-focus-key') ?? null);
  }
  const activeIndex = candidates.findIndex((candidate) => candidate.key === activeKey);
  if (activeIndex < 0) {
    throw new Error('Keyboard traversal did not enter the tagged focus scope');
  }
  for (let step = activeIndex; step < candidates.length - 1; step += 1) {
    await page.keyboard.press('Tab');
  }
  const finalKey = await page.evaluate(() => document.activeElement?.getAttribute('data-a11y-focus-key') ?? null);
  if (finalKey !== candidates.at(-1)?.key) {
    const lastCandidateRendered = await page.evaluate((key) => {
      const element = document.querySelector(`[data-a11y-focus-key="${key}"]`);
      return element?.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true }) ?? false;
    }, candidates.at(-1)?.key ?? '');
    if (!lastCandidateRendered) {
      return;
    }
    throw new Error(
      `Keyboard traversal did not reach the last tagged focus candidate: active=${String(activeKey)} final=${String(finalKey)} candidates=${candidates.map((candidate) => `${candidate.key}:${candidate.description}`).join(', ')}`,
    );
  }
}

async function tagFocusCandidates(page: Page, state: TemplateState): Promise<{
  candidates: ProbeFocusCandidate[];
  unexplained: string[];
}> {
  return page.evaluate(
    (options) => window.__a11yProbe.tagFocusCandidates(options),
    {
      groups: focusGroups as ProbeFocusGroup[],
      excludeSelectors: DYNAMIC_FOCUS_SELECTORS,
      scopeSelector: state.focusScopeSelector ?? null,
    },
  );
}

async function traceFocusDirection(
  page: Page,
  key: 'Tab' | 'Shift+Tab',
  expectedKeys: Set<string>,
): Promise<ProbeFocusStop[]> {
  await page.evaluate(() => window.__a11yProbe.startFocusTrace());
  const maximumSteps = expectedKeys.size * 2 + 20;
  let trace: ProbeFocusStop[] = [];
  for (let step = 0; step < maximumSteps; step += 1) {
    await page.keyboard.press(key);
    if ((step + 1) % 5 !== 0 && step + 1 !== maximumSteps) {
      continue;
    }
    trace = await page.evaluate(() => window.__a11yProbe.readFocusTrace());
    const reached = new Set(trace.map((stop) => stop.key).filter((value): value is string => Boolean(value)));
    if ([...expectedKeys].every((expected) => reached.has(expected))) {
      return trace;
    }
  }
  return trace;
}

async function measurePointerTargets(page: Page): Promise<ProbeTargetReport> {
  return page.evaluate(
    (options) => window.__a11yProbe.measureTargets(options),
    { minimum: MINIMUM_TARGET_PX },
  );
}

async function traverseFocus(
  page: Page,
  state: TemplateState,
  directions: 'forward' | 'reverse' | 'both',
): Promise<FocusRun> {
  await page.addStyleTag({ content: 'html, body { scroll-behavior: auto !important; }' });
  const tagged = await tagFocusCandidates(page, state);
  let forward: ProbeFocusStop[] = [];
  let reverse: ProbeFocusStop[] = [];
  let reverseCandidateKeys: Set<string> | null = null;

  if (directions === 'forward' || directions === 'both') {
    forward = await traceFocusDirection(page, 'Tab', new Set(tagged.candidates.map((candidate) => candidate.key)));
  }

  if (directions === 'reverse' || directions === 'both') {
    if (state.focusScopeSelector) {
      await moveFocusToLastTaggedCandidate(page, tagged.candidates);
      reverseCandidateKeys = new Set(await page.evaluate(() =>
        Array.from(document.querySelectorAll('[data-a11y-focus-key]'))
          .filter((element) => element.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true }))
          .map((element) => element.getAttribute('data-a11y-focus-key'))
          .filter((key): key is string => Boolean(key)),
      ));
    }
    const expectedKeys = reverseCandidateKeys ?? new Set(tagged.candidates.map((candidate) => candidate.key));
    reverse = await traceFocusDirection(page, 'Shift+Tab', expectedKeys);
  }

  return {
    candidates: reverseCandidateKeys
      ? tagged.candidates.filter((candidate) => reverseCandidateKeys.has(candidate.key))
      : tagged.candidates,
    unexplained: tagged.unexplained,
    forward,
    reverse,
    stops: [...forward, ...reverse],
  };
}

function assertKeyboardReachability(
  run: FocusRun,
  label: string,
  direction: 'forward' | 'reverse',
): void {
  const stops = direction === 'forward' ? run.forward : run.reverse;
  const reached = new Set(stops.map((stop) => stop.key));
  expect(run.candidates.length, `${label} focus candidates`).toBeGreaterThan(0);
  expect(run.unexplained, `${label} runtime focus candidates outside the reviewed inventory`).toEqual([]);
  expect(
    run.candidates.filter((candidate) => !reached.has(candidate.key)).map((candidate) => candidate.description),
    `${label} expected focus stops not reached by ${direction} keyboard traversal`,
  ).toEqual([]);
  expect(
    Array.from(new Set(stops.filter((stop) => !stop.key && !stop.group).map((stop) => stop.description))),
    `${label} unexplained runtime focus stops`,
  ).toEqual([]);
}

function focusPresentationFindings(
  stops: ProbeFocusStop[],
  options: { measuresContrast: boolean },
): string[] {
  const findings = new Set<string>();
  for (const stop of stops) {
    if (stop.outlineStyle === 'none' && stop.boxShadow === 'none') {
      findings.add(`focus-indicator-missing: ${stop.description}`);
    } else if (stop.outlineStyle !== 'none' && stop.outlineWidth < 2 && stop.boxShadow === 'none') {
      findings.add(`focus-indicator-thin: ${stop.description} ${stop.outlineWidth}px`);
    }
    if (stop.sampledPoints === 0) {
      findings.add(`focus-outside-viewport: ${stop.description}`);
      continue;
    }
    if (stop.coveredPoints === stop.sampledPoints) {
      findings.add(`focus-obscured: ${stop.description} hidden by ${stop.obscuredBy}`);
    }
    if (!options.measuresContrast) {
      continue;
    }
    // The project focus indicator is a light inner outline inside a dark outer
    // ring, so the discernible contrast can come from either ring.
    const ratios = [
      stop.outlineStyle === 'none' ? 0 : contrastRatio(stop.outlineColor, stop.backgroundColor),
      stop.boxShadowColor ? contrastRatio(stop.boxShadowColor, stop.backgroundColor) : 0,
      stop.boxShadowColor && stop.outlineStyle !== 'none'
        ? contrastRatio(stop.outlineColor, stop.boxShadowColor)
        : 0,
    ];
    const ratio = Math.max(...ratios);
    if (ratio < MINIMUM_FOCUS_CONTRAST) {
      findings.add(`focus-contrast: ${stop.description} ${Math.round(ratio * 100) / 100}`);
    }
  }
  return Array.from(findings);
}

for (const state of templateStates) {
  const breakpoints =
    state.kind === 'responsive'
      ? (['desktop', 'mobile'] as const)
      : state.kind === 'desktop-only'
        ? (['desktop'] as const)
        : (['mobile'] as const);

  for (const breakpoint of breakpoints) {
    test(`forward focus traversal and geometry: ${state.id} at ${breakpoint}`, async ({ page }, testInfo) => {
      const label = `${state.id} / ${breakpoint}`;
      await installProbe(page);
      await enterState(page, state, { viewport: viewportFor(state, breakpoint) });

      const run = await traverseFocus(page, state, 'forward');
      assertKeyboardReachability(run, label, 'forward');

      const { unexplained, reproduced } = classifyFindings(
        testInfo,
        'focus',
        state.id,
        focusPresentationFindings(run.stops, { measuresContrast: true }).map((finding) => ({
          conditionId: breakpoint as string,
          finding,
        })),
      );
      expect(unexplained, `${label} focus geometry`).toEqual([]);

      const reachedGroups = new Set(
        [...run.candidates.map((candidate) => candidate.group), ...run.forward.map((stop) => stop.group)].filter(
          (group): group is string => Boolean(group),
        ),
      );
      const expectedGroups = breakpoint === 'desktop' ? state.desktopGroups : state.mobileGroups;
      expect(
        expectedGroups.filter((group) => !reachedGroups.has(group)),
        `${label} reviewed inventory entries with no runtime match`,
      ).toEqual([]);
      expect(
        staleExceptions('focus', state.id, [breakpoint], reproduced),
        `${label} reviewed focus exceptions that no longer reproduce`,
      ).toEqual([]);
    });

    test(`reverse focus traversal and pointer targets: ${state.id} at ${breakpoint}`, async ({ page }, testInfo) => {
      const label = `${state.id} / ${breakpoint}`;
      await installProbe(page);
      await enterState(page, state, { viewport: viewportFor(state, breakpoint) });

      const targets = await measurePointerTargets(page);
      const run = await traverseFocus(page, state, 'reverse');
      assertKeyboardReachability(run, label, 'reverse');

      const { unexplained, reproduced } = classifyFindings(
        testInfo,
        'target',
        state.id,
        targets.undersized.map((target) => ({
          conditionId: breakpoint as string,
          finding: `target-size: ${target.description} ${target.width}x${target.height} nearest ${target.nearest}`,
        })),
      );
      expect(unexplained, `${label} pointer target size and spacing`).toEqual([]);
      expect(targets.targetCount, `${label} pointer targets`).toBeGreaterThan(0);
      expect(
        staleExceptions('target', state.id, [breakpoint], reproduced),
        `${label} reviewed pointer-target exceptions that no longer reproduce`,
      ).toEqual([]);
      testInfo.annotations.push({
        type: 'target-size-exceptions',
        description: `${label}: ${targets.inlineExceptions.length} inline, ${targets.spacingExceptions.length} spacing, ${targets.equivalentControls.length} equivalent controls, of ${targets.targetCount} targets`,
      });
    });
  }
}

const focusConditionStates = new Set(['home', 'documentation-hub', 'article']);
const focusConditions = [
  condition({ id: 'text-resize-200', style: 'html { font-size: 200% !important; }' }),
  condition({ id: 'reflow-320', viewport: { width: 320, height: 800 } }),
  condition({ id: 'theme-dark', colorScheme: 'dark' }),
  condition({ id: 'forced-colors', forcedColors: 'active' }),
];

for (const state of templateStates.filter((entry) => focusConditionStates.has(entry.id))) {
  for (const applied of focusConditions) {
    test(`focus geometry: ${state.id} under ${applied.id}`, async ({ page }, testInfo) => {
      const label = `${state.id} / ${applied.id}`;
      await installProbe(page);
      await enterState(page, state, {
        viewport: applied.viewport ?? DESKTOP_VIEWPORT,
        colorScheme: applied.colorScheme,
        forcedColors: applied.forcedColors,
      });
      if (applied.style) {
        await page.addStyleTag({ content: applied.style });
        await settleAnimations(page);
      }

      const run = await traverseFocus(page, state, 'forward');
      assertKeyboardReachability(run, label, 'forward');
      const { unexplained, reproduced } = classifyFindings(
        testInfo,
        'focus',
        state.id,
        focusPresentationFindings(run.stops, {
          measuresContrast: applied.forcedColors !== 'active',
        }).map((finding) => ({ conditionId: applied.id, finding })),
      );
      expect(unexplained, `${label} focus geometry`).toEqual([]);
      expect(
        staleExceptions('focus', state.id, [applied.id], reproduced),
        `${label} reviewed exceptions that no longer reproduce`,
      ).toEqual([]);
    });
  }
}