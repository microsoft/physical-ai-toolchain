// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

import { expect, test, type Page } from '@playwright/test';

import { analyzeAccessibility, expectPageReady, siteRoute } from './accessibility-helpers';
import { deployedRouteManifestPath, readDeployedRouteManifest } from './route-inventory';
import { evidence } from './evidence-cell-reporter';

type Theme = 'light' | 'dark';
type RenderedState = 'default' | 'not-found';

interface AxeCheckData {
  contrastRatio?: number;
  expectedContrastRatio?: string;
  messageKey?: string;
}

interface AxeCheckResult {
  data?: AxeCheckData | null;
}

interface AxeNode {
  all?: AxeCheckResult[];
  any?: AxeCheckResult[];
  failureSummary?: string;
  html: string;
  none?: AxeCheckResult[];
  target: unknown[];
}

interface AxeResult {
  id: string;
  nodes: AxeNode[];
}

interface FeatureInventory {
  activationTriggers: Record<string, number>;
  controls: Record<string, number>;
  graphics: Record<string, number>;
  headingLevels: number[];
  landmarks: Record<string, number>;
  links: Record<string, number>;
}

interface ClientNavigationEvidence {
  documentPreserved: boolean;
  lang: string | null;
  performed: boolean;
  title: string | null;
  via: string | null;
}

interface RouteEvidence {
  clientNavigation: ClientNavigationEvidence | null;
  contrastEvidence: ContrastMeasurement[];
  errors: string[];
  features: FeatureInventory | null;
  incomplete: AxeResult[];
  lang: string | null;
  route: string;
  state: RenderedState;
  status: number | null;
  theme: Theme;
  title: string | null;
  violations: AxeResult[];
}

type ContrastMethodStatus =
  | 'computed-ratio-pass-candidate'
  | 'computed-ratio-failure-candidate'
  | 'qualified-review-required'
  | 'insufficient-evidence';

interface ContrastMeasurement {
  background: string | null;
  backgroundImage: string | null;
  evidencePath: string | null;
  foreground: string | null;
  methodStatus: ContrastMethodStatus;
  obscured: boolean | null;
  opacity: number | null;
  ratio: number | null;
  reason: string;
  requiredRatio: number | null;
  route: string;
  signature: string;
  state: RenderedState;
  target: string;
  theme: Theme;
  tupleDigest: string;
}

interface SignatureParts {
  html: string;
  reason: string;
  rule: string;
  state: RenderedState;
  target: string;
  theme: Theme;
}

interface LedgerEntry extends SignatureParts {
  count: number;
  measuredFailures: string[];
  routeFamilies: string[];
  routes: string[];
  signature: string;
}

interface BaselineEntry extends SignatureParts {
  classification: 'reviewed' | 'unresolved';
  count: number;
  evidence: string | null;
  freshnessTriggers: string[];
  owner: string;
  rationale: string;
  reviewBy: string | null;
  reviewedOn: string;
  routeFamilies: string[];
  signature: string;
}

interface BaselineDocument {
  entries: BaselineEntry[];
  policy: string;
  schemaVersion: 1;
  source: string;
}

type LedgerStatus =
  | 'accepted'
  | 'decreased'
  | 'increased'
  | 'measured-failure'
  | 'stale'
  | 'unknown'
  | 'unowned'
  | 'unreconciled'
  | 'unresolved';

interface LedgerAssessment {
  detail: string;
  signature: string;
  status: LedgerStatus;
}

interface CrawlArtifacts {
  blocking: string[];
  contrastBlocking: string[];
  ledger: {
    assessments: LedgerAssessment[];
    baseline: string;
    entries: LedgerEntry[];
    schemaVersion: 1;
    totals: Record<string, number>;
  };
  results: {
    manifest: string;
    results: RouteEvidence[];
    schemaVersion: 2;
  };
  routeBlocking: string[];
  summary: string;
}

const REDACTED = '[redacted]';
const FRAGMENT_LIMIT = 200;
const NOT_FOUND_ROUTE = '/does-not-exist/';
const THEMES: Theme[] = ['light', 'dark'];

const manifest = readDeployedRouteManifest();
const outputDirectory = path.resolve(process.cwd(), process.env.DOCS_E2E_OUTPUT_DIR ?? 'test-results');
const resultsPath = path.join(outputDirectory, 'site-crawl-results.json');
const ledgerPath = path.join(outputDirectory, 'contrast-ledger.json');
const summaryPath = path.join(outputDirectory, 'site-crawl-summary.txt');
const contrastEvidenceDirectory = path.join(outputDirectory, 'contrast-evidence');
const baselinePath = path.resolve(process.cwd(), 'e2e', 'contrast-baseline.json');
const relativeBaselinePath = path.relative(process.cwd(), baselinePath).replaceAll('\\', '/');
const baselineWriteRequested = process.env.DOCS_CONTRAST_BASELINE_WRITE === '1' && !process.env.CI;

const evidenceByKey = new Map<string, RouteEvidence>();

function evidenceKey(route: string, theme: Theme, state: RenderedState): string {
  return `${route}|${theme}|${state}`;
}

function expectedEvidenceKeys(): string[] {
  return [
    ...manifest.routes.flatMap((route) => THEMES.map((theme) => evidenceKey(route, theme, 'default'))),
    evidenceKey(NOT_FOUND_ROUTE, 'light', 'not-found'),
  ].sort();
}

function routeFamily(route: string): string {
  const [, segment] = route.split('/');
  return segment ? `/${segment}` : '/';
}

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

// Query strings and typed values can carry user content, so they never reach retained evidence.
function redactSensitive(value: string): string {
  return value
    .replace(/(\svalue=")[^"]+(")/g, `$1${REDACTED}$2`)
    .replace(/([?&](?:q|query|search)=)[^"'&\s\]]*/gi, `$1${REDACTED}`);
}

// Docusaurus emits per-build CSS module hashes, React identifiers, and Mermaid SVG identifiers.
function normalizeIdentifiers(value: string): string {
  return value
    .replace(/_R_[A-Za-z0-9]+_/g, '_R_')
    .replace(/mermaid-svg-\d+/g, 'mermaid-svg')
    .replace(/([A-Za-z][A-Za-z0-9]*)_[A-Za-z0-9+/-]{4}(?![A-Za-z0-9])/g, (_match, name: string) => name)
    .replace(/\s+/g, ' ')
    .trim();
}

export function normalizeFragment(value: string, limit = FRAGMENT_LIMIT): string {
  const normalized = normalizeIdentifiers(redactSensitive(value));
  return normalized.length > limit ? `${normalized.slice(0, limit)} [truncated]` : normalized;
}

function normalizeTarget(target: unknown[]): string {
  return normalizeFragment(JSON.stringify(target.map((selector) => String(selector))));
}

function checkResults(node: AxeNode): AxeCheckResult[] {
  return [...(node.any ?? []), ...(node.all ?? []), ...(node.none ?? [])];
}

function incompleteReason(node: AxeNode): string {
  const messageKey = checkResults(node).find((check) => check.data?.messageKey)?.data?.messageKey;
  return messageKey ?? normalizeFragment(node.failureSummary ?? 'unspecified', 120);
}

// Axe reports a computed ratio only when it resolved both layers; that measurement decides the criterion.
function measuredContrastFailure(node: AxeNode): string | null {
  for (const check of checkResults(node)) {
    const ratio = check.data?.contrastRatio;
    const required = Number.parseFloat(String(check.data?.expectedContrastRatio ?? ''));
    if (typeof ratio === 'number' && ratio > 0 && Number.isFinite(required) && ratio < required) {
      return `measured ${ratio}:1 below the required ${required}:1`;
    }
  }
  return null;
}

function parsedColor(color: string | null): { alpha: number; channels: number[] } | null {
  const values = color?.match(/[\d.]+/g)?.map(Number);
  if (!values || values.length < 3) {
    return null;
  }
  return { alpha: values[3] ?? 1, channels: values.slice(0, 3) };
}

function renderedContrastRatio(foreground: string | null, background: string | null): number | null {
  const foregroundColor = parsedColor(foreground);
  const backgroundColor = parsedColor(background);
  if (!foregroundColor || !backgroundColor || foregroundColor.alpha !== 1 || backgroundColor.alpha !== 1) {
    return null;
  }
  const luminance = (channels: number[]): number => {
    const [red, green, blue] = channels.map((channel) => {
      const normalized = channel / 255;
      return normalized <= 0.04045
        ? normalized / 12.92
        : ((normalized + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
  };
  const values = [luminance(foregroundColor.channels), luminance(backgroundColor.channels)].sort(
    (left, right) => right - left,
  );
  return Number(((values[0] + 0.05) / (values[1] + 0.05)).toFixed(2));
}

export function requiredContrastRatio(fontSizePx: number, fontWeight: number): number {
  return fontSizePx >= 24 || (fontSizePx >= 18.66 && fontWeight >= 700) ? 3 : 4.5;
}

async function captureContrastMeasurement(
  page: Page,
  result: AxeResult,
  node: AxeNode,
  route: string,
  theme: Theme,
  state: RenderedState,
): Promise<ContrastMeasurement> {
  const parts: SignatureParts = {
    html: normalizeFragment(node.html),
    reason: incompleteReason(node),
    rule: result.id,
    state,
    target: normalizeTarget(node.target),
    theme,
  };
  const signature = contrastSignature(parts);
  const axeRequiredRatio =
    checkResults(node)
      .map((check) => Number.parseFloat(String(check.data?.expectedContrastRatio ?? '')))
      .find(Number.isFinite) ?? null;
  const selector = node.target.find((candidate) => typeof candidate === 'string');
  let background: string | null = null;
  let backgroundImage: string | null = null;
  let foreground: string | null = null;
  let fontSizePx: number | null = null;
  let fontWeight: number | null = null;
  let obscured: boolean | null = null;
  let opacity: number | null = null;
  let evidencePath: string | null = null;
  let targetFound = false;

  if (typeof selector === 'string' && (await page.locator(selector).count()) > 0) {
    targetFound = true;
    const locator = page.locator(selector).first();
    const rendered = await locator.evaluate((element) => {
      const elementStyle = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      const centerX = Math.min(window.innerWidth - 1, Math.max(0, rect.left + rect.width / 2));
      const centerY = Math.min(window.innerHeight - 1, Math.max(0, rect.top + rect.height / 2));
      const top = rect.width > 0 && rect.height > 0 ? document.elementFromPoint(centerX, centerY) : null;
      const parseColor = (value: string) => {
        const values = value.match(/[\d.]+/g)?.map(Number);
        return values && values.length >= 3
          ? { channels: values.slice(0, 3), alpha: values[3] ?? 1 }
          : null;
      };
      const layers: Array<{ channels: number[]; alpha: number }> = [];
      let image: string | null = null;
      let current: Element | null = element;
      while (current) {
        const style = getComputedStyle(current);
        const color = parseColor(style.backgroundColor);
        if (!image && style.backgroundImage !== 'none') {
          image = style.backgroundImage;
        }
        if (color && color.alpha > 0) {
          layers.push(color);
        }
        current = current.parentElement;
      }
      let composite = { channels: [255, 255, 255], alpha: 1 };
      for (const layer of layers.reverse()) {
        composite = {
          channels: layer.channels.map(
            (channel, index) => channel * layer.alpha + composite.channels[index] * (1 - layer.alpha),
          ),
          alpha: layer.alpha + composite.alpha * (1 - layer.alpha),
        };
      }
      const effectiveBackground = `rgb(${composite.channels.map((channel) => Math.round(channel)).join(', ')})`;
      const computedWeight = Number.parseInt(elementStyle.fontWeight, 10);
      return {
        background: effectiveBackground,
        backgroundImage: image,
        fontSizePx: Number.parseFloat(elementStyle.fontSize),
        fontWeight: Number.isFinite(computedWeight) ? computedWeight : elementStyle.fontWeight === 'bold' ? 700 : 400,
        foreground: elementStyle.color,
        obscured: top === null ? null : !(top === element || element.contains(top) || top.contains(element)),
        opacity: Number.parseFloat(elementStyle.opacity),
      };
    });
    ({ background, backgroundImage, fontSizePx, fontWeight, foreground, obscured, opacity } = rendered);
  }

  const requiredRatio =
    axeRequiredRatio ??
    (fontSizePx !== null && fontWeight !== null ? requiredContrastRatio(fontSizePx, fontWeight) : null);
  const ratio = renderedContrastRatio(foreground, background);
  const isOpaque =
    parsedColor(foreground)?.alpha === 1 && parsedColor(background)?.alpha === 1 && opacity === 1;
  const isAmbiguousReason =
    parts.reason === 'bgImage' || parts.reason === 'imgNode' || parts.reason === 'elmPartiallyObscuring';
  const methodStatus: ContrastMethodStatus =
    !foreground || !background || requiredRatio === null || obscured === null
      ? 'insufficient-evidence'
      : isAmbiguousReason || backgroundImage || obscured || !isOpaque || ratio === null
        ? 'qualified-review-required'
        : ratio < requiredRatio
          ? 'computed-ratio-failure-candidate'
          : 'computed-ratio-pass-candidate';
  const tuple = { background, backgroundImage, foreground, methodStatus, obscured, opacity, ratio, requiredRatio };
  const tupleDigest = crypto.createHash('sha256').update(JSON.stringify(tuple)).digest('hex');

  if (typeof selector === 'string' && targetFound) {
    const relativePath = path.join('contrast-evidence', `${signature}-${tupleDigest.slice(0, 12)}.png`);
    const absolutePath = path.join(outputDirectory, relativePath);
    if (!fs.existsSync(absolutePath)) {
      fs.mkdirSync(contrastEvidenceDirectory, { recursive: true });
      try {
        await page.locator(selector).first().screenshot({ path: absolutePath });
      } catch {
        const locator = page.locator(selector).first();
        await locator.scrollIntoViewIfNeeded().catch(() => undefined);
        const box = await locator.boundingBox().catch(() => null);
        const viewport = page.viewportSize() ?? { height: 720, width: 1280 };
        const scroll = await page.evaluate(() => ({ x: window.scrollX, y: window.scrollY }));
        const clip = box && box.width > 0 && box.height > 0
          ? {
              height: Math.min(box.height, viewport.height),
              width: Math.min(box.width, viewport.width),
              x: Math.max(0, box.x + scroll.x),
              y: Math.max(0, box.y + scroll.y),
            }
          : { height: viewport.height, width: viewport.width, x: scroll.x, y: scroll.y };
        await page.screenshot({ path: absolutePath, clip });
      }
    }
    evidencePath = relativePath.replaceAll('\\', '/');
  }
  return {
    ...tuple,
    evidencePath,
    reason: parts.reason,
    route,
    signature,
    state,
    target: parts.target,
    theme,
    tupleDigest,
  };
}

export function contrastSignature(parts: SignatureParts): string {
  return crypto
    .createHash('sha256')
    .update([parts.rule, parts.reason, parts.target, parts.html, parts.theme, parts.state].join('\u0000'))
    .digest('hex')
    .slice(0, 16);
}

export function buildContrastLedger(evidence: RouteEvidence[]): LedgerEntry[] {
  const entries = new Map<string, LedgerEntry>();
  for (const record of [...evidence].sort((left, right) => left.route.localeCompare(right.route))) {
    for (const result of record.incomplete) {
      for (const node of result.nodes) {
        const parts: SignatureParts = {
          html: normalizeFragment(node.html),
          reason: incompleteReason(node),
          rule: result.id,
          state: record.state,
          target: normalizeTarget(node.target),
          theme: record.theme,
        };
        const signature = contrastSignature(parts);
        const entry = entries.get(signature) ?? {
          ...parts,
          count: 0,
          measuredFailures: [],
          routeFamilies: [],
          routes: [],
          signature,
        };
        entry.count += 1;
        if (!entry.routes.includes(record.route)) {
          entry.routes.push(record.route);
        }
        if (!entry.routeFamilies.includes(routeFamily(record.route))) {
          entry.routeFamilies.push(routeFamily(record.route));
        }
        const measured = measuredContrastFailure(node);
        if (measured && !entry.measuredFailures.includes(`${record.route}: ${measured}`)) {
          entry.measuredFailures.push(`${record.route}: ${measured}`);
        }
        entries.set(signature, entry);
      }
    }
  }
  return [...entries.values()]
    .map((entry) => ({ ...entry, routeFamilies: [...entry.routeFamilies].sort(), routes: [...entry.routes].sort() }))
    .sort((left, right) => right.count - left.count || left.signature.localeCompare(right.signature));
}

export function validateBaselineDocument(raw: unknown): BaselineEntry[] {
  const document = raw as Partial<BaselineDocument>;
  if (document?.schemaVersion !== 1 || !Array.isArray(document.entries)) {
    throw new Error(`Contrast baseline has an unsupported schema: ${relativeBaselinePath}`);
  }
  const signatures = new Set<string>();
  for (const entry of document.entries) {
    const structured =
      typeof entry?.signature === 'string' &&
      typeof entry.rule === 'string' &&
      typeof entry.reason === 'string' &&
      typeof entry.target === 'string' &&
      typeof entry.html === 'string' &&
      (entry.theme === 'light' || entry.theme === 'dark') &&
      (entry.state === 'default' || entry.state === 'not-found') &&
      Number.isInteger(entry.count) &&
      entry.count > 0 &&
      Array.isArray(entry.routeFamilies) &&
      entry.routeFamilies.every((family) => typeof family === 'string') &&
      Array.isArray(entry.freshnessTriggers) &&
      entry.freshnessTriggers.length > 0 &&
      typeof entry.owner === 'string' &&
      entry.owner.length > 0 &&
      typeof entry.rationale === 'string' &&
      entry.rationale.length > 0 &&
      /^\d{4}-\d{2}-\d{2}$/.test(String(entry.reviewedOn)) &&
      (entry.classification === 'reviewed' || entry.classification === 'unresolved');
    if (!structured) {
      throw new Error(`Contrast baseline entry is incomplete: ${String(entry?.signature)}`);
    }
    if (
      entry.classification === 'reviewed' &&
      !(
        entry.owner !== 'unassigned' &&
        typeof entry.evidence === 'string' &&
        entry.evidence.length > 0 &&
        /^\d{4}-\d{2}-\d{2}$/.test(String(entry.reviewBy))
      )
    ) {
      throw new Error(`Reviewed baseline entry needs an owner, deciding evidence, and a review date: ${entry.signature}`);
    }
    if (signatures.has(entry.signature)) {
      throw new Error(`Contrast baseline has a duplicate signature: ${entry.signature}`);
    }
    signatures.add(entry.signature);
    if (contrastSignature(entry) !== entry.signature) {
      throw new Error(`Contrast baseline signature does not match its normalized parts: ${entry.signature}`);
    }
  }
  return document.entries;
}

function readBaseline(): BaselineEntry[] {
  if (!fs.existsSync(baselinePath)) {
    throw new Error(`Contrast baseline is missing: ${relativeBaselinePath}`);
  }
  return validateBaselineDocument(JSON.parse(fs.readFileSync(baselinePath, 'utf8')));
}

export function assessContrastLedger(
  ledger: LedgerEntry[],
  baseline: BaselineEntry[],
  options: { reconcileUnobserved: boolean; today: string },
): LedgerAssessment[] {
  const bySignature = new Map(baseline.map((entry) => [entry.signature, entry]));
  const assessments: LedgerAssessment[] = ledger.map((entry) => {
    const reference = bySignature.get(entry.signature);
    if (entry.measuredFailures.length > 0) {
      return {
        detail: `${entry.rule} ${entry.target} ${entry.measuredFailures.join('; ')}`,
        signature: entry.signature,
        status: 'measured-failure',
      };
    }
    if (!reference) {
      return {
        detail: `unknown ${entry.rule} signature on ${entry.routes.length} ${entry.theme} route(s): ${entry.target}`,
        signature: entry.signature,
        status: 'unknown',
      };
    }
    if (entry.count > reference.count) {
      return {
        detail: `${entry.rule} count increased from ${reference.count} to ${entry.count}: ${entry.target}`,
        signature: entry.signature,
        status: 'increased',
      };
    }
    const unreviewedFamilies = entry.routeFamilies.filter((family) => !reference.routeFamilies.includes(family));
    if (unreviewedFamilies.length > 0) {
      return {
        detail: `${entry.rule} appeared in unreviewed route families ${unreviewedFamilies.join(', ')}: ${entry.target}`,
        signature: entry.signature,
        status: 'unreconciled',
      };
    }
    if (reference.classification === 'unresolved') {
      return {
        detail: `${entry.rule} is unresolved and needs deciding evidence (${entry.reason}): ${entry.target}`,
        signature: entry.signature,
        status: 'unresolved',
      };
    }
    if (reference.owner === 'unassigned' || !reference.evidence) {
      return {
        detail: `${entry.rule} classification has no owner or deciding evidence: ${entry.target}`,
        signature: entry.signature,
        status: 'unowned',
      };
    }
    if (!reference.reviewBy || reference.reviewBy < options.today) {
      return {
        detail: `${entry.rule} review expired on ${String(reference.reviewBy)}: ${entry.target}`,
        signature: entry.signature,
        status: 'stale',
      };
    }
    if (options.reconcileUnobserved && entry.count < reference.count) {
      return {
        detail: `${entry.rule} count decreased from ${reference.count} to ${entry.count}; ratchet the baseline: ${entry.target}`,
        signature: entry.signature,
        status: 'decreased',
      };
    }
    return { detail: `${entry.rule}: ${entry.target}`, signature: entry.signature, status: 'accepted' };
  });

  if (options.reconcileUnobserved) {
    const observed = new Set(ledger.map((entry) => entry.signature));
    for (const entry of baseline) {
      if (!observed.has(entry.signature)) {
        assessments.push({
          detail: `baseline entry is no longer produced by the current build; remove it: ${entry.target}`,
          signature: entry.signature,
          status: 'unreconciled',
        });
      }
    }
  }
  return assessments;
}

export function checkRouteInvariants(evidence: RouteEvidence): string[] {
  const label = `${evidence.route} [${evidence.theme}/${evidence.state}]`;
  const failures: string[] = [];
  const expectedStatus = evidence.state === 'not-found' ? 404 : 200;
  if (evidence.status !== expectedStatus) {
    failures.push(`${label}: expected HTTP ${expectedStatus} and received ${String(evidence.status)}`);
  }
  if (!evidence.title || evidence.title.trim().length === 0) {
    failures.push(`${label}: document title is empty`);
  }
  if (evidence.lang !== 'en') {
    failures.push(`${label}: expected lang="en" and received ${String(evidence.lang)}`);
  }
  if (evidence.violations.length > 0) {
    const rules = evidence.violations.map((violation) => violation.id).join(', ');
    failures.push(`${label}: ${evidence.violations.length} axe violation(s): ${rules}`);
  }

  const features = evidence.features;
  if (!features) {
    failures.push(`${label}: rendered feature inventory is missing`);
    return failures;
  }
  for (const landmark of ['banner', 'contentinfo', 'main'] as const) {
    if (features.landmarks[landmark] !== 1) {
      failures.push(`${label}: expected one ${landmark} landmark and found ${features.landmarks[landmark]}`);
    }
  }
  if (features.landmarks.navigation < 1) {
    failures.push(`${label}: no navigation landmark is exposed`);
  }
  const topLevelHeadings = features.headingLevels.filter((level) => level === 1).length;
  if (topLevelHeadings !== 1) {
    failures.push(`${label}: expected one level-one heading and found ${topLevelHeadings}`);
  }
  features.headingLevels.slice(1).forEach((level, index) => {
    if (level > features.headingLevels[index] + 1) {
      failures.push(`${label}: heading level ${level} skips ${features.headingLevels[index] + 1}`);
    }
  });
  if (features.graphics.imagesWithoutAlt > 0) {
    failures.push(`${label}: ${features.graphics.imagesWithoutAlt} image(s) have no alt attribute`);
  }
  if (features.graphics.svgWithoutAccessibleName > 0) {
    failures.push(`${label}: ${features.graphics.svgWithoutAccessibleName} exposed graphic(s) have no accessible name`);
  }
  for (const [trigger, count] of Object.entries(features.activationTriggers)) {
    if (count > 0) {
      failures.push(`${label}: guarded trigger ${trigger} activated ${count} time(s) and needs a reviewed disposition`);
    }
  }

  const navigation = evidence.clientNavigation;
  if (evidence.state !== 'default') {
    return failures;
  }
  if (!navigation?.performed) {
    failures.push(`${label}: client-side navigation was not exercised`);
  } else if (!navigation.documentPreserved) {
    failures.push(`${label}: returning through ${String(navigation.via)} reloaded the document instead of routing`);
  } else {
    if (navigation.title !== evidence.title) {
      const restored = String(navigation.title);
      failures.push(`${label}: client-side navigation restored title ${restored} instead of ${String(evidence.title)}`);
    }
    if (navigation.lang !== 'en') {
      failures.push(`${label}: client-side navigation restored lang ${String(navigation.lang)}`);
    }
  }
  return failures;
}

function buildSummary(artifacts: Omit<CrawlArtifacts, 'summary'>, expectedKeys: string[]): string {
  const { blocking, ledger, results } = artifacts;
  const routeFindings = results.results.filter((record) => record.errors.length > 0);
  const nodes = ledger.entries.reduce((total, entry) => total + entry.count, 0);
  const dispositions = Object.entries(ledger.totals)
    .filter(([, count]) => count > 0)
    .map(([status, count]) => `${status}=${count}`)
    .join(' ');
  const lines = [
    'Docusaurus route and contrast evidence summary',
    '=============================================',
    `Route states evaluated: ${results.results.length} of ${expectedKeys.length} expected`,
    `Route states with findings: ${routeFindings.length}`,
    `Axe violations: ${results.results.reduce((total, record) => total + record.violations.length, 0)}`,
    `Unresolved axe nodes: ${nodes} across ${ledger.entries.length} signature(s)`,
    `Baseline: ${ledger.baseline}`,
    `Ledger dispositions: ${dispositions || 'none'}`,
    `Blocking findings: ${blocking.length}`,
    '',
  ];
  if (routeFindings.length > 0) {
    lines.push('Route findings', '--------------');
    for (const record of routeFindings) {
      lines.push(...record.errors.map((error) => `  ${error}`));
    }
    lines.push('');
  }
  const unaccepted = ledger.assessments.filter((assessment) => assessment.status !== 'accepted');
  if (unaccepted.length > 0) {
    lines.push('Contrast ledger findings', '------------------------');
    lines.push(...unaccepted.map((item) => `  [${item.status}] ${item.signature} ${item.detail}`));
    lines.push('');
  }
  return `${lines.join('\n')}\n`;
}

export function buildCrawlArtifacts(
  evidence: RouteEvidence[],
  baseline: BaselineEntry[],
  options: { expectedKeys: string[]; today: string },
): CrawlArtifacts {
  const results = [...evidence].sort((left, right) =>
    evidenceKey(left.route, left.theme, left.state).localeCompare(evidenceKey(right.route, right.theme, right.state)),
  );
  const routeErrors = results.flatMap((record) => record.errors);
  const observedKeys = results.map((record) => evidenceKey(record.route, record.theme, record.state)).sort();
  const missingKeys = options.expectedKeys.filter((key) => !observedKeys.includes(key));
  const unexpectedKeys = observedKeys.filter((key) => !options.expectedKeys.includes(key));
  const duplicateKeys = observedKeys.filter((key, index) => observedKeys.indexOf(key) !== index);

  const entries = buildContrastLedger(results);
  const assessments = assessContrastLedger(entries, baseline, {
    reconcileUnobserved: routeErrors.length === 0 && missingKeys.length === 0,
    today: options.today,
  });
  const totals = assessments.reduce<Record<string, number>>((accumulator, assessment) => {
    accumulator[assessment.status] = (accumulator[assessment.status] ?? 0) + 1;
    return accumulator;
  }, {});

  const titles = new Map<string, string[]>();
  for (const record of results.filter((candidate) => candidate.theme === 'light' && candidate.state === 'default')) {
    titles.set(record.title ?? '', [...(titles.get(record.title ?? '') ?? []), record.route]);
  }

  const routeBlocking = [
    ...missingKeys.map((key) => `route state never produced evidence: ${key}`),
    ...unexpectedKeys.map((key) => `route state is not declared by the manifest: ${key}`),
    ...duplicateKeys.map((key) => `route state produced duplicate evidence: ${key}`),
    ...routeErrors,
    ...[...titles.entries()]
      .filter(([, routes]) => routes.length > 1)
      .map(([title, routes]) => `document title "${title}" is not descriptive; shared by ${routes.join(', ')}`),
  ];
  const contrastBlocking = assessments
    .filter((assessment) => assessment.status !== 'accepted')
    .map((assessment) => `[${assessment.status}] ${assessment.signature} ${assessment.detail}`);
  const blocking = [...routeBlocking, ...contrastBlocking];

  const artifacts: Omit<CrawlArtifacts, 'summary'> = {
    blocking,
    contrastBlocking,
    ledger: { assessments, baseline: relativeBaselinePath, entries, schemaVersion: 1, totals },
    results: {
      manifest: path.relative(process.cwd(), deployedRouteManifestPath).replaceAll('\\', '/'),
      results,
      schemaVersion: 2,
    },
    routeBlocking,
  };
  return { ...artifacts, summary: buildSummary(artifacts, options.expectedKeys) };
}

function writeJson(filePath: string, value: unknown): void {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`, 'utf8');
}

// Seeding can only record unresolved classifications, so it can never manufacture an allowance.
function writeBaselineSeed(entries: LedgerEntry[]): void {
  const document: BaselineDocument = {
    entries: entries.map((entry) => ({
      classification: 'unresolved',
      count: entry.count,
      evidence: null,
      freshnessTriggers: [
        'Docusaurus, theme, or plugin upgrade',
        'authored content change in a listed route family',
        'shared component, design token, or stylesheet change',
      ],
      html: entry.html,
      owner: 'unassigned',
      rationale: 'Seeded from crawl output; axe could not decide this result and no qualified review exists yet.',
      reason: entry.reason,
      reviewBy: null,
      reviewedOn: today(),
      routeFamilies: entry.routeFamilies,
      rule: entry.rule,
      signature: entry.signature,
      state: entry.state,
      target: entry.target,
      theme: entry.theme,
    })),
    policy:
      'Every entry is unresolved and blocking until a qualified reviewer records an owner, rationale, deciding evidence, and a review date.',
    schemaVersion: 1,
    source: 'docs/docusaurus/e2e/site-crawl.spec.ts',
  };
  writeJson(baselinePath, document);
}

function writeArtifacts(baseline: BaselineEntry[]): CrawlArtifacts {
  const artifacts = buildCrawlArtifacts([...evidenceByKey.values()], baseline, {
    expectedKeys: expectedEvidenceKeys(),
    today: today(),
  });
  writeJson(resultsPath, artifacts.results);
  writeJson(ledgerPath, artifacts.ledger);
  fs.mkdirSync(path.dirname(summaryPath), { recursive: true });
  fs.writeFileSync(summaryPath, artifacts.summary, 'utf8');
  return artifacts;
}

async function collectFeatureInventory(page: Page): Promise<FeatureInventory> {
  const landmarks = {
    banner: await page.getByRole('banner').count(),
    complementary: await page.getByRole('complementary').count(),
    contentinfo: await page.getByRole('contentinfo').count(),
    main: await page.getByRole('main').count(),
    navigation: await page.getByRole('navigation').count(),
  };
  const rendered = await page.evaluate(() => {
    const personalDataTokens = new Set([
      'additional-name', 'address-level1', 'address-level2', 'address-level3', 'address-level4', 'address-line1',
      'address-line2', 'address-line3', 'bday', 'bday-day', 'bday-month', 'bday-year', 'cc-additional-name',
      'cc-csc', 'cc-exp', 'cc-exp-month', 'cc-exp-year', 'cc-family-name', 'cc-given-name', 'cc-name', 'cc-number',
      'cc-type', 'country', 'country-name', 'current-password', 'email', 'family-name', 'given-name',
      'honorific-prefix', 'honorific-suffix', 'impp', 'language', 'name', 'new-password', 'nickname', 'one-time-code',
      'organization', 'organization-title', 'photo', 'postal-code', 'sex', 'street-address', 'tel', 'tel-area-code',
      'tel-country-code', 'tel-extension', 'tel-local', 'tel-national', 'transaction-amount', 'transaction-currency',
      'url', 'username',
    ]);
    const count = (selector: string): number => document.querySelectorAll(selector).length;
    const anchors = [...document.querySelectorAll('a[href]')] as HTMLAnchorElement[];
    const images = [...document.querySelectorAll('img')];
    const vectors = [...document.querySelectorAll('svg')];
    const documentLanguage = document.documentElement.lang;
    const motionListeners = (window as unknown as { __docsMotionListeners?: string[] }).__docsMotionListeners ?? [];

    return {
      activationTriggers: {
        authentication: count(
          'input[type="password"], [autocomplete~="current-password"], [autocomplete~="new-password"], [autocomplete~="one-time-code"]',
        ),
        characterShortcuts: count('[accesskey]'),
        consequentialMutation: count('form[method="post" i], button[type="submit"], input[type="submit"]'),
        dragging: count('[draggable="true"]'),
        languageChanges: [...document.querySelectorAll('[lang]')].filter(
          (element) => element !== document.documentElement && element.getAttribute('lang') !== documentLanguage,
        ).length,
        media: count('audio, video, track, embed, object, iframe, frame, marquee, bgsound'),
        motionApis: motionListeners.length,
        personalDataFields: [...document.querySelectorAll('[autocomplete]')].filter((field) =>
          (field.getAttribute('autocomplete') ?? '')
            .split(/\s+/)
            .some((token) => personalDataTokens.has(token.toLowerCase())),
        ).length,
        pointerGestures: count('[ontouchstart], [ontouchmove], [ongesturestart]'),
        timedUpdates: count('meta[http-equiv="refresh" i]'),
        unexpectedAutoplay: count('[autoplay]'),
        validation: count('form, [required], [pattern], [aria-invalid], [aria-errormessage]'),
        zoomableImages: count('img[data-zoomable], .medium-zoom-image, [data-zoom-src]'),
      },
      controls: {
        buttons: count('button, [role="button"]'),
        details: count('details'),
        inputs: count('input'),
        selects: count('select'),
        textareas: count('textarea'),
      },
      graphics: {
        images: images.length,
        imagesWithoutAlt: images.filter((image) => !image.hasAttribute('alt')).length,
        svg: vectors.length,
        svgWithoutAccessibleName: vectors.filter(
          (vector) =>
            vector.getAttribute('aria-hidden') !== 'true' &&
            vector.getAttribute('role') === 'img' &&
            !vector.getAttribute('aria-label') &&
            !vector.getAttribute('aria-labelledby') &&
            !vector.querySelector('title'),
        ).length,
      },
      headingLevels: [
        ...document.querySelectorAll(
          'main h1, main h2, main h3, main h4, main h5, main h6, [role="main"] h1, [role="main"] h2, [role="main"] h3, [role="main"] h4, [role="main"] h5, [role="main"] h6',
        ),
      ].map((heading) => Number(heading.tagName.slice(1))),
      links: {
        external: anchors.filter((anchor) => anchor.origin !== location.origin).length,
        hash: anchors.filter((anchor) => (anchor.getAttribute('href') ?? '').startsWith('#')).length,
        internal: anchors.filter(
          (anchor) => anchor.origin === location.origin && !(anchor.getAttribute('href') ?? '').startsWith('#'),
        ).length,
        total: anchors.length,
      },
    };
  });
  return { ...rendered, landmarks };
}

// Returning through session history proves the router rendered the route without a new document load.
async function probeClientNavigation(page: Page): Promise<ClientNavigationEvidence> {
  const origin = page.url();
  const candidate = await page.evaluate(() => {
    const anchors = [...document.querySelectorAll('header a[href], nav a[href], footer a[href]')] as HTMLAnchorElement[];
    const match = anchors.find(
      (anchor) =>
        anchor.origin === location.origin &&
        anchor.pathname !== location.pathname &&
        !(anchor.getAttribute('href') ?? '').startsWith('#') &&
        anchor.checkVisibility(),
    );
    return match ? { href: match.href, selector: match.getAttribute('href') ?? '' } : null;
  });
  if (!candidate) {
    return { documentPreserved: false, lang: null, performed: false, title: null, via: null };
  }

  await page.evaluate(() => {
    (window as unknown as { __docsDirectLoad?: boolean }).__docsDirectLoad = true;
  });
  await page.locator(`a[href="${candidate.selector}"]`).first().click();
  await page.waitForURL(candidate.href);
  await page.goBack();
  await page.waitForURL(origin);
  await page.locator('main, [role="main"]').first().waitFor({ state: 'visible' });

  const restored = await page.evaluate(() => ({
    documentPreserved: (window as unknown as { __docsDirectLoad?: boolean }).__docsDirectLoad === true,
    lang: document.documentElement.lang,
    title: document.title,
  }));
  return { ...restored, performed: true, via: candidate.selector };
}

async function crawl(page: Page, route: string, theme: Theme, state: RenderedState): Promise<void> {
  const evidence: RouteEvidence = {
    clientNavigation: null,
    contrastEvidence: [],
    errors: [],
    features: null,
    incomplete: [],
    lang: null,
    route,
    state,
    status: null,
    theme,
    title: null,
    violations: [],
  };
  try {
    await page.addInitScript(() => {
      const registered: string[] = [];
      (window as unknown as { __docsMotionListeners: string[] }).__docsMotionListeners = registered;
      const original = window.addEventListener.bind(window);
      window.addEventListener = function trackMotionListeners(type: string, ...rest: unknown[]) {
        if (type === 'devicemotion' || type === 'deviceorientation') {
          registered.push(type);
        }
        return (original as unknown as (...args: unknown[]) => void)(type, ...rest);
      } as typeof window.addEventListener;
    });
    await page.emulateMedia({ colorScheme: theme });
    const response = await page.goto(siteRoute(route));
    evidence.status = response?.status() ?? null;
    await expectPageReady(page);
    evidence.title = await page.title();
    evidence.lang = await page.locator('html').getAttribute('lang');
    evidence.features = await collectFeatureInventory(page);
    const accessibility = (await analyzeAccessibility(page)) as unknown as {
      incomplete: AxeResult[];
      violations: AxeResult[];
    };
    evidence.violations = accessibility.violations;
    evidence.incomplete = accessibility.incomplete;
    for (const result of evidence.incomplete.filter((candidate) => candidate.id === 'color-contrast')) {
      for (const node of result.nodes) {
        evidence.contrastEvidence.push(await captureContrastMeasurement(page, result, node, route, theme, state));
      }
    }
    if (state === 'default') {
      evidence.clientNavigation = await probeClientNavigation(page);
    }
  } catch (error) {
    evidence.errors.push(`${route} [${theme}/${state}]: ${error instanceof Error ? error.message : String(error)}`);
  }
  evidence.errors.push(...checkRouteInvariants(evidence));
  evidenceByKey.set(evidenceKey(route, theme, state), evidence);
}

test.describe.serial('all-route default-state evidence', () => {
  test.afterAll(() => {
    writeArtifacts(readBaseline());
  });

  test('collects every route state before evaluating the gate', evidence('dcs01-dcs12-route-crawl', [
    { journeyId: 'DCS01', method: 'PLAYWRIGHT_TREE' },
    { journeyId: 'DCS12', method: 'PLAYWRIGHT_TREE' },
  ]), async ({ page }) => {
    test.setTimeout(55 * 60_000);
    for (const route of manifest.routes) {
      for (const theme of THEMES) {
        await crawl(page, route, theme, 'default');
      }
    }

    const exclusion = manifest.exclusions.find((entry) => entry.path === '404.html');
    expect(exclusion?.reason, 'the generated 404 output must cite the not-found journey').toMatch(/not-found/i);
    await crawl(page, NOT_FOUND_ROUTE, 'light', 'not-found');

    const artifacts = writeArtifacts(readBaseline());
    if (baselineWriteRequested) {
      writeBaselineSeed(artifacts.ledger.entries);
      console.log(`Seeded ${artifacts.ledger.entries.length} unresolved signatures into ${relativeBaselinePath}`);
    }
    expect(artifacts.routeBlocking, artifacts.summary).toEqual([]);
  });

  test('rejects unresolved contrast signatures', async () => {
    test.skip(baselineWriteRequested, 'Baseline seeding records unresolved signatures for later review.');
    const artifacts = writeArtifacts(readBaseline());
    expect(artifacts.contrastBlocking, artifacts.summary).toEqual([]);
  });
});

test.describe('route and contrast evidence contracts', () => {
  const signatureParts: SignatureParts = {
    html: '<p class="heroSubtitle">Example</p>',
    reason: 'bgImage',
    rule: 'color-contrast',
    state: 'default',
    target: '[".heroSubtitle"]',
    theme: 'light',
  };
  const reviewedEntry = (overrides: Partial<BaselineEntry> = {}): BaselineEntry => ({
    ...signatureParts,
    classification: 'reviewed',
    count: 1,
    evidence: 'docs/contributing/accessibility.md#contrast-exceptions',
    freshnessTriggers: ['shared stylesheet change'],
    owner: 'accessibility-owner',
    rationale: 'Measured by a qualified reviewer.',
    reviewBy: '2999-01-01',
    reviewedOn: '2026-09-14',
    routeFamilies: ['/'],
    signature: contrastSignature(signatureParts),
    ...overrides,
  });
  const ledgerEntry: LedgerEntry = {
    ...signatureParts,
    count: 1,
    measuredFailures: [],
    routeFamilies: ['/'],
    routes: ['/'],
    signature: contrastSignature(signatureParts),
  };
  const routeEvidence = (overrides: Partial<RouteEvidence> = {}): RouteEvidence => ({
    clientNavigation: { documentPreserved: true, lang: 'en', performed: true, title: 'Home', via: '/documentation' },
    contrastEvidence: [],
    errors: [],
    features: {
      activationTriggers: { media: 0 },
      controls: { buttons: 1 },
      graphics: { images: 0, imagesWithoutAlt: 0, svg: 0, svgWithoutAccessibleName: 0 },
      headingLevels: [1, 2],
      landmarks: { banner: 1, contentinfo: 1, main: 1, navigation: 1 },
      links: { total: 1 },
    },
    incomplete: [],
    lang: 'en',
    route: '/',
    state: 'default',
    status: 200,
    theme: 'light',
    title: 'Home',
    violations: [],
    ...overrides,
  });

  test('signatures drop per-build identifiers and keep rendered state', () => {
    expect(normalizeFragment('<h1 class="heroTitle_eUPs" id="_R_6qh_">Title</h1>')).toBe(
      '<h1 class="heroTitle" id="_R_">Title</h1>',
    );
    expect(normalizeFragment('["#mermaid-svg-6464905-flowchart-C-3"]')).toBe('["#mermaid-svg-flowchart-C-3"]');
    expect(normalizeFragment('<input value="typed query" href="/search?q=typed">')).toBe(
      `<input value="${REDACTED}" href="/search?q=${REDACTED}">`,
    );
    expect(contrastSignature({ ...signatureParts, theme: 'dark' })).not.toBe(contrastSignature(signatureParts));
    expect(contrastSignature({ ...signatureParts, state: 'not-found' })).not.toBe(contrastSignature(signatureParts));
  });

  test('computed typography selects the WCAG normal or large-text contrast threshold', () => {
    expect(requiredContrastRatio(16, 400)).toBe(4.5);
    expect(requiredContrastRatio(24, 400)).toBe(3);
    expect(requiredContrastRatio(18.66, 700)).toBe(3);
    expect(requiredContrastRatio(18.66, 600)).toBe(4.5);
  });

  test('the shipped baseline keeps unresolved signatures blocking', () => {
    const baseline = readBaseline();
    expect(baseline.length).toBeGreaterThan(0);
    for (const entry of baseline.filter((candidate) => candidate.classification === 'unresolved')) {
      const observed: LedgerEntry = { ...entry, measuredFailures: [], routes: ['/'] };
      const [assessment] = assessContrastLedger([observed], [entry], { reconcileUnobserved: false, today: today() });
      expect(assessment.status, entry.signature).toBe('unresolved');
    }
  });

  test('the contrast gate blocks unknown, increased, expired, and unreconciled signatures', () => {
    const assess = (ledger: LedgerEntry[], baseline: BaselineEntry[], reconcileUnobserved = false): string[] =>
      assessContrastLedger(ledger, baseline, { reconcileUnobserved, today: '2026-09-14' }).map((item) => item.status);

    expect(assess([ledgerEntry], [reviewedEntry()])).toEqual(['accepted']);
    expect(assess([ledgerEntry], [])).toEqual(['unknown']);
    expect(assess([{ ...ledgerEntry, count: 2 }], [reviewedEntry()])).toEqual(['increased']);
    expect(assess([ledgerEntry], [reviewedEntry({ classification: 'unresolved' })])).toEqual(['unresolved']);
    expect(assess([ledgerEntry], [reviewedEntry({ evidence: null })])).toEqual(['unowned']);
    expect(assess([ledgerEntry], [reviewedEntry({ reviewBy: '2026-09-13' })])).toEqual(['stale']);
    expect(assess([{ ...ledgerEntry, routeFamilies: ['/contributing'] }], [reviewedEntry()])).toEqual(['unreconciled']);
    expect(assess([ledgerEntry], [reviewedEntry({ count: 3 })], true)).toEqual(['decreased']);
    expect(assess([], [reviewedEntry()], true)).toEqual(['unreconciled']);
    expect(
      assess([{ ...ledgerEntry, measuredFailures: ['/: measured 2:1 below the required 4.5:1'] }], [reviewedEntry()]),
    ).toEqual(['measured-failure']);
  });

  test('a reviewed baseline entry needs an owner, deciding evidence, and a review date', () => {
    const document = (entry: BaselineEntry): unknown => ({
      entries: [entry],
      policy: 'contract test',
      schemaVersion: 1,
      source: 'contract test',
    });
    expect(() => validateBaselineDocument(document(reviewedEntry()))).not.toThrow();
    expect(() => validateBaselineDocument(document(reviewedEntry({ evidence: null })))).toThrow(/deciding evidence/);
    expect(() => validateBaselineDocument(document(reviewedEntry({ owner: 'unassigned' })))).toThrow(/deciding evidence/);
    expect(() => validateBaselineDocument(document(reviewedEntry({ reviewBy: null })))).toThrow(/deciding evidence/);
    expect(() => validateBaselineDocument(document(reviewedEntry({ signature: 'tampered' })))).toThrow(/does not match/);
    expect(() => validateBaselineDocument({ entries: [], schemaVersion: 2 })).toThrow(/unsupported schema/);
  });

  test('artifacts stay complete when a route fails', () => {
    const failing = routeEvidence({ route: '/broken', status: 500, title: 'Broken' });
    failing.errors = checkRouteInvariants(failing);
    const healthy = routeEvidence({
      incomplete: [
        {
          id: 'color-contrast',
          nodes: [
            {
              any: [{ data: { messageKey: 'bgImage' } }],
              html: '<p class="heroSubtitle_eUPs">Example</p>',
              target: ['.heroSubtitle_eUPs'],
            },
          ],
        },
      ],
    });
    const expectedKeys = [evidenceKey('/', 'light', 'default'), evidenceKey('/missing', 'light', 'default')];
    const artifacts = buildCrawlArtifacts([healthy, failing], [reviewedEntry()], { expectedKeys, today: '2026-09-14' });

    expect(artifacts.results.results.map((record) => record.route)).toEqual(['/', '/broken']);
    expect(artifacts.ledger.entries).toHaveLength(1);
    expect(artifacts.ledger.entries[0].count).toBe(1);
    expect(artifacts.blocking).toContain('route state never produced evidence: /missing|light|default');
    expect(artifacts.blocking).toContain('route state is not declared by the manifest: /broken|light|default');
    expect(artifacts.blocking.some((finding) => finding.includes('expected HTTP 200 and received 500'))).toBe(true);
    expect(artifacts.routeBlocking).toEqual(artifacts.blocking);
    expect(artifacts.contrastBlocking).toEqual([]);
    expect(artifacts.summary).toContain('expected HTTP 200 and received 500');
    expect(artifacts.summary).toContain('Route states evaluated: 2 of 2 expected');
    // An interrupted run must not claim that unobserved baseline entries are stale.
    expect(artifacts.ledger.assessments.every((assessment) => assessment.status !== 'unreconciled')).toBe(true);
  });
});
