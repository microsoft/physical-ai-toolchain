// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import {
  appendFileSync, copyFileSync, existsSync, lstatSync, mkdirSync, readFileSync,
  readdirSync, realpathSync, writeFileSync,
} from 'node:fs';
import { dirname, isAbsolute, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = fileURLToPath(new URL('../../', import.meta.url));
const SLUG = /^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,99}$/;
const KINDS = new Set(['junit', 'nunit', 'jest', 'json', 'file', 'sarif']);
const TEST_KINDS = new Set(['junit', 'nunit', 'jest']);
const OUTCOMES = new Set(['success', 'failure', 'cancelled', 'skipped']);
const PROBES = {
  node: [process.execPath, ['--version']], git: ['git', ['--version']],
  npm: ['npm', ['--version']], uv: ['uv', ['--version']], python: ['python', ['--version']],
  go: ['go', ['version']], terraform: ['terraform', ['version', '-json']],
  pwsh: ['pwsh', ['-NoProfile', '-Command', '$PSVersionTable.PSVersion.ToString()']],
  shellcheck: ['shellcheck', ['--version']], actionlint: ['actionlint', ['--version']],
  ruff: ['ruff', ['--version']], trivy: ['trivy', ['--version']],
};

function requireValue(condition, message) {
  if (!condition) throw new Error(message);
}

function object(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function safeCount(value, label) {
  requireValue(Number.isSafeInteger(value) && value >= 0, `Invalid ${label} count`);
  return value;
}

function addCounts(a, b) {
  return Object.fromEntries(Object.keys(a).map(key => [key, safeCount(a[key] + b[key], key)]));
}

function testCounts() {
  return { total: 0, executed: 0, skipped: 0, failed: 0, errors: 0 };
}

function exactSet(actual, expected, label) {
  requireValue(Array.isArray(actual) && Array.isArray(expected), `Invalid ${label} set`);
  requireValue(new Set(actual).size === actual.length && new Set(expected).size === expected.length &&
    actual.length === expected.length && expected.every(item => actual.includes(item)), `Mismatched ${label} set`);
}

function ids(value, label) {
  requireValue(Array.isArray(value) && value.every(item => typeof item === 'string' && SLUG.test(item)) &&
    new Set(value).size === value.length, `Invalid ${label}`);
  return value;
}

function safeText(value, fallback = '') {
  return typeof value === 'string' ? value.replace(/[\u0000-\u001f\u007f]/g, ' ').slice(0, 500) : fallback;
}

function xmlText(value) {
  requireValue(!/&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)/.test(value), 'Invalid XML entity');
  return value.replace(/&([^;]+);/g, (_, entity) => {
    const named = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'" };
    if (named[entity]) return named[entity];
    const code = entity.startsWith('#x') ? Number.parseInt(entity.slice(2), 16) : Number(entity.slice(1));
    requireValue(Number.isInteger(code) && code > 0 && code <= 0x10ffff &&
      !(code >= 0xd800 && code <= 0xdfff), 'Invalid XML character');
    return String.fromCodePoint(code);
  });
}

// Only report XML is accepted: external entities and DTDs are never resolved.
function parseXml(text) {
  const document = { name: '', attrs: {}, children: [] };
  const stack = [document];
  const tokens = /<!--[\s\S]*?-->|<\?[\s\S]*?\?>|<!\[CDATA\[[\s\S]*?\]\]>|<\/?[A-Za-z_](?:[^<>"']|"[^"<]*"|'[^'<]*')*>|[^<]+/gy;
  let offset = 0;
  let match;
  while ((match = tokens.exec(text))) {
    requireValue(match.index === offset, 'Malformed report XML');
    offset = tokens.lastIndex;
    const token = match[0];
    if (token.startsWith('<!--') || token.startsWith('<?')) continue;
    if (token.startsWith('<![CDATA[')) {
      requireValue(stack.length > 1, 'CDATA outside XML root');
      continue;
    }
    if (!token.startsWith('<')) {
      requireValue(stack.length > 1 || token.trim() === '', 'Text outside XML root');
      xmlText(token);
      continue;
    }
    if (token.startsWith('</')) {
      requireValue(/^<\/[A-Za-z_][\w:.-]*\s*>$/.test(token) && stack.length > 1 &&
        stack.at(-1).name === token.slice(2, -1).trim(), 'Mismatched XML element');
      stack.pop();
      continue;
    }
    const opening = /^<([A-Za-z_][\w:.-]*)([\s\S]*?)(\/?)>$/.exec(token);
    requireValue(opening, 'Malformed XML element');
    const node = { name: opening[1], attrs: {}, children: [] };
    let rest = opening[2];
    while (rest.trim()) {
      const attr = /^\s+([A-Za-z_][\w:.-]*)\s*=\s*(?:"([^"]*)"|'([^']*)')/.exec(rest);
      requireValue(attr && !Object.hasOwn(node.attrs, attr[1]), 'Malformed or duplicate XML attribute');
      node.attrs[attr[1]] = xmlText(attr[2] ?? attr[3]);
      rest = rest.slice(attr[0].length);
    }
    stack.at(-1).children.push(node);
    if (!opening[3]) stack.push(node);
  }
  requireValue(offset === text.length && stack.length === 1 && document.children.length === 1, 'Malformed report XML');
  return document.children[0];
}

function checkDeclared(attrs, key, expected) {
  if (!Object.hasOwn(attrs, key)) return;
  requireValue(/^(0|[1-9]\d*)$/.test(attrs[key]) && Number.isSafeInteger(Number(attrs[key])) &&
    Number(attrs[key]) === expected, `Contradictory report ${key} count`);
}

function parseXmlTests(text, kind) {
  const root = parseXml(text);
  requireValue((kind === 'junit' ? ['testsuites', 'testsuite'] : ['test-run', 'test-results', 'test-suite'])
    .includes(root.name), `Invalid ${kind} report root`);
  const seen = new Set();
  function walk(node) {
    if (node.name === (kind === 'junit' ? 'testcase' : 'test-case')) {
      const { attrs } = node;
      requireValue(typeof attrs.name === 'string' && attrs.name.length > 0, 'Missing testcase identity');
      requireValue(!node.children.some(child => ['testcase', 'test-case', 'testsuite', 'test-suite'].includes(child.name)),
        'Nested testcase evidence');
      if (kind === 'nunit') {
        // Pester parameterized cases can share a display name without an explicit identity.
        const identity = attrs.id || attrs.fullname;
        if (identity) {
          requireValue(!seen.has(identity), 'Duplicate testcase identity');
          seen.add(identity);
        }
      }
      let skipped;
      let failed;
      let errors = false;
      if (kind === 'junit') {
        skipped = node.children.some(child => child.name === 'skipped');
        failed = node.children.some(child => child.name === 'failure');
        errors = node.children.some(child => child.name === 'error');
        requireValue(Number(skipped) + Number(failed) + Number(errors) <= 1, 'Contradictory testcase outcome');
      } else {
        const result = (attrs.result ?? '').toLowerCase();
        requireValue(['passed', 'success', 'failed', 'failure', 'error', 'skipped', 'ignored',
          'inconclusive', 'notrunnable', 'cancelled', 'warning'].includes(result), 'Invalid NUnit testcase result');
        requireValue(attrs.executed === undefined || ['True', 'False', 'true', 'false'].includes(attrs.executed),
          'Invalid NUnit executed flag');
        skipped = ['skipped', 'ignored', 'inconclusive'].includes(result) || attrs.executed?.toLowerCase() === 'false';
        failed = ['failed', 'failure', 'error', 'notrunnable', 'cancelled', 'warning'].includes(result);
        requireValue(!(skipped && failed), 'Contradictory NUnit testcase outcome');
      }
      return { total: 1, executed: Number(!skipped), skipped: Number(skipped),
        failed: Number(failed || errors), errors: Number(errors) };
    }
    const counts = node.children.reduce((sum, child) => addCounts(sum, walk(child)), testCounts());
    if (kind === 'junit' && ['testsuite', 'testsuites'].includes(node.name)) {
      checkDeclared(node.attrs, 'tests', counts.total);
      checkDeclared(node.attrs, 'failures', counts.failed - counts.errors);
      checkDeclared(node.attrs, 'errors', counts.errors);
      checkDeclared(node.attrs, 'skipped', counts.skipped);
    }
    if (kind === 'nunit' && ['test-run', 'test-suite'].includes(node.name)) {
      checkDeclared(node.attrs, 'total', counts.total);
      checkDeclared(node.attrs, 'passed', counts.executed - counts.failed);
      checkDeclared(node.attrs, 'failed', counts.failed);
      if (node.attrs.skipped !== undefined || node.attrs.inconclusive !== undefined) {
        const skipped = safeCount(Number(node.attrs.skipped ?? 0), 'skipped');
        const inconclusive = safeCount(Number(node.attrs.inconclusive ?? 0), 'inconclusive');
        requireValue(skipped + inconclusive === counts.skipped, 'Contradictory NUnit skipped counts');
      }
    }
    if (kind === 'nunit' && node.name === 'test-results') {
      checkDeclared(node.attrs, 'total', counts.total);
      if (node.attrs['not-run'] !== undefined) {
        checkDeclared(node.attrs, 'not-run', safeCount(Number(node.attrs['not-run']), 'not-run'));
      }
      const skipFields = ['skipped', 'ignored', 'inconclusive'].filter(key => node.attrs[key] !== undefined);
      if (skipFields.length) {
        const skipped = skipFields.reduce((sum, key) => sum + safeCount(Number(node.attrs[key]), key), 0);
        requireValue(skipped === counts.skipped, 'Contradictory NUnit skipped counts');
      }
      if (node.attrs.failures !== undefined && node.attrs.errors !== undefined) {
        const failures = Number(node.attrs.failures);
        const errors = Number(node.attrs.errors);
        safeCount(failures, 'failures');
        safeCount(errors, 'errors');
        requireValue(failures + errors === counts.failed, 'Contradictory NUnit failure counts');
      }
    }
    return counts;
  }
  return walk(root);
}

function parseJest(data) {
  requireValue(object(data) && Array.isArray(data.testResults) && data.testResults.length > 0,
    'Invalid Jest report');
  const seen = new Set();
  const counts = testCounts();
  let todo = 0;
  for (const suite of data.testResults) {
    requireValue(object(suite) && typeof suite.name === 'string' && Array.isArray(suite.assertionResults),
      'Invalid Jest suite');
    requireValue(['passed', 'failed', 'pending'].includes(suite.status), 'Invalid Jest suite status');
    for (const assertion of suite.assertionResults) {
      requireValue(object(assertion) && typeof assertion.fullName === 'string' && assertion.fullName.length > 0,
        'Missing Jest assertion identity');
      const id = JSON.stringify([suite.name, assertion.fullName]);
      requireValue(!seen.has(id), 'Duplicate Jest assertion identity');
      seen.add(id);
      requireValue(['passed', 'failed', 'pending', 'todo', 'skipped', 'disabled'].includes(assertion.status),
        'Invalid Jest assertion status');
      const skipped = !['passed', 'failed'].includes(assertion.status);
      counts.total++;
      counts.skipped += Number(skipped);
      counts.executed += Number(!skipped);
      counts.failed += Number(assertion.status === 'failed');
      todo += Number(assertion.status === 'todo');
    }
    requireValue(suite.status !== 'failed' || suite.assertionResults.some(item => item.status === 'failed'),
      'Jest suite execution failed');
    requireValue(suite.status !== 'pending' || suite.assertionResults.every(item =>
      !['passed', 'failed'].includes(item.status)), 'Contradictory pending Jest suite');
  }
  const expected = {
    numTotalTests: counts.total, numPassedTests: counts.executed - counts.failed, numFailedTests: counts.failed,
    numPendingTests: counts.skipped - todo,
  };
  if (data.numTodoTests !== undefined || todo > 0) expected.numTodoTests = todo;
  for (const [key, count] of Object.entries(expected)) {
    requireValue(safeCount(data[key], key) === count, `Contradictory Jest ${key} count`);
  }
  const suites = { numTotalTestSuites: data.testResults.length,
    numPassedTestSuites: data.testResults.filter(suite => suite.status === 'passed').length,
    numFailedTestSuites: data.testResults.filter(suite => suite.status === 'failed').length,
    numPendingTestSuites: data.testResults.filter(suite => suite.status === 'pending').length };
  for (const [key, count] of Object.entries(suites)) {
    if (data[key] !== undefined) requireValue(safeCount(data[key], key) === count, `Contradictory Jest ${key} count`);
  }
  if (data.numRuntimeErrorTestSuites !== undefined) {
    requireValue(safeCount(data.numRuntimeErrorTestSuites, 'runtime errors') === 0, 'Jest suite execution failed');
  }
  requireValue(typeof data.success === 'boolean' && data.success === (counts.failed === 0),
    'Contradictory Jest success');
  return counts;
}

export function parseTestReport(text, kind) {
  const counts = kind === 'jest' ? parseJest(JSON.parse(text)) : parseXmlTests(text, kind);
  requireValue(counts.executed > 0, 'Required test report contains no executed testcases');
  return counts;
}

function containedPath(root, name, mustExist = true) {
  requireValue(typeof name === 'string' && name.length > 0 && !isAbsolute(name) &&
    !/^[A-Za-z]:|[\\\u0000-\u001f\u007f]/.test(name) && !name.split('/').some(part => part === '..' || part === ''),
  'Report path must be workspace-relative');
  const base = resolve(root);
  const destination = resolve(base, name);
  requireValue(destination !== base && destination.startsWith(`${base}${sep}`), 'Report path escapes workspace');
  if (mustExist) {
    requireValue(existsSync(destination) && !lstatSync(destination).isSymbolicLink(), 'Missing or linked report');
    const real = realpathSync(destination);
    requireValue(real.startsWith(`${realpathSync(base)}${sep}`), 'Report path escapes workspace');
  }
  return destination;
}

function reportFiles(root, path, kind) {
  const destination = containedPath(root, path);
  if (lstatSync(destination).isFile()) return [path];
  requireValue(kind === 'sarif' && lstatSync(destination).isDirectory(), 'Only SARIF reports may be directories');
  const files = [];
  function collect(folder) {
    for (const entry of readdirSync(containedPath(root, folder), { withFileTypes: true })) {
      requireValue(!entry.isSymbolicLink(), 'Linked SARIF report');
      const child = `${folder}/${entry.name}`;
      if (entry.isDirectory()) collect(child);
      else if (entry.isFile() && /\.sarif(?:\.json)?$/i.test(entry.name)) files.push(child);
    }
  }
  collect(path);
  requireValue(files.length > 0 && files.length <= 1000, 'Missing or excessive SARIF reports');
  return files.sort();
}

function structuredCounts(data, spec) {
  if (Array.isArray(data)) {
    requireValue(data.length > 0 && data.every(result => object(result) &&
      typeof result.File === 'string' && result.File.length > 0 &&
      typeof result.MsDate === 'string' && result.MsDate.length > 0 &&
      Number.isSafeInteger(result.AgeDays) && Number.isSafeInteger(result.Threshold) && result.Threshold >= 0 &&
      typeof result.IsStale === 'boolean' && result.IsStale === (result.AgeDays > result.Threshold)) &&
      new Set(data.map(result => result.File)).size === data.length, 'Invalid date freshness report');
    requireValue(spec.tool === undefined, 'Unexpected freshness report tool context');
    const findings = data.filter(result => result.IsStale).length;
    return { findings, successful: findings === 0 };
  }
  requireValue(object(data) && Object.keys(data).length > 0, 'Invalid structured JSON report');
  if (spec.tool) requireValue(data.tool === spec.tool || data.summary?.tool === spec.tool, 'Wrong JSON tool');
  const summary = object(data.summary) ? data.summary : data;
  const array = value => Array.isArray(value) ? value : object(value) ? [value] : null;
  const results = array(data.results);
  const issues = array(data.issues);
  const count = (source, key) => safeCount(source[key], key);
  const boolean = (source, key) => {
    requireValue(typeof source[key] === 'boolean', `Invalid JSON ${key}`);
    return source[key];
  };
  const unique = (items, key) => {
    requireValue(items && items.every(item => object(item) && typeof item[key] === 'string' && item[key].length > 0) &&
      new Set(items.map(item => item[key])).size === items.length, 'Invalid or duplicate JSON target identity');
  };
  let findings = 0;
  let successful = true;
  for (const [key, value] of Object.entries(summary)) {
    if (/count|total|passed|failed|errors|warnings|checked|violations/i.test(key) &&
        !['overall_passed', 'check_passed', 'lint_passed', 'format_passed', 'execution_errors'].includes(key) &&
        !(summary === data && ['errors', 'Violations'].includes(key))) safeCount(value, key);
  }
  if (Object.hasOwn(summary, 'images_count')) {
    requireValue(count(summary, 'images_count') > 0 && results?.length === summary.images_count,
      'Contradictory image discovery count');
    unique(results, 'shard');
    requireValue(results.every(result => SLUG.test(result.shard) &&
      typeof result.image === 'string' && /^[^\s]+@sha256:[a-f0-9]{64}$/i.test(result.image)),
    'Invalid immutable image discovery target');
    successful = boolean(summary, 'overall_passed');
  } else if (Object.hasOwn(data, 'projects_count')) {
    const total = count(data, 'projects_count');
    requireValue(total > 0 && results?.length === total, 'Contradictory lock project count');
    unique(results, 'Project');
    const failed = results.filter(result => !boolean(result, 'Passed')).length;
    requireValue(count(data, 'drift_count') === failed && boolean(data, 'check_passed') === (failed === 0),
      'Contradictory lock results');
    findings = failed;
    successful = failed === 0;
  } else if (Object.hasOwn(summary, 'total_issues')) {
    findings = count(summary, 'total_issues');
    const findingsList = issues ?? (data.issues === null && findings === 0 ? [] : null);
    requireValue(findingsList !== null && findingsList.every(object) && findingsList.length === findings &&
      count(summary, 'files_affected') <= findings, 'Contradictory link language findings');
    successful = findings === 0;
  } else if (Object.hasOwn(summary, 'total_files')) {
    const total = count(summary, 'total_files');
    findings = count(summary, 'total_broken_links');
    const broken = array(data.broken_links) ?? (data.broken_links === null && findings === 0 ? [] : null);
    requireValue(total > 0 && broken !== null && broken.every(object) &&
      count(summary, 'files_with_broken_links') <= total && summary.files_with_broken_links <= findings &&
      findings >= broken.length && findings <= count(summary, 'total_links_checked') &&
      (findings === 0) === (summary.files_with_broken_links === 0) &&
      (findings === 0) === (broken.length === 0), 'Contradictory Markdown link results');
    successful = findings === 0;
  } else if (Object.hasOwn(data, 'TotalDependencies')) {
    const total = count(data, 'TotalDependencies');
    const pinned = count(data, 'PinnedDependencies');
    findings = count(data, 'UnpinnedDependencies');
    const violations = data.Violations;
    const expectedScore = total === 0 ? 100 : pinned / total * 100;
    requireValue(count(data, 'ScannedFiles') > 0 && Array.isArray(violations) &&
      violations.every(result => object(result) && typeof result.Severity === 'string' && result.Severity.length > 0) &&
      violations.length === total && pinned + findings === total &&
      violations.filter(result => result.Severity === 'Info').length === pinned &&
      violations.filter(result => result.Severity !== 'Info').length === findings &&
      Number.isFinite(data.ComplianceScore) && data.ComplianceScore >= 0 && data.ComplianceScore <= 100 &&
      Math.abs(data.ComplianceScore - expectedScore) <= 0.005000001,
    'Contradictory dependency pinning results');
    // The required pinning operation enforces its configured compliance threshold.
  } else if (Object.hasOwn(summary, 'totalFiles')) {
    requireValue(count(summary, 'totalFiles') > 0, 'No files checked in JSON report');
    findings = safeCount(count(summary, 'errorCount') + count(summary, 'warningCount'), 'findings');
    successful = summary.errorCount === 0;
    if (Object.hasOwn(summary, 'passedFiles')) {
      requireValue(results?.length === summary.totalFiles &&
        count(summary, 'passedFiles') + count(summary, 'failedFiles') === summary.totalFiles,
      'Contradictory JSON file counts');
      unique(results, 'file');
      const failed = results.filter(result => !boolean(result, 'isValid')).length;
      requireValue(failed === summary.failedFiles, 'Contradictory JSON validation results');
      successful &&= failed === 0;
    } else {
      const findingsList = results || issues ||
        (findings === 0 && (Object.hasOwn(data, 'results') || Object.hasOwn(data, 'issues')) ? [] : null);
      requireValue(findingsList !== null && findingsList.every(object), 'Missing JSON findings array');
      if (Object.hasOwn(data, 'lint_passed')) successful = boolean(data, 'lint_passed') && successful;
    }
  } else if (Object.hasOwn(data, 'files_checked')) {
    findings = safeCount(count(data, 'error_count') + count(data, 'warning_count'), 'findings');
    requireValue(count(data, 'files_checked') > 0 && (issues?.every(object) ||
      data.issues === null && findings === 0), 'Missing ShellCheck execution results');
    successful = boolean(data, 'lint_passed');
    requireValue(!successful || data.error_count === 0, 'Contradictory ShellCheck result');
    if (Object.hasOwn(data, 'execution_errors')) {
      requireValue(Array.isArray(data.execution_errors), 'Invalid ShellCheck execution errors');
      successful &&= data.execution_errors.length === 0;
    }
  } else if (object(data.format_check) && Array.isArray(data.validation)) {
    requireValue(data.validation.length > 0, 'Missing Terraform validation results');
    unique(data.validation, 'directory');
    successful = boolean(data.format_check, 'passed');
    for (const result of data.validation) {
      successful = boolean(result, 'passed') && !boolean(result, 'skipped') && successful;
    }
    requireValue(count(summary, 'directories_checked') === data.validation.length, 'Contradictory Terraform directory count');
    if (Object.hasOwn(summary, 'directories_expected')) {
      requireValue(count(summary, 'directories_expected') === data.validation.length, 'Missing expected Terraform directories');
    }
    if (Object.hasOwn(summary, 'directories_passed')) {
      requireValue(count(summary, 'directories_passed') === data.validation.filter(result =>
        result.passed && !result.skipped).length, 'Contradictory Terraform passed count');
    }
    if (Object.hasOwn(summary, 'directories_skipped')) {
      requireValue(count(summary, 'directories_skipped') === data.validation.filter(result =>
        result.skipped).length, 'Contradictory Terraform skipped count');
    }
    if (Object.hasOwn(summary, 'format_passed')) {
      requireValue(boolean(summary, 'format_passed') === data.format_check.passed, 'Contradictory Terraform format result');
    }
    if (Object.hasOwn(summary, 'overall_passed')) {
      requireValue(boolean(summary, 'overall_passed') === successful, 'Contradictory Terraform overall result');
    }
  } else if (typeof summary.overall_passed === 'boolean' &&
      (typeof data.terraform_docs_version === 'string' || typeof data.golangci_lint_version === 'string')) {
    successful = summary.overall_passed;
    if (Object.hasOwn(data, 'lint_passed')) {
      requireValue(boolean(data, 'lint_passed') === successful, 'Contradictory Go lint status');
      findings = count(data, 'violation_count');
      requireValue(!successful || findings === 0, 'Contradictory Go lint findings');
    } else {
      successful = !boolean(data, 'skipped') && !boolean(data, 'drift_detected') && successful;
      if (Object.hasOwn(summary, 'files_drifted')) {
        findings = count(summary, 'files_drifted');
        requireValue(Array.isArray(data.drifted_files) && data.drifted_files.length === findings &&
          data.drift_detected === (findings > 0), 'Contradictory Terraform documentation drift');
      }
    }
  } else if (Object.hasOwn(data, 'issues') && Object.hasOwn(data, 'errors')) {
    requireValue(issues !== null && Array.isArray(data.errors) && issues.every(object) &&
      data.errors.every(error => object(error) || typeof error === 'string'), 'Invalid TFLint findings');
    findings = safeCount(issues.length + data.errors.length, 'findings');
    successful = findings === 0;
  } else {
    requireValue(object(data.summary) && Object.keys(summary).length > 0 && results !== null &&
      results.every(item => object(item) && Object.keys(item).length > 0), 'Missing structured JSON summary/results');
    if (Object.hasOwn(summary, 'total')) requireValue(count(summary, 'total') > 0 &&
      summary.total === results.length, 'Contradictory structured JSON total');
    for (const [key, value] of Object.entries(summary)) {
      if (/^(errors|warnings|violations|findings)$/i.test(key)) findings = safeCount(findings + value, 'findings');
    }
    successful = (summary.failed ?? 0) === 0 && (summary.errors ?? 0) === 0 && summary.overall_passed !== false;
  }
  return { findings, successful };
}

export function inspectReport(root, spec) {
  requireValue(object(spec) && KINDS.has(spec.kind), 'Invalid report specification');
  const files = reportFiles(root, spec.path, spec.kind);
  let counts = testCounts();
  let findings = 0;
  let successful = true;
  const evidence = [];
  for (const path of files) {
    const bytes = readFileSync(containedPath(root, path));
    requireValue((spec.kind === 'file' || bytes.length > 0) && bytes.length <= 64 * 1024 * 1024,
      'Empty or excessive report');
    const text = bytes.toString('utf8').replace(/^\uFEFF/, '');
    if (TEST_KINDS.has(spec.kind)) counts = addCounts(counts, parseTestReport(text, spec.kind));
    else if (spec.kind === 'sarif') {
      const data = JSON.parse(text);
      requireValue(object(data) && data.version === '2.1.0' && Array.isArray(data.runs) && data.runs.length > 0,
        'Invalid SARIF runs');
      for (const run of data.runs) {
        const results = run?.results === undefined ? [] : run.results;
        requireValue(typeof run?.tool?.driver?.name === 'string' && run.tool.driver.name.trim() &&
          Array.isArray(results), 'Invalid SARIF tool/results');
        if (spec.tool) requireValue(run.tool.driver.name === spec.tool, 'Wrong SARIF tool');
        requireValue(results.every(result => object(result) && object(result.message) &&
          (typeof result.message.text === 'string' || typeof result.message.markdown === 'string')),
        'Invalid SARIF result');
        requireValue(!run.invocations?.some(invocation => invocation.executionSuccessful === false),
          'SARIF tool execution failed');
        findings = safeCount(findings + results.length, 'findings');
      }
    } else if (spec.kind === 'json') {
      const result = structuredCounts(JSON.parse(text), spec);
      findings = safeCount(findings + result.findings, 'findings');
      successful &&= result.successful;
    }
    else requireValue(!/\.(?:json|sarif)$/i.test(path), 'Structured evidence requires its report adapter');
    evidence.push({ path, bytes: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex') });
  }
  return { ...spec, counts, findings, successful: successful && counts.failed === 0, files: evidence };
}

function canonicalJob(contract, workflow, job) {
  const definition = contract?.execution?.[workflow]?.jobs?.[job];
  requireValue(object(definition), 'Missing canonical execution job');
  ids(definition['required-steps'], 'canonical required steps');
  requireValue(definition['required-steps'].length > 0 && Array.isArray(definition.reports), 'Empty canonical operations');
  return definition;
}

function canonicalReports(definition, shard) {
  return definition.reports.map(report => ({ ...report, path: report.path.replaceAll('{shard}', shard) }));
}

function reportKeys(reports, includeTool = true) {
  requireValue(Array.isArray(reports) && reports.every(report => object(report) && KINDS.has(report.kind) &&
    typeof report.path === 'string'), 'Invalid required reports');
  return reports.map(report => JSON.stringify([report.kind, report.path, includeTool ? report.tool ?? null : null]));
}

function identity(options) {
  const { workflow, job, shard = 'default', target = shard, runId, runAttempt, sha } = options;
  requireValue([workflow, job, shard].every(value => typeof value === 'string' && SLUG.test(value)), 'Invalid receipt identity');
  requireValue([runId, runAttempt].every(value => typeof value === 'string' && /^[1-9]\d*$/.test(value)),
    'Invalid run identity');
  requireValue(typeof sha === 'string' && /^[a-f0-9]{40}(?:[a-f0-9]{24})?$/i.test(sha), 'Invalid commit identity');
  requireValue(typeof target === 'string' && target.length > 0 && target.length <= 1000 &&
    !/[\u0000-\u0020\u007f]/.test(target), 'Invalid target identity');
  return { workflow, job, shard, target, 'run-id': runId, 'run-attempt': runAttempt, sha };
}

export function probeTools(names = ['node', 'git']) {
  ids(names, 'tool probes');
  return Object.fromEntries(names.map(name => {
    requireValue(Object.hasOwn(PROBES, name), 'Unknown tool probe');
    const [command, args] = PROBES[name];
    const result = spawnSync(command, args, { encoding: 'utf8', timeout: 5000, maxBuffer: 16 * 1024, shell: false });
    const version = result.status === 0 ? safeText(result.stdout || result.stderr).slice(0, 160) : 'unavailable';
    return [name, version];
  }));
}

function emptyCounts() {
  return { expected: 0, executed: 0, tests: 0, 'skipped-tests': 0, 'skipped-operations': 0, findings: 0 };
}

function initialReceipt(options, mode) {
  const id = identity(options);
  requireValue(typeof options.selected === 'boolean', 'Selected must be a boolean');
  const selectionReason = safeText(options.selectionReason);
  requireValue(selectionReason.length > 0, 'Missing selection reason');
  return { version: 1, mode, ...id, selected: options.selected, 'selection-reason': selectionReason,
    operations: [], reports: [], counts: emptyCounts(), tools: {}, errors: [],
    'work-status': 'failure', 'first-failure': '',
    'artifact-name': `ci-outcome-${id.workflow}-${id.job}-${id.shard}-${id['run-id']}-${id['run-attempt']}` };
}

function failure(receipt, operation, message) {
  receipt.errors.push(safeText(message));
  receipt['first-failure'] ||= safeText(operation);
}

function finish(receipt) {
  receipt['work-status'] = receipt.operations.some(operation => operation.outcome === 'cancelled') ||
    receipt.children?.some(child => child['work-status'] === 'cancelled') ? 'cancelled' :
    receipt.errors.length > 0 ? 'failure' : receipt.selected ? 'success' : 'planned-skip';
  return receipt;
}

export function recordOutcome(options, contract) {
  const receipt = initialReceipt(options, 'record');
  try {
    const definition = canonicalJob(contract, receipt.workflow, receipt.job);
    if (definition.shards && !definition.discovery) {
      requireValue(ids(definition.shards, 'canonical shards').includes(receipt.shard), 'Unexpected canonical shard');
    }
    const expectedReports = canonicalReports(definition, receipt.shard);
    exactSet(ids(options.requiredSteps, 'required steps'), definition['required-steps'], 'required operations');
    exactSet(reportKeys(options.reports, false), reportKeys(expectedReports, false), 'required reports');
    requireValue(options.reports.every(report => report.tool === undefined || expectedReports.some(expected =>
      expected.path === report.path && expected.kind === report.kind && expected.tool === report.tool)),
    'Wrong declared report tool');
    if (definition.target) requireValue(receipt.target === definition.target.replaceAll('{shard}', receipt.shard),
      'Wrong canonical target');
    requireValue(object(options.steps), 'Missing step outcomes');
    receipt.counts.expected = receipt.selected ? definition['required-steps'].length : 0;
    for (const id of definition['required-steps']) {
      const step = options.steps[id];
      const outcome = step?.outcome ?? 'missing';
      receipt.operations.push({ id, outcome, conclusion: OUTCOMES.has(step?.conclusion) ? step.conclusion : 'missing' });
      if (['success', 'failure'].includes(outcome)) receipt.counts.executed++;
      if (outcome === 'skipped') receipt.counts['skipped-operations']++;
      if (receipt.selected ? outcome !== 'success' : outcome !== 'skipped') {
        failure(receipt, id, receipt.selected ? `Required operation ${id}: ${outcome}` : `Unselected operation ${id} ran or is missing`);
      }
    }
    if (receipt.selected) {
      for (const spec of expectedReports) {
        try {
          const report = inspectReport(options.workspace ?? ROOT, spec);
          receipt.reports.push(report);
          receipt.counts.tests = safeCount(receipt.counts.tests + report.counts.executed, 'tests');
          receipt.counts['skipped-tests'] = safeCount(receipt.counts['skipped-tests'] + report.counts.skipped, 'skipped tests');
          receipt.counts.findings = safeCount(receipt.counts.findings + report.findings, 'findings');
          if (!report.successful) failure(receipt, `report:${spec.path}`, 'Required report contains failed checks');
        } catch {
          failure(receipt, `report:${spec.path}`, `Invalid or missing required ${spec.kind} report: ${spec.path}`);
          try {
            const files = reportFiles(options.workspace ?? ROOT, spec.path, spec.kind).map(path => {
              const bytes = readFileSync(containedPath(options.workspace ?? ROOT, path));
              return { path, bytes: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex') };
            });
            receipt.reports.push({ ...spec, counts: testCounts(), findings: 0, files, invalid: true });
          } catch {
            // Missing reports remain explicit errors; available reports are still published.
          }
        }
      }
    }
    receipt.tools = probeTools(options.tools ?? ['node', 'git']);
  } catch (error) {
    failure(receipt, 'contract', error.message);
  }
  return finish(receipt);
}

function expectedTargets(options, definition, job, shards) {
  const provided = options.expectedTargets?.[job] ?? {};
  requireValue(object(provided), 'Invalid expected targets');
  if (Object.keys(provided).length) exactSet(Object.keys(provided), shards, 'target shards');
  const targets = Object.fromEntries(shards.map(shard =>
    [shard, provided[shard] ?? definition.target?.replaceAll('{shard}', shard) ?? shard]));
  if (definition.target) requireValue(shards.every(shard =>
    targets[shard] === definition.target.replaceAll('{shard}', shard)), 'Wrong expected canonical target');
  if (definition.discovery) {
    const discovery = options.needs[definition.discovery.job];
    requireValue(discovery?.result === 'success', 'Target discovery did not succeed');
    const independentShards = JSON.parse(discovery.outputs?.[definition.discovery['shards-output']] ?? 'null');
    const independentTargets = JSON.parse(discovery.outputs?.[definition.discovery['targets-output']] ?? 'null');
    exactSet(shards, ids(independentShards, 'discovered shards'), 'discovered shards');
    requireValue(object(independentTargets), 'Missing independently discovered targets');
    exactSet(Object.keys(independentTargets), shards, 'discovered targets');
    requireValue(shards.every(shard => targets[shard] === independentTargets[shard]), 'Mismatched discovered target identity');
  }
  return targets;
}

function validateChild(child, options, contract, target) {
  requireValue(object(child) && child.version === 1 && child.mode === 'record', 'Invalid child receipt schema');
  const id = identity({ workflow: child.workflow, job: child.job, shard: child.shard, target: child.target,
    runId: child['run-id'], runAttempt: child['run-attempt'], sha: child.sha });
  requireValue(id.workflow === options.workflow && id['run-id'] === options.runId &&
    id['run-attempt'] === options.runAttempt && id.sha === options.sha && id.target === target, 'Stale or mismatched child identity');
  requireValue(child['artifact-name'] ===
    `ci-outcome-${id.workflow}-${id.job}-${id.shard}-${id['run-id']}-${id['run-attempt']}`, 'Mismatched child artifact identity');
  requireValue(child.selected === true && child['work-status'] === 'success' && Array.isArray(child.errors) &&
    child.errors.length === 0, 'Child did not successfully execute selected work');
  const definition = canonicalJob(contract, child.workflow, child.job);
  const reports = canonicalReports(definition, child.shard);
  requireValue(Array.isArray(child.operations), 'Missing child operations');
  exactSet(child.operations.map(operation => operation.id), definition['required-steps'], 'child operations');
  requireValue(child.operations.every(operation => operation.outcome === 'success'), 'Child operation was not successful');
  exactSet(reportKeys(child.reports), reportKeys(reports), 'child reports');
  const counts = emptyCounts();
  counts.expected = counts.executed = definition['required-steps'].length;
  for (const report of child.reports) {
    const canonical = reports.find(item => item.path === report.path && item.kind === report.kind);
    requireValue(options.artifactRoots?.has(child), 'Missing raw child artifact');
    const rawRoot = options.artifactRoots.get(child);
    const measured = inspectReport(resolve(rawRoot, 'reports'), canonical);
    requireValue(JSON.stringify(measured) === JSON.stringify(report), 'Contradictory raw child report evidence');
    requireValue(measured.successful, 'Child report has failed checks');
    counts.tests = safeCount(counts.tests + measured.counts.executed, 'tests');
    counts['skipped-tests'] = safeCount(counts['skipped-tests'] + measured.counts.skipped, 'skipped tests');
    counts.findings = safeCount(counts.findings + measured.findings, 'findings');
  }
  requireValue(object(child.counts), 'Missing child counts');
  exactSet(Object.keys(child.counts), Object.keys(counts), 'child count fields');
  requireValue(Object.entries(counts).every(([key, value]) => safeCount(child.counts[key], key) === value),
    'Contradictory child operation/test counts');
  return counts;
}

export function aggregateOutcomes(options, contract, children) {
  const receipt = initialReceipt({ ...options, shard: options.shard ?? 'aggregate' }, 'aggregate');
  receipt.children = [];
  try {
    requireValue(object(options.expectedShards) && object(options.needs), 'Missing expected shard/needs mapping');
    const jobs = contract?.execution?.[options.workflow]?.jobs;
    requireValue(object(jobs) && Object.keys(jobs).length > 0, 'Missing canonical aggregate jobs');
    exactSet(Object.keys(options.expectedShards), Object.keys(jobs), 'aggregate jobs');
    if (options.expectedTargets) {
      requireValue(object(options.expectedTargets) && Object.keys(options.expectedTargets).every(job => Object.hasOwn(jobs, job)),
        'Unexpected target job');
    }
    const expected = new Map();
    for (const job of Object.keys(jobs)) {
      if (options.needs[job]?.result === 'cancelled') receipt.children.push({ job, 'work-status': 'cancelled' });
    }
    for (const [job, shards] of Object.entries(options.expectedShards)) {
      ids(shards, 'expected shards');
      const definition = canonicalJob(contract, options.workflow, job);
      if (shards.length > 0 && definition.shards && !definition.discovery) {
        exactSet(shards, ids(definition.shards, 'canonical shards'), 'canonical shard inventory');
      }
      const targets = expectedTargets(options, definition, job, shards);
      const result = options.needs[job]?.result;
      if (shards.length === 0) {
        requireValue(result === 'skipped', 'Unselected child was not skipped');
      } else {
        requireValue(result === 'success', `Selected child ${job}: ${result ?? 'missing'}`);
        for (const shard of shards) expected.set(`${job}/${shard}`, targets[shard]);
      }
    }
    requireValue(options.selected === (expected.size > 0), 'Aggregate selection contradicts expected shards');
    requireValue(Array.isArray(children), 'Missing child receipts');
    const found = new Set();
    for (const child of children) {
      const key = `${child?.job}/${child?.shard}`;
      requireValue(expected.has(key) && !found.has(key), 'Duplicate or unexpected child receipt');
      found.add(key);
      const counts = validateChild(child, options, contract, expected.get(key));
      receipt.counts = addCounts(receipt.counts, counts);
      receipt.children.push({ job: child.job, shard: child.shard, target: child.target,
        'artifact-name': child['artifact-name'], 'work-status': child['work-status'], counts });
    }
    exactSet([...found], [...expected.keys()], 'child receipts');
    receipt.tools = probeTools(options.tools ?? ['node', 'git']);
  } catch (error) {
    failure(receipt, 'aggregate', error.message);
  }
  return finish(receipt);
}

export function outcomeOutputs(receipt) {
  return { 'work-status': receipt['work-status'], 'expected-count': String(receipt.counts.expected),
    'executed-count': String(receipt.counts.executed), 'test-count': String(receipt.counts.tests),
    'skipped-count': String(receipt.counts['skipped-tests'] + receipt.counts['skipped-operations']),
    'first-failure': receipt['first-failure'], 'artifact-name': receipt['artifact-name'] };
}

export function publishOutcome(receipt, options = {}) {
  const workspace = options.workspace ?? ROOT;
  const destination = containedPath(workspace, options.outputDirectory ?? `.ci-outcomes/${receipt['artifact-name']}`, false);
  requireValue(!existsSync(destination), 'Evidence output directory already exists');
  mkdirSync(destination, { recursive: true });
  for (const report of receipt.reports) {
    for (const file of report.files) {
      const source = containedPath(workspace, file.path);
      requireValue(createHash('sha256').update(readFileSync(source)).digest('hex') === file.sha256, 'Report changed before publication');
      const target = containedPath(destination, `reports/${file.path}`, false);
      mkdirSync(dirname(target), { recursive: true });
      copyFileSync(source, target);
    }
  }
  writeFileSync(resolve(destination, 'receipt.json'), `${JSON.stringify(receipt, null, 2)}\n`);
  const outputs = { ...outcomeOutputs(receipt), 'artifact-path': destination };
  if (options.githubOutput) appendFileSync(options.githubOutput,
    Object.entries(outputs).map(([key, value]) => `${key}=${value}\n`).join(''));
  if (options.githubSummary) {
    const escape = value => String(value).replace(/[&<>|`[\]\\]/g, character => `&#${character.charCodeAt(0)};`)
      .replace(/[\r\n]/g, ' ');
    const rows = Object.entries(outputs).filter(([key]) => key !== 'artifact-path');
    appendFileSync(options.githubSummary, `\n### CI execution: ${escape(receipt.workflow)} / ${escape(receipt.shard)}\n\n` +
      '| Evidence | Value |\n| --- | --- |\n' +
      rows.map(([key, value]) => `| ${escape(key)} | ${escape(value)} |\n`).join('') +
      `| Selection | ${escape(receipt['selection-reason'])} |\n| Findings | ${receipt.counts.findings} |\n` +
      `| Tools | ${escape(JSON.stringify(receipt.tools))} |\n`);
  }
  return outputs;
}

export function readReceipts(directory) {
  const children = [];
  const artifactRoots = new Map();
  requireValue(existsSync(directory), 'Missing downloaded receipt artifacts');
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    requireValue(entry.isDirectory() && !entry.isSymbolicLink(), 'Invalid downloaded artifact layout');
    const root = resolve(directory, entry.name);
    const path = containedPath(root, 'receipt.json');
    const child = JSON.parse(readFileSync(path, 'utf8'));
    requireValue(child['artifact-name'] === entry.name, 'Wrong artifact identity');
    children.push(child);
    artifactRoots.set(child, root);
  }
  return { children, artifactRoots };
}

export function cliMain(env = process.env) {
  const input = name => env[`INPUT_${name.toUpperCase().replaceAll('-', '_')}`];
  const parse = (name, fallback) => JSON.parse(input(name) || JSON.stringify(fallback));
  const workspace = env.GITHUB_WORKSPACE || ROOT;
  let receipt;
  const options = { workflow: input('workflow'), job: env.GITHUB_JOB, shard: input('shard') || 'default',
    target: input('target') || input('shard') || 'default',
    runId: env.GITHUB_RUN_ID, runAttempt: env.GITHUB_RUN_ATTEMPT, sha: env.GITHUB_SHA,
    selected: input('selected') === 'true', selectionReason: input('selection-reason') || 'Selected by caller',
    workspace };
  try {
    requireValue(['true', 'false'].includes(input('selected') ?? 'true'), 'Invalid selected input');
    options.selected = input('selected') !== 'false';
    requireValue(['true', 'false'].includes(input('fail-on-error') ?? 'true'), 'Invalid fail-on-error input');
    const contract = JSON.parse(readFileSync(env.CI_CONTRACT_PATH || resolve(ROOT, 'scripts/ci/ci-contract.json'), 'utf8'));
    options.tools = parse('tools', ['node', 'git']);
    if (input('expected-shards')) {
      options.expectedShards = parse('expected-shards', {});
      options.expectedTargets = parse('expected-targets', {});
      options.needs = parse('needs-json', {});
      const directory = resolve(workspace, '.ci-outcome-downloads');
      const artifacts = !existsSync(directory) && object(options.expectedShards) &&
        Object.values(options.expectedShards).every(shards => Array.isArray(shards) && shards.length === 0) ?
        { children: [], artifactRoots: new Map() } : readReceipts(directory);
      options.artifactRoots = artifacts.artifactRoots;
      receipt = aggregateOutcomes(options, contract, artifacts.children);
    } else {
      options.steps = parse('steps-json', {});
      options.requiredSteps = parse('required-steps', []);
      options.reports = parse('reports', []);
      receipt = recordOutcome(options, contract);
    }
  } catch (error) {
    receipt = initialReceipt(options, input('expected-shards') ? 'aggregate' : 'record');
    failure(receipt, 'input', error.message);
  }
  publishOutcome(receipt, { workspace, githubOutput: env.GITHUB_OUTPUT, githubSummary: env.GITHUB_STEP_SUMMARY });
  console.log(`CI execution ${receipt.workflow}/${receipt.shard}: ${receipt['work-status']}`);
  return receipt['work-status'] === 'failure' || receipt['work-status'] === 'cancelled' ? 1 : 0;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    process.exitCode = cliMain();
  } catch {
    console.error('Unable to publish CI execution evidence');
    process.exitCode = 1;
  }
}
