// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

import childProcess from 'node:child_process';
import fs, { appendFileSync, existsSync, lstatSync, mkdirSync, unlinkSync } from 'node:fs';
import { dirname, isAbsolute, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';

const revisionPattern = /^(?:[0-9a-f]{40}|[0-9a-f]{64})$/;
const scope = 'Full tested-revision history, including merge diffs; unrelated refs excluded';
const maxReportBytes = 64 * 1024 * 1024;
const isObject = value => value !== null && typeof value === 'object' && !Array.isArray(value);

class ScanError extends Error {}

function git(cwd, env, args) {
  const result = childProcess.spawnSync('git', ['-C', cwd, ...args], {
    env, encoding: 'utf8', timeout: 30_000, maxBuffer: 1024 * 1024,
  });
  if (result.error || result.signal || result.status !== 0) {
    throw new ScanError(`Git ${args[0]} failed; verify the checkout and complete tested history`);
  }
  return result.stdout.trim();
}

function prepareReport(cwd, reportPath, env) {
  const local = relative(cwd, reportPath);
  if (!local || isAbsolute(local) || local.split(sep).includes('..') || !local.endsWith('.sarif')) {
    throw new ScanError('The report must be a .sarif file inside the repository');
  }
  let current = cwd;
  for (const part of local.split(sep)) {
    current = resolve(current, part);
    if (lstatSync(current, { throwIfNoEntry: false })?.isSymbolicLink()) {
      throw new ScanError('Report paths must not contain symlinks or junctions');
    }
  }
  if (git(cwd, env, ['ls-files', '--', local])) {
    throw new ScanError('Refusing to overwrite a tracked report');
  }
  if (existsSync(reportPath)) {
    if (!lstatSync(reportPath).isFile()) throw new ScanError('Report destination is not an ordinary file');
    unlinkSync(reportPath);
  }
  mkdirSync(dirname(reportPath), { recursive: true });
}

function readReport(reportPath) {
  const flags = fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW ?? 0) | (fs.constants.O_NONBLOCK ?? 0);
  const fd = fs.openSync(reportPath, flags);
  try {
    const before = fs.fstatSync(fd);
    if (!before.isFile() || before.size > maxReportBytes) {
      throw new ScanError('Gitleaks report is unsafe or exceeds the 64 MiB limit');
    }
    const entry = fs.lstatSync(reportPath);
    if (entry.isSymbolicLink() || entry.dev !== before.dev || entry.ino !== before.ino) {
      throw new ScanError('Gitleaks report path does not identify the opened regular file');
    }
    // Read one extra byte to detect growth without exceeding the report-size budget.
    const buffer = Buffer.alloc(before.size + 1);
    let length = 0;
    while (length < buffer.length) {
      const count = fs.readSync(fd, buffer, length, buffer.length - length, length);
      if (count === 0) break;
      length += count;
    }
    const after = fs.fstatSync(fd);
    if (length !== before.size || after.size !== before.size
      || after.mtimeMs !== before.mtimeMs || after.ctimeMs !== before.ctimeMs) {
      throw new ScanError('Gitleaks report changed while being read');
    }
    return buffer.subarray(0, length).toString('utf8');
  } finally {
    fs.closeSync(fd);
  }
}

function reportFindingCount(reportPath) {
  let report;
  try {
    report = JSON.parse(readReport(reportPath));
  } catch (error) {
    if (error instanceof SyntaxError) throw new ScanError('Gitleaks report is not valid JSON');
    throw error;
  }
  if (!isObject(report) || report.version !== '2.1.0' || !Array.isArray(report.runs) || !report.runs.length) {
    throw new ScanError('Gitleaks report must contain SARIF 2.1.0 runs');
  }
  let count = 0;
  for (const run of report.runs) {
    if (!isObject(run) || run.tool?.driver?.name !== 'gitleaks' || !Array.isArray(run.results)
      || (run.invocations !== undefined && (!Array.isArray(run.invocations)
        || run.invocations.some(invocation => !isObject(invocation) || invocation.executionSuccessful === false)))) {
      throw new ScanError('Gitleaks report has invalid or unsuccessful scan results');
    }
    for (const result of run.results) {
      if (!isObject(result) || typeof result.ruleId !== 'string' || !result.ruleId
        || !isObject(result.message) || typeof result.message.text !== 'string') {
        throw new ScanError('Gitleaks report contains a malformed finding');
      }
      count++;
    }
  }
  return count;
}

export function scan({
  cwd = process.cwd(), expectedRevision, binary, reportPath = 'logs/gitleaks-results.sarif',
  softFail = false, env = process.env,
}) {
  let revision = '';
  let scannerExitCode = null;
  try {
    if (typeof expectedRevision !== 'string' || !revisionPattern.test(expectedRevision) || typeof softFail !== 'boolean') {
      throw new ScanError('An exact expected revision and boolean soft-fail are required');
    }
    cwd = resolve(cwd);
    const processEnv = { ...env, NODE_DISABLE_COMPILE_CACHE: '1', GIT_TERMINAL_PROMPT: '0' };
    if (resolve(git(cwd, processEnv, ['rev-parse', '--show-toplevel'])) !== cwd) {
      throw new ScanError('Run the scanner from the repository root');
    }
    revision = git(cwd, processEnv, ['rev-parse', '--verify', 'HEAD^{commit}']);
    if (revision !== expectedRevision) throw new ScanError('Checked-out HEAD differs from the expected revision');
    if (git(cwd, processEnv, ['rev-parse', '--is-shallow-repository']) !== 'false') {
      throw new ScanError('Full tested history is required; shallow checkout is not supported');
    }
    reportPath = resolve(cwd, reportPath);
    prepareReport(cwd, reportPath, processEnv);
    if (typeof binary !== 'string' || !isAbsolute(binary) || !existsSync(binary)) {
      throw new ScanError('GITLEAKS_BIN must name an existing absolute scanner executable');
    }
    const result = childProcess.spawnSync(binary, [
      'git', cwd,
      '--log-opts', `--full-history --diff-merges=first-parent ${revision}`,
      '--report-format', 'sarif', '--report-path', reportPath,
      '--redact', '--no-banner', '--log-level', 'error', '--timeout', '300',
    ], { cwd, env: processEnv, encoding: 'utf8', timeout: 330_000, maxBuffer: 4 * 1024 * 1024 });
    scannerExitCode = Number.isInteger(result.status) ? result.status : null;
    if (result.error || result.signal || ![0, 1].includes(scannerExitCode)) {
      throw new ScanError(`Gitleaks execution failed (exit ${scannerExitCode ?? 'unavailable'}); check scanner availability and timeout`);
    }
    const findings = reportFindingCount(reportPath);
    if ((scannerExitCode === 0) !== (findings === 0)) {
      throw new ScanError('Gitleaks exit code and report findings disagree');
    }
    return {
      status: findings ? 'findings' : 'clean', revision, scannerExitCode, findings,
      exitCode: findings && !softFail ? 1 : 0,
    };
  } catch (error) {
    if (!(error instanceof ScanError) && !(error instanceof Error && typeof error.code === 'string')) throw error;
    return {
      status: 'error', revision, scannerExitCode, exitCode: 2,
      error: error instanceof ScanError ? error.message : 'Scanner file operation failed; check report permissions and available storage',
    };
  }
}

export function renderSummary({ status, revision, scannerExitCode, outcome }) {
  const validRevision = typeof revision === 'string' && revisionPattern.test(revision);
  let state = 'error';
  if (!status && !revision && !scannerExitCode && (!outcome || outcome === 'skipped')) state = 'not-run';
  if (validRevision && status === 'clean' && scannerExitCode === '0' && outcome === 'success') state = 'clean';
  if (validRevision && status === 'findings' && scannerExitCode === '1'
    && ['success', 'failure'].includes(outcome)) state = 'findings';
  const label = {
    clean: 'No Secrets Found',
    findings: 'Secrets Detected',
    error: 'Scan failed or produced inconsistent results',
    'not-run': 'Scan not completed',
  }[state];
  return {
    status: state,
    markdown: [
      '## Gitleaks Secret Scan Results', '',
      '| Metric | Value |', '|--------|-------|',
      `| Status | ${label} |`,
      `| Revision | ${validRevision ? revision : 'Unavailable'} |`,
      `| Scope | ${scope} |`, '',
    ].join('\n'),
  };
}

export function main(args = process.argv.slice(2), env = process.env) {
  const { positionals, values } = parseArgs({
    args, allowPositionals: true, strict: true,
    options: {
      'expected-revision': { type: 'string' }, binary: { type: 'string' },
      report: { type: 'string' }, 'soft-fail': { type: 'string' },
    },
  });
  if (positionals.length !== 1 || !['scan', 'summary'].includes(positionals[0])) {
    throw new ScanError('Usage: gitleaks-scan.mjs scan [--expected-revision SHA --binary PATH] | summary');
  }
  if (positionals[0] === 'summary') {
    if (Object.keys(values).length) throw new ScanError('Summary accepts only scan outcome environment variables');
    const summary = renderSummary({
      status: env.SCAN_STATUS, revision: env.SCAN_REVISION,
      scannerExitCode: env.SCAN_EXIT_CODE, outcome: env.SCAN_OUTCOME,
    });
    if (env.GITHUB_STEP_SUMMARY) appendFileSync(env.GITHUB_STEP_SUMMARY, summary.markdown);
    console.log(summary.markdown);
    return summary.status === 'error' ? 2 : 0;
  }
  const softFail = values['soft-fail'] ?? env.SOFT_FAIL ?? 'false';
  if (!['true', 'false'].includes(softFail)) throw new ScanError('soft-fail must be true or false');
  if (env.GITHUB_ACTIONS === 'true' && values['expected-revision'] !== undefined
    && values['expected-revision'] !== env.GITHUB_SHA) {
    throw new ScanError('CI expected revision must match GITHUB_SHA');
  }
  const result = scan({
    expectedRevision: values['expected-revision'] ?? env.GITHUB_SHA,
    binary: values.binary ?? env.GITLEAKS_BIN,
    reportPath: values.report, softFail: softFail === 'true', env,
  });
  if (env.GITHUB_OUTPUT) {
    appendFileSync(env.GITHUB_OUTPUT, [
      `scan-status=${result.status}`, `scan-revision=${result.revision}`,
      `scan-exit-code=${result.scannerExitCode ?? 'unavailable'}`, '',
    ].join('\n'));
  }
  console.log(JSON.stringify({ ...result, scope }));
  if (result.error) console.error(result.error);
  return result.exitCode;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    process.exitCode = main();
  } catch (error) {
    if (!(error instanceof ScanError) && !(error instanceof Error && typeof error.code === 'string')) throw error;
    console.error(error instanceof ScanError ? error.message : 'Scanner command failed; check arguments and output-file permissions');
    process.exitCode = 2;
  }
}
