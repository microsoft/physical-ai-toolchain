// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

import assert from 'node:assert/strict';
import childProcess, { spawnSync } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import fs, { existsSync, mkdirSync, mkdtempSync, readFileSync, renameSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { syncBuiltinESMExports } from 'node:module';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { before, test } from 'node:test';
import { renderSummary, scan } from '../../security/gitleaks-scan.mjs';

const binary = process.env.GITLEAKS_BIN;
const helper = fileURLToPath(new URL('../../security/gitleaks-scan.mjs', import.meta.url));
const cleanReport = { version: '2.1.0', runs: [{ tool: { driver: { name: 'gitleaks' } }, results: [] }] };
const findingReport = {
  version: '2.1.0',
  runs: [{ tool: { driver: { name: 'gitleaks' } }, results: [{ ruleId: 'generic-api-key', message: { text: 'Redacted fixture' } }] }],
};

before(() => {
  assert.ok(binary && existsSync(binary), 'GITLEAKS_BIN must name the checksum-verified Gitleaks 8.30.0 executable');
  const result = spawnSync(binary, ['version'], { encoding: 'utf8', timeout: 30_000 });
  assert.ifError(result.error);
  assert.equal(result.status, 0);
  assert.equal(result.stdout.trim(), '8.30.0', 'Tests must exercise the workflow-pinned scanner');
});

function repository(t) {
  const cwd = mkdtempSync(join(tmpdir(), 'gitleaks-scope-'));
  t.after(() => rmSync(cwd, { recursive: true, force: true, maxRetries: 8, retryDelay: 100 }));
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) =>
    !/^(GIT_|GITLEAKS_|GITHUB_|SOFT_FAIL$)/i.test(key)));
  Object.assign(env, {
    GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: join(cwd, 'unused-config'),
    GIT_AUTHOR_NAME: 'Scanner Test', GIT_AUTHOR_EMAIL: 'scanner@example.invalid',
    GIT_COMMITTER_NAME: 'Scanner Test', GIT_COMMITTER_EMAIL: 'scanner@example.invalid',
    GIT_TERMINAL_PROMPT: '0', NODE_DISABLE_COMPILE_CACHE: '1',
  });
  const git = (...args) => {
    const result = spawnSync('git', [
      '-c', 'core.autocrlf=false', '-c', `core.hooksPath=${join(cwd, 'unused-hooks')}`, ...args,
    ], { cwd, env, encoding: 'utf8', timeout: 30_000 });
    assert.ifError(result.error);
    assert.equal(result.status, 0, `Git fixture operation failed: ${args[0]}`);
    return result.stdout.trim();
  };
  const write = (path, text) => {
    mkdirSync(dirname(join(cwd, path)), { recursive: true });
    writeFileSync(join(cwd, path), text);
  };
  const commit = () => {
    git('add', '--all');
    git('commit', '--quiet', '--no-gpg-sign', '--allow-empty', '-m', 'Synthetic scanner fixture');
    return git('rev-parse', 'HEAD');
  };
  git('init', '--quiet', '--initial-branch=main', '--object-format=sha1', '--template=');
  write('readme.txt', 'A clean test repository.\n');
  const base = commit();
  return { cwd, env, git, write, commit, base, reportPath: join(cwd, 'logs', 'scan.sarif') };
}

function syntheticFinding() {
  return `api_${'key'} = "${randomBytes(32).toString('hex')}"\n`;
}

function runScan(repo, options = {}) {
  return scan({ ...repo, binary, expectedRevision: repo.git('rev-parse', 'HEAD'), ...options });
}

test('scope excludes unrelated branches, remote refs and tags without suppressions', t => {
  const repo = repository(t);
  repo.git('switch', '--quiet', '-c', 'unrelated');
  repo.write('candidate.txt', syntheticFinding());
  const unrelated = repo.commit();
  assert.equal(runScan(repo).status, 'findings', 'Positive control must be detected');
  repo.git('update-ref', 'refs/remotes/origin/unrelated', unrelated);
  repo.git('tag', 'unrelated-tag');
  repo.git('switch', '--quiet', 'main');
  const result = runScan(repo);
  assert.equal(result.status, 'clean');
  assert.equal(result.revision, repo.base);
  assert.equal(result.exitCode, 0);
  assert.ok(repo.git('show-ref').includes(unrelated), 'Unrelated refs must not be deleted');
});

for (const operation of ['present', 'deleted', 'renamed', 'second-parent', 'merge-only']) {
  test(`history still detects a synthetic finding: ${operation}`, t => {
    const repo = repository(t);
    const candidate = syntheticFinding();
    if (operation === 'second-parent' || operation === 'merge-only') {
      repo.git('switch', '--quiet', '-c', 'feature');
      repo.write('feature.txt', operation === 'second-parent' ? candidate : 'Feature.\n');
      repo.commit();
      repo.git('switch', '--quiet', 'main');
      repo.write('main.txt', 'Main.\n');
      repo.commit();
      repo.git('merge', '--no-ff', '--no-commit', 'feature');
      if (operation === 'merge-only') repo.write('merge-only.txt', candidate);
      repo.commit();
      repo.git('switch', '--detach', '--quiet', 'HEAD');
    } else {
      repo.write('candidate.txt', candidate);
      repo.commit();
      if (operation === 'deleted') {
        repo.git('rm', '--quiet', 'candidate.txt');
        repo.commit();
      } else if (operation === 'renamed') {
        repo.git('mv', 'candidate.txt', 'renamed.txt');
        repo.commit();
      }
    }
    const result = runScan(repo);
    assert.equal(result.status, 'findings');
    assert.equal(result.exitCode, 1);
    assert.equal(result.scannerExitCode, 1);
    assert.ok(result.findings > 0);
    assert.ok(!JSON.stringify(result).includes(candidate), 'Result must not echo fixture contents');
  });
}

test('soft-fail affects detected findings, not their reported state', t => {
  const repo = repository(t);
  repo.write('candidate.txt', syntheticFinding());
  repo.commit();
  const result = runScan(repo, { softFail: true });
  assert.equal(result.status, 'findings');
  assert.equal(result.exitCode, 0);
  assert.match(renderSummary({ status: result.status, revision: result.revision, scannerExitCode: '1', outcome: 'success' }).markdown,
    /Secrets Detected/);
});

for (const expectedRevision of ['', 'main', '0'.repeat(40), 'abc\ninjected']) {
  test(`identity rejects invalid or mismatched revision: ${JSON.stringify(expectedRevision)}`, t => {
    const repo = repository(t);
    const result = runScan(repo, { expectedRevision });
    assert.equal(result.status, 'error');
    assert.notEqual(result.exitCode, 0);
    assert.equal(result.scannerExitCode, null);
    assert.ok(!existsSync(repo.reportPath));
  });
}

test('missing scanner and stale report cannot produce a clean scan', t => {
  const repo = repository(t);
  repo.write('logs/scan.sarif', JSON.stringify(cleanReport));
  const result = runScan(repo, { binary: join(repo.cwd, 'missing-scanner'), softFail: true });
  assert.equal(result.status, 'error');
  assert.notEqual(result.exitCode, 0);
  assert.ok(!existsSync(repo.reportPath));
});

function scannerReport(t, repo, report = cleanReport) {
  const originalSpawn = childProcess.spawnSync;
  t.mock.method(childProcess, 'spawnSync', (command, args, options) => {
    if (command !== binary) return originalSpawn(command, args, options);
    repo.write('logs/scan.sarif', typeof report === 'string' ? report : JSON.stringify(report));
    return { status: 0, stdout: '', stderr: '' };
  });
}

test('report replacement after validation cannot redirect the read to a different file', t => {
  const repo = repository(t);
  scannerReport(t, repo);
  const originalLstat = fs.lstatSync;
  const originalRead = fs.readSync;
  const reads = [];
  let replaced = false;
  t.mock.method(fs, 'lstatSync', (path, options) => {
    const stat = originalLstat(path, options);
    if (path === repo.reportPath && stat?.isFile() && !replaced) {
      replaced = true;
      renameSync(repo.reportPath, `${repo.reportPath}.original`);
      repo.write('logs/scan.sarif', JSON.stringify(findingReport));
    }
    return stat;
  });
  t.mock.method(fs, 'readSync', (fd, buffer, offset, length, position) => {
    const count = originalRead(fd, buffer, offset, length, position);
    reads.push(Buffer.from(buffer.subarray(offset, offset + count)));
    return count;
  });
  syncBuiltinESMExports();
  t.after(() => { t.mock.restoreAll(); syncBuiltinESMExports(); });
  const result = runScan(repo);
  assert.equal(replaced, true, 'Exercise the actual report-check interleaving');
  assert.ok(['clean', 'error'].includes(result.status));
  assert.equal(Buffer.concat(reads).toString('utf8'), JSON.stringify(cleanReport),
    'Consume the validated original handle, not the replacement path');
});

test('report data is read only through a validated descriptor and every descriptor closes', t => {
  const repo = repository(t);
  scannerReport(t, repo);
  const originalReadFile = fs.readFileSync;
  const originalRead = fs.readSync;
  const originalClose = fs.closeSync;
  const consumed = new Set();
  const closed = new Set();
  t.mock.method(fs, 'readFileSync', (path, ...args) => {
    assert.notEqual(path, repo.reportPath, 'Do not reopen the checked report pathname');
    return originalReadFile(path, ...args);
  });
  t.mock.method(fs, 'readSync', (fd, ...args) => {
    assert.equal(typeof fd, 'number');
    consumed.add(fd);
    return originalRead(fd, ...args);
  });
  t.mock.method(fs, 'closeSync', fd => { closed.add(fd); return originalClose(fd); });
  syncBuiltinESMExports();
  t.after(() => { t.mock.restoreAll(); syncBuiltinESMExports(); });
  assert.equal(runScan(repo).status, 'clean');
  assert.equal(consumed.size, 1);
  assert.deepEqual(closed, consumed);
});

for (const mutation of ['growth', 'truncation', 'parse-error', 'read-error', 'stat-error', 'oversize', 'non-regular']) {
  test(`report descriptor rejects ${mutation} and closes on failure`, t => {
    const repo = repository(t);
    scannerReport(t, repo, mutation === 'parse-error' ? '{' : cleanReport);
    const originalFstat = fs.fstatSync;
    const originalRead = fs.readSync;
    const originalClose = fs.closeSync;
    const inspected = new Set();
    const closed = new Set();
    let changed = false;
    t.mock.method(fs, 'fstatSync', fd => {
      const stat = originalFstat(fd);
      inspected.add(fd);
      if (mutation === 'stat-error') throw Object.assign(new Error('synthetic private detail'), { code: 'EIO' });
      if (!changed) {
        changed = true;
        if (mutation === 'growth') fs.appendFileSync(repo.reportPath, 'extra');
        if (mutation === 'truncation') fs.truncateSync(repo.reportPath, 1);
        if (mutation === 'oversize') return { ...stat, isFile: () => true, size: 64 * 1024 * 1024 + 1 };
        if (mutation === 'non-regular') return { ...stat, isFile: () => false };
      }
      return stat;
    });
    t.mock.method(fs, 'readSync', (fd, ...args) => {
      if (mutation === 'read-error') throw Object.assign(new Error('synthetic private detail'), { code: 'EIO' });
      return originalRead(fd, ...args);
    });
    t.mock.method(fs, 'closeSync', fd => { closed.add(fd); return originalClose(fd); });
    const result = runScan(repo);
    assert.equal(changed || mutation === 'stat-error', true, 'Descriptor metadata must be inspected');
    assert.equal(result.status, 'error');
    assert.notEqual(result.exitCode, 0);
    assert.equal(inspected.size, 1);
    for (const fd of inspected) assert.ok(closed.has(fd), 'Close the inspected report descriptor');
    assert.ok(!JSON.stringify(result).includes('private detail'));
  });
}

for (const mismatch of ['symlink', 'identity']) {
  test(`report rejects ${mismatch} after opening without reading its contents`, t => {
    const repo = repository(t);
    scannerReport(t, repo);
    const originalLstat = fs.lstatSync;
    const originalClose = fs.closeSync;
    let closed = false;
    t.mock.method(fs, 'lstatSync', (path, options) => {
      const stat = originalLstat(path, options);
      if (path !== repo.reportPath || !stat?.isFile()) return stat;
      return { ...stat, isSymbolicLink: () => mismatch === 'symlink',
        ino: mismatch === 'identity' ? stat.ino + 1 : stat.ino };
    });
    t.mock.method(fs, 'readSync', () => assert.fail('Unsafe report must not be read'));
    t.mock.method(fs, 'closeSync', fd => { closed = true; return originalClose(fd); });
    const result = runScan(repo);
    assert.equal(result.status, 'error');
    assert.match(result.error, /opened regular file/);
    assert.equal(closed, true);
  });
}

test('report safety rejects tracked files and escaping destinations', t => {
  const repo = repository(t);
  repo.write('tracked.sarif', 'Tracked content must not be removed.\n');
  repo.commit();
  for (const reportPath of ['tracked.sarif', '../outside.sarif', '.', 'not-a-report.txt']) {
    const result = runScan(repo, { reportPath });
    assert.equal(result.status, 'error');
    assert.notEqual(result.exitCode, 0);
  }
  assert.equal(readFileSync(join(repo.cwd, 'tracked.sarif'), 'utf8'), 'Tracked content must not be removed.\n');
});

test('report safety rejects symlinked directories and non-file destinations', t => {
  const repo = repository(t);
  const target = join(repo.cwd, 'target');
  mkdirSync(target);
  symlinkSync(target, join(repo.cwd, 'linked'), process.platform === 'win32' ? 'junction' : 'dir');
  assert.equal(runScan(repo, { reportPath: 'linked/result.sarif' }).status, 'error');
  assert.ok(!existsSync(join(target, 'result.sarif')));
  mkdirSync(join(repo.cwd, 'directory.sarif'));
  assert.equal(runScan(repo, { reportPath: 'directory.sarif' }).status, 'error');
});

test('scanner rejects a nested working directory and shallow history', t => {
  const repo = repository(t);
  mkdirSync(join(repo.cwd, 'nested'));
  assert.equal(runScan(repo, { cwd: join(repo.cwd, 'nested') }).status, 'error');
  writeFileSync(join(repo.cwd, '.git', 'shallow'), `${repo.base}\n`);
  const result = runScan(repo);
  assert.equal(result.status, 'error');
  assert.match(result.error, /shallow/);
});

test('Git execution failures are explicit and redacted', t => {
  const repo = repository(t);
  const expectedRevision = repo.base;
  t.mock.method(childProcess, 'spawnSync', () => ({ status: 128, stderr: 'sensitive diagnostic' }));
  const result = scan({ ...repo, binary, expectedRevision });
  assert.equal(result.status, 'error');
  assert.match(result.error, /Git/);
  assert.ok(!JSON.stringify(result).includes('sensitive'));
});

const failures = [
  { name: 'missing report', status: 0 },
  { name: 'invalid JSON', status: 0, report: '{' },
  { name: 'invalid SARIF', status: 0, report: '{}' },
  { name: 'missing results', status: 0, report: JSON.stringify({ version: '2.1.0', runs: [{}] }) },
  { name: 'invalid invocation list', status: 0,
    report: JSON.stringify({ ...cleanReport, runs: [{ ...cleanReport.runs[0], invocations: {} }] }) },
  { name: 'unsuccessful invocation', status: 0,
    report: JSON.stringify({ ...cleanReport, runs: [{ ...cleanReport.runs[0], invocations: [{ executionSuccessful: false }] }] }) },
  { name: 'empty findings result', status: 1, report: JSON.stringify(cleanReport) },
  { name: 'findings with success exit', status: 0, report: JSON.stringify(findingReport) },
  { name: 'scanner error', status: 2, report: JSON.stringify(cleanReport) },
  { name: 'scanner timeout', status: null, error: { code: 'ETIMEDOUT' } },
];
for (const fixture of failures) {
  test(`execution fails closed: ${fixture.name}`, t => {
    const repo = repository(t);
    const originalSpawn = childProcess.spawnSync;
    t.mock.method(childProcess, 'spawnSync', (command, args, options) => {
      if (command !== binary) return originalSpawn(command, args, options);
      if (fixture.report !== undefined) repo.write('logs/scan.sarif', fixture.report);
      return { ...fixture, stdout: 'sensitive external output', stderr: 'sensitive external error' };
    });
    const result = runScan(repo, { softFail: true });
    assert.equal(result.status, 'error');
    assert.notEqual(result.exitCode, 0);
    assert.ok(!JSON.stringify(result).includes('sensitive'));
  });
}

test('actual scanner arguments bind merge history, redaction and the current revision', t => {
  const repo = repository(t);
  const originalSpawn = childProcess.spawnSync;
  t.mock.method(childProcess, 'spawnSync', (command, args, options) => {
    if (command === binary) {
      assert.equal(args[args.indexOf('--log-opts') + 1],
        `--full-history --diff-merges=first-parent ${repo.base}`);
      assert.ok(args.includes('--redact'));
      assert.ok(!args.includes('--all'));
      assert.ok(!args.includes('--first-parent'));
      assert.ok(options.timeout > 0 && options.maxBuffer > 0);
    }
    return originalSpawn(command, args, options);
  });
  assert.equal(runScan(repo).status, 'clean');
});

const revision = 'a'.repeat(40);
for (const fixture of [
  { status: 'clean', scannerExitCode: '0', outcome: 'success', expected: 'clean' },
  { status: 'findings', scannerExitCode: '1', outcome: 'failure', expected: 'findings' },
  { status: 'findings', scannerExitCode: '1', outcome: 'success', expected: 'findings' },
  { status: 'error', scannerExitCode: '2', outcome: 'failure', expected: 'error' },
  { status: '', scannerExitCode: '', outcome: 'skipped', expected: 'not-run' },
  { status: '', scannerExitCode: '', outcome: 'failure', expected: 'error' },
  { status: '', scannerExitCode: '', outcome: 'success', expected: 'error' },
  { status: 'clean', scannerExitCode: '1', outcome: 'success', expected: 'error' },
  { status: 'findings', scannerExitCode: '0', outcome: 'success', expected: 'error' },
  { status: 'clean', scannerExitCode: '0', outcome: 'cancelled', expected: 'error' },
  { status: 'clean', scannerExitCode: '0', outcome: 'failure', expected: 'error' },
]) {
  test(`summary ${JSON.stringify(fixture)} cannot invent clean execution`, () => {
    const result = renderSummary({ ...fixture, revision: fixture.status ? revision : '' });
    assert.equal(result.status, fixture.expected);
    assert.equal(result.markdown.includes('No Secrets Found'), fixture.expected === 'clean');
  });
}

test('summary rejects output injection and malformed identity', () => {
  const result = renderSummary({ status: 'clean', scannerExitCode: '0', outcome: 'success', revision: 'abc\n| injected |' });
  assert.equal(result.status, 'error');
  assert.ok(!result.markdown.includes('injected'));
});

test('CLI emits revision-bound outputs and writes an outcome-aware summary', t => {
  const repo = repository(t);
  const output = join(repo.cwd, 'outputs.txt');
  const summary = join(repo.cwd, 'summary.md');
  const result = spawnSync(process.execPath, [helper, 'scan'], {
    cwd: repo.cwd, encoding: 'utf8', timeout: 120_000,
    env: { ...repo.env, GITLEAKS_BIN: binary, GITHUB_SHA: repo.base, GITHUB_OUTPUT: output },
  });
  assert.ifError(result.error);
  assert.equal(result.status, 0, result.stderr);
  assert.match(readFileSync(output, 'utf8'), /scan-status=clean/);
  assert.ok(readFileSync(output, 'utf8').includes(`scan-revision=${repo.base}`));
  assert.match(readFileSync(output, 'utf8'), /scan-exit-code=0/);
  const rendered = spawnSync(process.execPath, [helper, 'summary'], {
    cwd: repo.cwd, encoding: 'utf8', timeout: 30_000,
    env: { ...repo.env, GITHUB_STEP_SUMMARY: summary, SCAN_STATUS: 'clean',
      SCAN_REVISION: repo.base, SCAN_EXIT_CODE: '0', SCAN_OUTCOME: 'success' },
  });
  assert.equal(rendered.status, 0, rendered.stderr);
  assert.match(readFileSync(summary, 'utf8'), /No Secrets Found/);
});

test('CLI rejects unrecognized options rather than accepting arbitrary log options', t => {
  const repo = repository(t);
  const result = spawnSync(process.execPath, [helper, 'scan', '--log-opts=--all'], {
    cwd: repo.cwd, env: repo.env, encoding: 'utf8', timeout: 30_000,
  });
  assert.notEqual(result.status, 0);
  assert.ok(!existsSync(join(repo.cwd, 'logs', 'gitleaks-results.sarif')));
});

for (const args of [[], ['unknown'], ['scan', '--soft-fail=maybe'], ['summary', '--report=x.sarif']]) {
  test(`CLI rejects invalid arguments: ${JSON.stringify(args)}`, t => {
    const repo = repository(t);
    const result = spawnSync(process.execPath, [helper, ...args], {
      cwd: repo.cwd, env: repo.env, encoding: 'utf8', timeout: 30_000,
    });
    assert.notEqual(result.status, 0);
    assert.ok(!existsSync(join(repo.cwd, 'logs')));
  });
}

test('CI cannot override the workflow tested revision', t => {
  const repo = repository(t);
  const result = spawnSync(process.execPath, [helper, 'scan', '--expected-revision', repo.base], {
    cwd: repo.cwd, encoding: 'utf8', timeout: 30_000,
    env: { ...repo.env, GITHUB_ACTIONS: 'true', GITHUB_SHA: '0'.repeat(40), GITLEAKS_BIN: binary },
  });
  assert.notEqual(result.status, 0);
  assert.ok(!existsSync(join(repo.cwd, 'logs')));
});
