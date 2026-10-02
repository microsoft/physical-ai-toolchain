// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { parseDocument } from 'yaml';
import {
  aggregateOutcomes, inspectReport, outcomeOutputs, parseTestReport,
  probeTools, publishOutcome, readReceipts, recordOutcome,
} from '../../ci/publish-outcome.mjs';

const root = fileURLToPath(new URL('../../../', import.meta.url));
const helper = join(root, 'scripts', 'ci', 'publish-outcome.mjs');
const sha = 'a'.repeat(40);
const junit = '<testsuites tests="2" failures="0" errors="0" skipped="1"><testsuite name="suite" tests="2" failures="0" errors="0" skipped="1"><testcase classname="Example" name="passes"/><testcase classname="Example" name="skips"><skipped/></testcase></testsuite></testsuites>';
const passingXml = '<testsuite name="one" tests="1"><testcase name="passes"/></testsuite>';
const contract = { execution: { checks: { jobs: {
  test: { 'required-steps': ['setup', 'test'], reports: [{ path: 'results/{shard}.xml', kind: 'junit' }] },
} } } };

function fixture(t) {
  const directory = mkdtempSync(join(root, '.ci-evidence-fixture-'));
  t.after(() => rmSync(directory, { recursive: true, force: true, maxRetries: 5, retryDelay: 50 }));
  return directory;
}

function write(directory, path, value) {
  const destination = join(directory, path);
  mkdirSync(dirname(destination), { recursive: true });
  writeFileSync(destination, typeof value === 'string' ? value : JSON.stringify(value));
  return destination;
}

function options(workspace, overrides = {}) {
  return { workflow: 'checks', job: 'test', shard: 'linux', target: 'linux',
    runId: '123', runAttempt: '1', sha, selected: true, selectionReason: 'Caller selected checks',
    requiredSteps: ['setup', 'test'], reports: [{ path: 'results/linux.xml', kind: 'junit' }],
    steps: { setup: { outcome: 'success', conclusion: 'success' }, test: { outcome: 'success', conclusion: 'success' } },
    workspace, tools: [], ...overrides };
}

function childFixture(t, customContract = contract, overrides = {}) {
  const workspace = fixture(t);
  write(workspace, 'results/linux.xml', junit);
  const input = options(workspace, overrides);
  const child = recordOutcome(input, customContract);
  publishOutcome(child, { workspace, outputDirectory: `.ci-outcome-downloads/${child['artifact-name']}` });
  const { children, artifactRoots } = readReceipts(join(workspace, '.ci-outcome-downloads'));
  const aggregate = { ...input, job: 'aggregate', shard: 'aggregate', target: 'aggregate',
    expectedShards: { test: ['linux'] }, needs: { test: { result: 'success' } }, artifactRoots };
  return { workspace, child: children[0], children, aggregate };
}

test('JUnit: nested suites count emitted leaf cases without double counting', () => {
  assert.deepEqual(parseTestReport(junit, 'junit'), { total: 2, executed: 1, skipped: 1, failed: 0, errors: 0 });
  const nested = '<?xml version="1.0"?><testsuite name="outer" tests="2"><!-- report --><testsuite name="inner"><testcase name="x &amp; y"/><testcase name="z"><failure><![CDATA[details <here>]]></failure></testcase></testsuite></testsuite>';
  assert.equal(parseTestReport(nested, 'junit').failed, 1);
});

test('JUnit: errors and failures are separate reported counts', () => {
  const xml = '<testsuite tests="2" errors="1" failures="1"><testcase name="error"><error/></testcase><testcase name="failure"><failure/></testcase></testsuite>';
  assert.deepEqual(parseTestReport(xml, 'junit'), { total: 2, executed: 2, skipped: 0, failed: 2, errors: 1 });
});

test('JUnit: malformed, unsafe, empty, and contradictory reports are rejected', () => {
  const invalid = [
    '<testsuites tests="2"/>', '<testsuite><testcase name="a"><skipped/></testcase></testsuite>',
    '<testsuite tests="9007199254740992"><testcase name="a"/></testsuite>',
    '<testsuite tests="-1"><testcase name="a"/></testsuite>',
    '<testsuite tests="1.5"><testcase name="a"/></testsuite>',
    '<testsuite tests="2"><testcase name="a"/></testsuite>',
    '<testsuite><testcase name="a"><failure/><skipped/></testcase></testsuite>',
    '<testsuite><testcase/></testsuite>', '<testsuite><testcase name="a"></testsuite>',
    '<!DOCTYPE testsuite [<!ENTITY local SYSTEM "file:///etc/passwd">]><testsuite/>',
    '<testsuite><testcase name="&unknown;"/></testsuite>',
    '<testsuite><testcase name="&#0;"/></testsuite>',
    '<testsuite><testcase name="a" name="b"/></testsuite>',
    '<other><testcase name="a"/></other>', '<testsuite><testcase name="a"/></testsuite>trailing',
    '<testsuite><testcase name="a"/></testsuite><testsuite/>',
    '<testsuite><testcase name="a"/></testsuite><', '<testsuite><testcase name=a/></testsuite>',
    '<![CDATA[ignored]]><testsuite><testcase name="a"/></testsuite>',
    '<testsuite><testcase name="parent"><testcase name="hidden"><failure/></testcase></testcase></testsuite>',
  ];
  for (const xml of invalid) assert.throws(() => parseTestReport(xml, 'junit'), undefined, xml);
});

test('JUnit: XML escaped and numeric testcase identities are decoded', () => {
  const xml = '<testsuite><testcase name="&#65;"/><testcase name="&#x42; &quot;x&quot; &apos;y&apos; &lt;z&gt; >"/></testsuite>';
  assert.equal(parseTestReport(xml, 'junit').executed, 2);
  assert.equal(parseTestReport('<testsuite><testcase name="A"/><testcase name="&#65;"/></testsuite>', 'junit').executed, 2);
});

test('JUnit: parameterized duplicate display names retain every emitted testcase occurrence', t => {
  const workspace = fixture(t);
  const repeated = '<testcase classname="export-contract.test.ts" name="preserves the public export contract"/>';
  const xml = `<testsuites tests="2" failures="0"><testsuite name="exports" tests="2">${repeated}${repeated}</testsuite></testsuites>`;
  write(workspace, 'results/linux.xml', xml);
  const receipt = recordOutcome(options(workspace), contract);
  assert.equal(receipt['work-status'], 'success');
  assert.equal(outcomeOutputs(receipt)['test-count'], '2');
  const mixed = '<testsuite name="exports" tests="3" failures="1" skipped="1">' +
    repeated +
    '<testcase classname="export-contract.test.ts" name="preserves the public export contract"><failure/></testcase>' +
    '<testcase classname="export-contract.test.ts" name="preserves the public export contract"><skipped/></testcase>' +
    '</testsuite>';
  assert.deepEqual(parseTestReport(mixed, 'junit'), { total: 3, executed: 2, skipped: 1, failed: 1, errors: 0 });
  write(workspace, 'results/linux.xml', mixed);
  assert.equal(recordOutcome(options(workspace), contract)['work-status'], 'failure');
});

test('JUnit: native Node runner nested reports retain executed and skipped testcase counts', t => {
  const workspace = fixture(t);
  const suite = write(workspace, 'native.test.cjs', `
    const { test, describe } = require('node:test');
    describe('suite', () => {
      test('passes', () => {});
      test.skip('skips', () => {});
    });
  `);
  const result = spawnSync(process.execPath, ['--test', '--test-reporter=junit', suite],
    { cwd: workspace, encoding: 'utf8', timeout: 30_000,
      env: Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith('NODE_TEST_'))) });
  assert.equal(result.status, 0, result.stderr);
  const counts = parseTestReport(result.stdout, 'junit');
  assert.equal(counts.executed, 1);
  assert.equal(counts.skipped, 1);
});

test('NUnit: version 3 counts actual nested cases including skipped cases', () => {
  const xml = '<test-run total="3" passed="1" failed="1" skipped="1"><test-suite name="outer"><test-suite name="inner"><test-case id="1" name="ok" result="Passed"/><test-case id="2" name="bad" result="Failed"/><test-case id="3" name="skip" result="Skipped"/></test-suite></test-suite></test-run>';
  assert.deepEqual(parseTestReport(xml, 'nunit'), { total: 3, executed: 2, skipped: 1, failed: 1, errors: 0 });
});

test('NUnit: version 2 excludes ignored and not-run cases', () => {
  const xml = '<test-results total="3" failures="1" errors="0" not-run="1"><test-suite name="suite"><results><test-case name="ok" executed="True" result="Success"/><test-case name="bad" executed="True" result="Failure"/><test-case name="skip" executed="False" result="Ignored"/></results></test-suite></test-results>';
  assert.equal(parseTestReport(xml, 'nunit').executed, 2);
});

test('NUnit: repeated parameterized display names preserve every outcome and declared total', t => {
  const workspace = fixture(t);
  const reports = [{ path: 'results/linux.xml', kind: 'nunit' }];
  const nunitContract = structuredClone(contract);
  nunitContract.execution.checks.jobs.test.reports = reports;
  const input = options(workspace, { reports });
  const passed = '<test-case name="suite.check()" executed="True" result="Success"/>';
  const skipped = '<test-case name="suite.check()" executed="False" result="Ignored"/>';
  const failed = '<test-case name="suite.check()" executed="True" result="Failure"><failure/></test-case>';
  const report = (cases, failures = 0) => `<test-results total="4" failures="${failures}" errors="0" skipped="1">` +
    `<test-suite type="ParameterizedTest" name="suite.check"><results>${cases}</results></test-suite></test-results>`;
  const xml = report(passed.repeat(3) + skipped);
  assert.deepEqual(parseTestReport(xml, 'nunit'), { total: 4, executed: 3, skipped: 1, failed: 0, errors: 0 });
  write(workspace, reports[0].path, xml);
  const receipt = recordOutcome(input, nunitContract);
  assert.equal(receipt['work-status'], 'success');
  assert.equal(outcomeOutputs(receipt)['test-count'], '3');
  assert.equal(outcomeOutputs(receipt)['skipped-count'], '1');
  const mixed = report(passed.repeat(2) + failed + skipped, 1);
  assert.deepEqual(parseTestReport(mixed, 'nunit'), { total: 4, executed: 3, skipped: 1, failed: 1, errors: 0 });
  write(workspace, reports[0].path, mixed);
  assert.equal(recordOutcome(input, nunitContract)['work-status'], 'failure');
  assert.throws(() => parseTestReport(xml.replace('total="4"', 'total="5"'), 'nunit'), /Contradictory/);
  assert.throws(() => parseTestReport(mixed.replace('failures="1"', 'failures="0"'), 'nunit'), /Contradictory/);
});

test('NUnit: native Pester report excludes filtered cases without double-counting explicit skips', t => {
  const available = spawnSync('pwsh', ['-NoProfile', '-NonInteractive', '-Command',
    "if (-not (Get-Module -ListAvailable Pester | Where-Object { $_.Version.Major -ge 5 })) { exit 2 }"],
  { encoding: 'utf8', timeout: 30_000 });
  if (available.error?.code === 'ENOENT' || available.status === 2) {
    t.skip('Pester 5 is not installed; parser fixtures remain covered');
    return;
  }
  const workspace = fixture(t);
  const script = `
    Import-Module Pester -MinimumVersion 5.0
    $config = New-PesterConfiguration
    $config.Run.Container = @(New-PesterContainer -ScriptBlock {
      Describe 'Evidence' {
        It 'passes' { 1 | Should -Be 1 }
        It 'parameterized' -ForEach @('one', 'two', 'three', 'four') { $_ | Should -Not -BeNullOrEmpty }
        It 'skips' -Skip { 1 | Should -Be 2 }
        It 'excluded' -Tag 'Integration' { 1 | Should -Be 2 }
        It 'also excluded' -Tag 'Integration' { 1 | Should -Be 2 }
      }
    })
    $config.TestResult.Enabled = $true
    $config.TestResult.OutputPath = 'results.xml'
    $config.TestResult.OutputFormat = 'NUnitXml'
    $config.Filter.ExcludeTag = @('Integration')
    $config.Run.Exit = $true
    Invoke-Pester -Configuration $config
  `;
  const result = spawnSync('pwsh', ['-NoProfile', '-NonInteractive', '-Command', script],
    { cwd: workspace, encoding: 'utf8', timeout: 30_000 });
  assert.equal(result.status, 0, result.stderr);
  const xml = readFileSync(join(workspace, 'results.xml'), 'utf8').replace(/^\uFEFF/, '');
  let counts;
  assert.doesNotThrow(() => { counts = parseTestReport(xml, 'nunit'); }, xml);
  assert.equal(counts.total, 6);
  assert.equal(counts.executed, 5);
  assert.equal(counts.skipped, 1);
  assert.match(xml, /not-run="2"/);
  assert.match(xml, /<test-results\b[^>]*total="6"[^>]*skipped="1"/);
  assert.equal((xml.match(/<test-case\b/g) ?? []).length, 6);
  assert.equal((xml.match(/name="Evidence\.parameterized\(\)"/g) ?? []).length, 4);
  const nunitContract = structuredClone(contract);
  const reports = [{ path: 'results.xml', kind: 'nunit' }];
  nunitContract.execution.checks.jobs.test.reports = reports;
  const input = options(workspace, { reports });
  const receipt = recordOutcome(input, nunitContract);
  assert.equal(receipt['work-status'], 'success');
  assert.equal(outcomeOutputs(receipt)['test-count'], '5');
  assert.equal(outcomeOutputs(receipt)['skipped-count'], '1');
  publishOutcome(receipt, { workspace, outputDirectory: `.ci-outcome-downloads/${receipt['artifact-name']}` });
  const { children, artifactRoots } = readReceipts(join(workspace, '.ci-outcome-downloads'));
  const aggregate = aggregateOutcomes({ ...input, job: 'aggregate', shard: 'aggregate', target: 'aggregate',
    expectedShards: { test: ['linux'] }, needs: { test: { result: 'success' } }, artifactRoots }, nunitContract, children);
  assert.equal(aggregate['work-status'], 'success');
  assert.deepEqual(aggregate.counts, receipt.counts);
});

test('NUnit: invalid results, inconsistent totals and duplicate identities fail', () => {
  for (const xml of [
    '<test-run><test-case name="test" result="Unknown"/></test-run>',
    '<test-run><test-case name="test" executed="maybe" result="Passed"/></test-run>',
    '<test-run><test-case name="test" executed="False" result="Failed"/></test-run>',
    '<test-run total="2"><test-case name="test" result="Passed"/></test-run>',
    '<test-run><test-case id="same" name="test" result="Passed"/><test-case id="same" name="other" result="Passed"/></test-run>',
    '<test-run><test-case fullname="suite.test" name="test" result="Passed"/><test-case fullname="suite.test" name="other" result="Passed"/></test-run>',
    '<test-results total="1" failures="1" errors="0"><test-case name="test" result="Success"/></test-results>',
  ]) assert.throws(() => parseTestReport(xml, 'nunit'));
});

function jestReport() {
  return { success: true, numTotalTests: 3, numPassedTests: 1, numFailedTests: 0,
    numPendingTests: 1, numTodoTests: 1,
    testResults: [{ name: 'suite.test.js', status: 'passed',
      assertionResults: [{ fullName: 'passes', status: 'passed' }, { fullName: 'skips', status: 'pending' },
        { fullName: 'todo', status: 'todo' }] }] };
}

test('Jest: native suite assertions reconcile pending and todo totals', () => {
  assert.deepEqual(parseTestReport(JSON.stringify(jestReport()), 'jest'),
    { total: 3, executed: 1, skipped: 2, failed: 0, errors: 0 });
});

test('Jest: suite summaries and pending suite assertions must reconcile', () => {
  const report = jestReport();
  report.testResults.push({ name: 'skipped.test.js', status: 'pending',
    assertionResults: [{ fullName: 'skipped suite case', status: 'pending' }] });
  Object.assign(report, { numTotalTests: 4, numPendingTests: 2,
    numTotalTestSuites: 2, numPassedTestSuites: 1, numFailedTestSuites: 0, numPendingTestSuites: 1,
    numRuntimeErrorTestSuites: 0 });
  assert.equal(parseTestReport(JSON.stringify(report), 'jest').skipped, 3);
  report.numTotalTestSuites = 1;
  assert.throws(() => parseTestReport(JSON.stringify(report), 'jest'));
  report.numTotalTestSuites = 2;
  report.numRuntimeErrorTestSuites = 1;
  assert.throws(() => parseTestReport(JSON.stringify(report), 'jest'));
  report.numRuntimeErrorTestSuites = 0;
  report.testResults[0].status = 'pending';
  assert.throws(() => parseTestReport(JSON.stringify(report), 'jest'));
});

test('Jest: failures cannot be hidden by top-level success', () => {
  const report = jestReport();
  report.testResults[0].assertionResults[0].status = 'failed';
  assert.throws(() => parseTestReport(JSON.stringify(report), 'jest'));
  report.numPassedTests = 0;
  report.numFailedTests = 1;
  report.success = false;
  report.testResults[0].status = 'failed';
  assert.equal(parseTestReport(JSON.stringify(report), 'jest').failed, 1);
});

test('Jest: invalid and optimistic suite/case shapes fail', () => {
  const mutations = [
    report => { report.numTotalTests = 3.5; },
    report => { report.numFailedTests = Number.MAX_SAFE_INTEGER + 1; },
    report => { report.numTodoTests = -1; },
    report => { report.testResults = []; },
    report => { report.testResults[0].assertionResults[0].fullName = ''; },
    report => { report.testResults[0].assertionResults[0].status = 'unknown'; },
    report => { report.testResults[0].assertionResults.push(report.testResults[0].assertionResults[0]); },
    report => { report.testResults[0].status = 'failed'; },
    report => { report.testResults[0].status = 'skipped'; },
    report => { report.testResults[0].assertionResults = null; },
    report => { report.success = 'true'; },
  ];
  for (const mutate of mutations) {
    const report = jestReport();
    mutate(report);
    assert.throws(() => parseTestReport(JSON.stringify(report), 'jest'));
  }
});

test('reports: raw files have named paths, lengths and SHA256 identities', t => {
  const workspace = fixture(t);
  write(workspace, 'results/linux.xml', junit);
  const report = inspectReport(workspace, { path: 'results/linux.xml', kind: 'junit' });
  assert.equal(report.files[0].path, 'results/linux.xml');
  assert.equal(report.files[0].bytes, Buffer.byteLength(junit));
  assert.match(report.files[0].sha256, /^[a-f0-9]{64}$/);
});

test('reports: structured JSON requires meaningful summary/results and safe counts', t => {
  const workspace = fixture(t);
  const spec = { path: 'lint.json', kind: 'json', tool: 'lint' };
  write(workspace, spec.path, { tool: 'lint', summary: { total: 1, errors: 0, warnings: 1 }, results: [{ path: 'a' }] });
  assert.equal(inspectReport(workspace, spec).findings, 1);
  for (const value of [{}, [], { summary: {}, results: [] }, { summary: { total: 1 } },
    { tool: 'lint', summary: { total: 1.5 }, results: [] },
    { tool: 'other', summary: { total: 1 }, results: [] }]) {
    write(workspace, spec.path, value);
    assert.throws(() => inspectReport(workspace, spec));
  }
});

test('reports: native frontmatter file identities and totals are independently reconciled', t => {
  const workspace = fixture(t);
  const spec = { path: 'frontmatter.json', kind: 'json' };
  const data = { summary: { totalFiles: 2, passedFiles: 1, failedFiles: 1, errorCount: 1, warningCount: 0 },
    results: [{ file: 'a.md', isValid: true }, { file: 'b.md', isValid: false }] };
  write(workspace, spec.path, data);
  assert.equal(inspectReport(workspace, spec).successful, false);
  for (const mutate of [
    report => { report.results[1].file = 'a.md'; },
    report => { report.results[1].isValid = true; },
    report => { report.summary.totalFiles = 3; },
    report => { report.results[1].isValid = 'false'; },
  ]) {
    const altered = structuredClone(data);
    mutate(altered);
    write(workspace, spec.path, altered);
    assert.throws(() => inspectReport(workspace, spec));
  }
});

test('reports: native lock results require unique positive projects and truthful drift', t => {
  const workspace = fixture(t);
  const spec = { path: 'locks.json', kind: 'json' };
  const data = { projects_count: 2, drift_count: 0, check_passed: true,
    results: [{ Project: '.', Passed: true }, { Project: 'training', Passed: true }] };
  write(workspace, spec.path, data);
  assert.equal(inspectReport(workspace, spec).successful, true);
  data.results[1].Passed = false;
  write(workspace, spec.path, data);
  assert.throws(() => inspectReport(workspace, spec));
  data.drift_count = 1;
  data.check_passed = false;
  write(workspace, spec.path, data);
  assert.equal(inspectReport(workspace, spec).successful, false);
});

test('reports: image discovery manifests bind positive counts to immutable shard identities', t => {
  const workspace = fixture(t);
  const spec = { path: 'manifest.json', kind: 'json' };
  const image = `registry.example/image:stable@sha256:${'a'.repeat(64)}`;
  const data = { summary: { images_count: 1, overall_passed: true }, results: [{ image, shard: 'image' }] };
  write(workspace, spec.path, data);
  assert.equal(inspectReport(workspace, spec).successful, true);
  for (const value of [
    { ...data, results: [] },
    { ...data, summary: { images_count: 0, overall_passed: true }, results: [] },
    { ...data, results: [{ image: 'registry.example/image:stable', shard: 'image' }] },
    { ...data, results: [{ image, shard: 'unsafe/shard' }] },
    { summary: { images_count: 2, overall_passed: true }, results: [...data.results, ...data.results] },
  ]) {
    write(workspace, spec.path, value);
    assert.throws(() => inspectReport(workspace, spec));
  }
});

test('reports: native scanner JSON shapes preserve findings and reject empty execution', t => {
  const workspace = fixture(t);
  const spec = { path: 'lint.json', kind: 'json' };
  for (const data of [
    { totalFiles: 1, errorCount: 0, warningCount: 1, results: { file: 'a.ps1', severity: 'Warning' } },
    { totalFiles: 1, errorCount: 0, warningCount: 0, issues: [] },
    { totalFiles: 1, errorCount: 0, warningCount: 0, results: null },
    { files_checked: 1, lint_passed: true, error_count: 0, warning_count: 0, issues: [] },
    { files_checked: 1, lint_passed: true, error_count: 0, warning_count: 0, issues: null },
    { lint_passed: true, summary: { overall_passed: true }, violation_count: 0, golangci_lint_version: '2.0' },
    { summary: { overall_passed: true }, skipped: false, drift_detected: false, terraform_docs_version: '0.20' },
    { summary: { overall_passed: true, files_drifted: 0 }, skipped: false, drift_detected: false,
      drifted_files: [], terraform_docs_version: '0.20' },
    { format_check: { passed: true }, validation: [{ directory: '.', passed: true, skipped: false }],
      summary: { directories_checked: 1, directories_passed: 1 } },
  ]) {
    write(workspace, spec.path, data);
    assert.equal(inspectReport(workspace, spec).successful, true, JSON.stringify(data));
  }
  for (const data of [
    { totalFiles: 0, errorCount: 0, warningCount: 0, issues: [] },
    { totalFiles: 1, errorCount: 0, warningCount: 0 },
    { files_checked: 1, lint_passed: true, error_count: 1, warning_count: 0, issues: [] },
    { lint_passed: false, summary: { overall_passed: true }, violation_count: 0, golangci_lint_version: '2.0' },
    { lint_passed: true, summary: { overall_passed: true }, violation_count: 1, golangci_lint_version: '2.0' },
    { format_check: { passed: true }, validation: [], summary: { directories_checked: 0 } },
    { format_check: { passed: true }, validation: [{ directory: '.', passed: true, skipped: false }],
      summary: { directories_checked: 2 } },
  ]) {
    write(workspace, spec.path, data);
    assert.throws(() => inspectReport(workspace, spec), undefined, JSON.stringify(data));
  }
});

test('reports: native date freshness arrays retain stale findings and exact typed identities', t => {
  const workspace = fixture(t);
  const spec = { path: 'freshness.json', kind: 'json' };
  const current = { File: 'a.md', MsDate: '09/01/2026', AgeDays: 23, IsStale: false, Threshold: 90 };
  const stale = { File: 'b.md', MsDate: '01/01/2026', AgeDays: 266, IsStale: true, Threshold: 90 };
  write(workspace, spec.path, [current]);
  assert.equal(inspectReport(workspace, spec).successful, true);
  write(workspace, spec.path, [current, stale]);
  assert.equal(inspectReport(workspace, spec).findings, 1);
  assert.equal(inspectReport(workspace, spec).successful, false);
  for (const records of [[], [current, current], [{ ...current, AgeDays: 1.5 }],
    [{ ...current, IsStale: true }], [{ ...current, Threshold: -1 }],
    [{ ...current, MsDate: null }], [{ ...current, File: '' }]]) {
    write(workspace, spec.path, records);
    assert.throws(() => inspectReport(workspace, spec));
  }
});

test('reports: native execution flags and expected Terraform inventory cannot be optimistic', t => {
  const workspace = fixture(t);
  const spec = { path: 'native.json', kind: 'json' };
  for (const data of [
    { totalFiles: 1, errorCount: 0, warningCount: 0, issues: [], lint_passed: false },
    { files_checked: 1, error_count: 0, warning_count: 0, issues: [], lint_passed: true,
      execution_errors: ['native execution failed'] },
  ]) {
    write(workspace, spec.path, data);
    assert.equal(inspectReport(workspace, spec).successful, false);
  }
  const terraform = { format_check: { passed: true },
    validation: [{ directory: '.', passed: true, skipped: false }],
    summary: { directories_checked: 1, directories_expected: 1, directories_passed: 1, directories_skipped: 0,
      format_passed: true, overall_passed: true } };
  write(workspace, spec.path, terraform);
  assert.equal(inspectReport(workspace, spec).successful, true);
  for (const key of ['directories_expected', 'directories_passed', 'directories_skipped']) {
    const altered = structuredClone(terraform);
    altered.summary[key]++;
    write(workspace, spec.path, altered);
    assert.throws(() => inspectReport(workspace, spec));
  }
});

test('reports: clean native link, TFLint and dependency pinning reports allow zero findings', t => {
  const workspace = fixture(t);
  const spec = { path: 'checks.json', kind: 'json' };
  for (const data of [
    { summary: { total_issues: 0, files_affected: 0 }, issues: [] },
    { summary: { total_issues: 0, files_affected: 0 }, issues: null },
    { summary: { total_files: 2, files_with_broken_links: 0, total_links_checked: 4, total_broken_links: 0 },
      broken_links: [] },
    { issues: [], errors: [] },
    { ComplianceScore: 100, TotalDependencies: 3, PinnedDependencies: 3, UnpinnedDependencies: 0,
      ScannedFiles: 2, Violations: [{ Severity: 'Info' }, { Severity: 'Info' }, { Severity: 'Info' }] },
    { ComplianceScore: 100, TotalDependencies: 0, PinnedDependencies: 0, UnpinnedDependencies: 0,
      ScannedFiles: 2, Violations: [] },
  ]) {
    write(workspace, spec.path, data);
    const report = inspectReport(workspace, spec);
    assert.equal(report.successful, true, JSON.stringify(data));
    assert.equal(report.findings, 0);
  }
});

test('reports: native link/TFLint failures and contradictory dependency counts defeat success', t => {
  const workspace = fixture(t);
  const spec = { path: 'checks.json', kind: 'json' };
  for (const data of [
    { summary: { total_issues: 1, files_affected: 1 }, issues: [{ file: 'a.md' }] },
    { summary: { total_files: 2, files_with_broken_links: 1, total_links_checked: 4, total_broken_links: 2 },
      broken_links: [{ file: 'a.md', links: ['a', 'b'] }] },
    { issues: [{ rule: 'terraform_required_version' }], errors: [] },
    { issues: [], errors: [{ message: 'scanner failed' }] },
    { summary: { overall_passed: false, files_drifted: 1 }, skipped: false, drift_detected: true,
      drifted_files: ['README.md'], terraform_docs_version: '0.20' },
  ]) {
    write(workspace, spec.path, data);
    assert.equal(inspectReport(workspace, spec).successful, false, JSON.stringify(data));
  }
  for (const data of [
    { summary: { total_issues: 2, files_affected: 1 }, issues: [] },
    { summary: { total_files: 0, files_with_broken_links: 0, total_links_checked: 0, total_broken_links: 0 },
      broken_links: [] },
    { issues: [], errors: 'not-an-array' },
    { ComplianceScore: 100, TotalDependencies: 2, PinnedDependencies: 1, UnpinnedDependencies: 1,
      ScannedFiles: 1, Violations: [{ Severity: 'Info' }, { Severity: 'High' }] },
  ]) {
    write(workspace, spec.path, data);
    assert.throws(() => inspectReport(workspace, spec));
  }
});

test('reports: dependency pinning preserves configurable step policy while exposing findings', t => {
  const workspace = fixture(t);
  const spec = { path: 'pinning.json', kind: 'json' };
  write(workspace, spec.path, { ComplianceScore: 95, TotalDependencies: 20, PinnedDependencies: 19,
    UnpinnedDependencies: 1, ScannedFiles: 2, Metadata: { ComplianceThreshold: 95 },
    Violations: [...Array.from({ length: 19 }, () => ({ Severity: 'Info' })), { Severity: 'High' }] });
  const pinningContract = structuredClone(contract);
  pinningContract.execution.checks.jobs.test.reports = [spec];
  const input = options(workspace, { reports: [spec] });
  const accepted = recordOutcome(input, pinningContract);
  assert.equal(accepted['work-status'], 'success');
  assert.equal(accepted.counts.findings, 1);
  input.steps.test.outcome = 'failure';
  const rejected = recordOutcome(input, pinningContract);
  assert.equal(rejected['work-status'], 'failure');
  assert.equal(rejected.counts.findings, 1);
});

test('reports: native pinning Info entries count compliant dependencies, not failing findings', t => {
  const workspace = fixture(t);
  const spec = { path: 'pinning.json', kind: 'json' };
  const native = { ScannedFiles: 5, TotalFiles: 0, TotalDependencies: 3, PinnedDependencies: 2,
    UnpinnedDependencies: 1, ComplianceScore: 66.67,
    Violations: [{ Severity: 'Info' }, { Severity: 'Info' }, { Severity: 'High' }],
    Summary: {}, Metadata: { ComplianceThreshold: 60 } };
  write(workspace, spec.path, native);
  assert.equal(inspectReport(workspace, spec).successful, true);
  assert.equal(inspectReport(workspace, spec).findings, 1);
  for (const mutate of [
    report => { report.PinnedDependencies = 1; },
    report => { report.UnpinnedDependencies = 0; },
    report => { report.TotalDependencies = 4; },
    report => { report.Violations[0].Severity = 'High'; },
    report => { delete report.Violations[0].Severity; },
    report => { report.ComplianceScore = 66.68; },
    report => { report.ScannedFiles = 0; },
  ]) {
    const altered = structuredClone(native);
    mutate(altered);
    write(workspace, spec.path, altered);
    assert.throws(() => inspectReport(workspace, spec));
  }
});

function sarif(tool = 'scanner', results = []) {
  return { version: '2.1.0', runs: [{ tool: { driver: { name: tool } }, results }] };
}

test('reports: clean and advisory finding SARIF runs are valid', t => {
  const workspace = fixture(t);
  write(workspace, 'sarif/a.sarif', sarif());
  write(workspace, 'sarif/nested/b.sarif.json', sarif('scanner', [{ message: { text: 'Advisory vulnerability' } }]));
  write(workspace, 'sarif/no-findings.sarif', { version: '2.1.0', runs: [{ tool: { driver: { name: 'scanner' } } }] });
  write(workspace, 'sarif/ignore.txt', 'not a report');
  const report = inspectReport(workspace, { path: 'sarif', kind: 'sarif', tool: 'scanner' });
  assert.equal(report.findings, 1);
  assert.equal(report.files.length, 3);
});

test('reports: invalid SARIF tools, runs, results and scanner failures are rejected', t => {
  const workspace = fixture(t);
  const spec = { path: 'scan.sarif', kind: 'sarif', tool: 'scanner' };
  for (const data of [{}, { version: '2.1.0', runs: [] }, sarif('wrong'), sarif('', []),
    sarif('scanner', [{}]), { version: '2.1.0', runs: [{ tool: { driver: { name: 'scanner' } }, results: null }] },
    { version: '2.1.0', runs: [{ ...sarif().runs[0], invocations: [{ executionSuccessful: false }] }] }]) {
    write(workspace, spec.path, data);
    assert.throws(() => inspectReport(workspace, spec));
  }
  mkdirSync(join(workspace, 'empty'));
  assert.throws(() => inspectReport(workspace, { path: 'empty', kind: 'sarif' }));
});

test('reports: traversal, directories and disguised structured files fail', t => {
  const workspace = fixture(t);
  write(workspace, 'log.txt', 'successful operation\n');
  assert.equal(inspectReport(workspace, { path: 'log.txt', kind: 'file' }).files.length, 1);
  write(workspace, 'empty.txt', '');
  assert.equal(inspectReport(workspace, { path: 'empty.txt', kind: 'file' }).files[0].bytes, 0);
  write(workspace, 'data.json', '{}');
  for (const [path, kind] of [
    ['../escape', 'file'], ['C:/escape', 'file'], ['/absolute', 'file'], ['back\\slash', 'file'],
    ['bad\nname', 'file'], ['missing', 'file'], ['data.json', 'file'],
    ['log.txt', 'unknown'], ['.', 'file'],
  ]) assert.throws(() => inspectReport(workspace, { path, kind }));
});

test('reports: empty structured reports remain invalid for every structured adapter', t => {
  const workspace = fixture(t);
  write(workspace, 'empty-report', '');
  for (const kind of ['junit', 'nunit', 'jest', 'json', 'sarif']) {
    assert.throws(() => inspectReport(workspace, { path: 'empty-report', kind }), /Empty/);
  }
});

test('record: canonical operation outcomes, reports and counts are published', t => {
  const workspace = fixture(t);
  write(workspace, 'results/linux.xml', junit);
  const receipt = recordOutcome(options(workspace), contract);
  assert.equal(receipt['work-status'], 'success');
  assert.deepEqual(receipt.counts, { expected: 2, executed: 2, tests: 1, 'skipped-tests': 1,
    'skipped-operations': 0, findings: 0 });
  assert.equal(receipt.job, 'test');
  assert.equal(receipt['run-id'], '123');
  assert.equal(receipt['run-attempt'], '1');
  assert.equal(receipt.target, 'linux');
  assert.match(receipt['artifact-name'], /checks-test-linux-123-1$/);
});

test('record: tolerated failure uses outcome rather than optimistic conclusion', t => {
  const workspace = fixture(t);
  write(workspace, 'results/linux.xml', junit);
  const input = options(workspace);
  input.steps.test = { outcome: 'failure', conclusion: 'success', outputs: { password: 'not-evidence' } };
  const receipt = recordOutcome(input, contract);
  assert.equal(receipt['work-status'], 'failure');
  assert.equal(receipt['first-failure'], 'test');
  assert.equal(receipt.counts.executed, 2);
  assert.doesNotMatch(JSON.stringify(receipt), /password|not-evidence/);
});

test('record: cancellation remains distinct and excludes unrun operations', t => {
  const workspace = fixture(t);
  const input = options(workspace);
  input.steps.setup.outcome = 'cancelled';
  input.steps.test.outcome = 'skipped';
  const receipt = recordOutcome(input, contract);
  assert.equal(receipt['work-status'], 'cancelled');
  assert.equal(receipt.counts.executed, 0);
  assert.equal(receipt.counts['skipped-operations'], 1);
});

test('record: missing or unexpected skipped required steps fail', t => {
  const workspace = fixture(t);
  write(workspace, 'results/linux.xml', junit);
  for (const value of [undefined, { outcome: 'skipped', conclusion: 'skipped' }, { outcome: 'unknown' }]) {
    const input = options(workspace);
    input.steps.test = value;
    const receipt = recordOutcome(input, contract);
    assert.equal(receipt['work-status'], 'failure');
    assert.equal(receipt.counts.executed, 1);
  }
});

test('record: planned skip requires explicit selection and every operation skipped', t => {
  const workspace = fixture(t);
  const input = options(workspace, { selected: false, steps: {
    setup: { outcome: 'skipped' }, test: { outcome: 'skipped' },
  } });
  const receipt = recordOutcome(input, contract);
  assert.equal(receipt['work-status'], 'planned-skip');
  assert.equal(receipt.counts.expected, 0);
  assert.equal(receipt.reports.length, 0);
  assert.equal(outcomeOutputs(receipt)['first-failure'], '');
  assert.equal(receipt['first-failure'], '');
  input.steps.test.outcome = 'success';
  assert.equal(recordOutcome(input, contract)['work-status'], 'failure');
});

test('record: removed required operation or report and substituted job fail closed', t => {
  const workspace = fixture(t);
  write(workspace, 'results/linux.xml', junit);
  for (const overrides of [{ requiredSteps: ['test'] }, { requiredSteps: ['test', 'test'] },
    { reports: [] }, { reports: [{ path: 'other.xml', kind: 'junit' }] }, { job: 'other' },
    { steps: null }, { tools: ['unknown'] }]) {
    const receipt = recordOutcome(options(workspace, overrides), contract);
    assert.equal(receipt['work-status'], 'failure', JSON.stringify(overrides));
  }
});

test('record: failed report overrides successful steps and empty sibling cannot be masked', t => {
  const workspace = fixture(t);
  const input = options(workspace);
  write(workspace, 'results/linux.xml', '<testsuite><testcase name="bad"><failure/></testcase></testsuite>');
  assert.equal(recordOutcome(input, contract)['work-status'], 'failure');
  const expanded = structuredClone(contract);
  expanded.execution.checks.jobs.test.reports.push({ path: 'extra.xml', kind: 'junit' });
  input.reports.push({ path: 'extra.xml', kind: 'junit' });
  write(workspace, 'results/linux.xml', junit);
  write(workspace, 'extra.xml', '<testsuite tests="0"/>');
  const receipt = recordOutcome(input, expanded);
  assert.equal(receipt['work-status'], 'failure');
  assert.equal(receipt.counts.tests, 1);
  assert.equal(receipt['first-failure'], 'report:extra.xml');
});

test('record: immutable target is checked independently against canonical metadata', t => {
  const workspace = fixture(t);
  const pinned = structuredClone(contract);
  pinned.execution.checks.jobs.test.target = `registry.example/image@sha256:${'1'.repeat(64)}`;
  assert.equal(recordOutcome(options(workspace), pinned)['work-status'], 'failure');
  write(workspace, 'results/linux.xml', junit);
  const receipt = recordOutcome(options(workspace, { target: pinned.execution.checks.jobs.test.target }), pinned);
  assert.equal(receipt['work-status'], 'success');
});

test('record: required tool context comes from canonical metadata rather than caller assertions', t => {
  const workspace = fixture(t);
  const canonical = structuredClone(contract);
  canonical.execution.checks.jobs.test.reports = [{ path: 'scan.sarif', kind: 'sarif', tool: 'scanner' }];
  const input = options(workspace, { reports: [{ path: 'scan.sarif', kind: 'sarif' }] });
  write(workspace, 'scan.sarif', sarif());
  assert.equal(recordOutcome(input, canonical)['work-status'], 'success');
  write(workspace, 'scan.sarif', sarif('wrong'));
  assert.equal(recordOutcome(input, canonical)['work-status'], 'failure');
  input.reports[0].tool = 'wrong';
  assert.equal(recordOutcome(input, canonical)['first-failure'], 'contract');
});

test('record: schema identity and selection inputs cannot contain unsafe values', t => {
  const workspace = fixture(t);
  for (const overrides of [{ workflow: '../checks' }, { job: '' }, { shard: 'line\nbreak' },
    { runId: '0' }, { runAttempt: '-1' }, { sha: 'main' }, { target: 'a b' },
    { selected: 'true' }, { selectionReason: '' }]) {
    assert.throws(() => recordOutcome(options(workspace, overrides), contract));
  }
});

test('publication: failure receipts retain available invalid reports and do not leak step outputs', t => {
  const workspace = fixture(t);
  write(workspace, 'results/linux.xml', '<broken');
  const receipt = recordOutcome(options(workspace), contract);
  const output = join(workspace, 'outputs');
  const summary = join(workspace, 'summary');
  const result = publishOutcome(receipt, { workspace, githubOutput: output, githubSummary: summary });
  assert.equal(readFileSync(join(result['artifact-path'], 'reports', 'results', 'linux.xml'), 'utf8'), '<broken');
  assert.match(readFileSync(output, 'utf8'), /work-status=failure/);
  assert.match(readFileSync(summary, 'utf8'), /report:results\/linux.xml/);
  assert.throws(() => publishOutcome(receipt, { workspace }), /already exists/);
});

test('publication: changed raw report cannot replace measured evidence', t => {
  const workspace = fixture(t);
  write(workspace, 'results/linux.xml', junit);
  const receipt = recordOutcome(options(workspace), contract);
  write(workspace, 'results/linux.xml', passingXml);
  assert.throws(() => publishOutcome(receipt, { workspace }), /changed/);
});

test('publication: output names and summary escaping are deterministic', t => {
  const workspace = fixture(t);
  write(workspace, 'results/linux.xml', junit);
  const receipt = recordOutcome(options(workspace, { selectionReason: 'caller | <script> <SCRIPT> <ScRiPt> [bad](url)' }), contract);
  const summary = join(workspace, 'summary');
  const outputs = publishOutcome(receipt, { workspace, githubSummary: summary });
  assert.deepEqual(Object.keys(outcomeOutputs(receipt)), [
    'work-status', 'expected-count', 'executed-count', 'test-count', 'skipped-count', 'first-failure', 'artifact-name',
  ]);
  assert.equal(outputs['skipped-count'], '1');
  assert.equal(outputs['first-failure'], '');
  assert.equal(receipt['first-failure'], '');
  assert.doesNotMatch(readFileSync(summary, 'utf8'), /<script>|\[bad\]/i);
});

test('aggregate: current exact shard receipts are remeasured from raw artifacts', t => {
  const { aggregate, children } = childFixture(t);
  const result = aggregateOutcomes(aggregate, contract, children);
  assert.equal(result['work-status'], 'success');
  assert.deepEqual(result.counts, children[0].counts);
  assert.equal(result.children.length, 1);
});

test('aggregate: duplicate, unexpected or missing receipts fail', t => {
  const { aggregate, children, child } = childFixture(t);
  for (const candidates of [[], [...children, child], [{ ...child, shard: 'unexpected' }],
    [{ ...child, job: 'substitute' }]]) {
    assert.equal(aggregateOutcomes(aggregate, contract, candidates)['work-status'], 'failure');
  }
});

test('aggregate: stale run, attempt, SHA, workflow, target and schema are rejected', t => {
  const { aggregate, children, child } = childFixture(t);
  for (const [key, value] of [['run-id', '124'], ['run-attempt', '2'], ['sha', 'b'.repeat(40)],
    ['workflow', 'other'], ['target', 'wrong'], ['version', 2], ['mode', 'aggregate'],
    ['artifact-name', 'renamed']]) {
    const previous = child[key];
    child[key] = value;
    assert.equal(aggregateOutcomes(aggregate, contract, children)['work-status'], 'failure', key);
    child[key] = previous;
  }
});

test('aggregate: optimistic counts, operations and removed reports cannot self-certify', t => {
  const { aggregate, children, child } = childFixture(t);
  const mutations = [
    item => { item.counts.executed = 1; }, item => { item.counts.tests = 9007199254740992; },
    item => { item.counts.extra = 1; }, item => { item.operations.pop(); },
    item => { item.operations[0].outcome = 'failure'; }, item => { item.reports = []; },
    item => { item.reports[0].counts.executed = 10; }, item => { item.selected = false; },
    item => { item['work-status'] = 'failure'; }, item => { item.errors.push('failure'); },
  ];
  const original = structuredClone(child);
  for (const mutate of mutations) {
    for (const key of Object.keys(child)) delete child[key];
    Object.assign(child, structuredClone(original));
    mutate(child);
    assert.equal(aggregateOutcomes(aggregate, contract, children)['work-status'], 'failure');
  }
});

test('aggregate: raw artifact tampering or missing artifact root fails', t => {
  const { aggregate, children, workspace, child } = childFixture(t);
  const rootPath = aggregate.artifactRoots.get(child);
  write(rootPath, 'reports/results/linux.xml', passingXml);
  assert.equal(aggregateOutcomes(aggregate, contract, children)['work-status'], 'failure');
  aggregate.artifactRoots = new Map();
  assert.equal(aggregateOutcomes(aggregate, contract, children)['work-status'], 'failure');
  assert.ok(existsSync(workspace));
});

test('aggregate: required selected child failure or cancellation cannot be hidden', t => {
  const { aggregate, children } = childFixture(t);
  aggregate.needs.test.result = 'failure';
  assert.equal(aggregateOutcomes(aggregate, contract, children)['work-status'], 'failure');
  aggregate.needs.test.result = 'cancelled';
  assert.equal(aggregateOutcomes(aggregate, contract, children)['work-status'], 'cancelled');
});

test('aggregate: unselected child permits only skipped result and no receipt', t => {
  const { aggregate } = childFixture(t);
  aggregate.expectedShards = { test: [] };
  aggregate.selected = false;
  aggregate.needs.test.result = 'skipped';
  assert.equal(aggregateOutcomes(aggregate, contract, [])['work-status'], 'planned-skip');
  aggregate.needs.test.result = 'success';
  assert.equal(aggregateOutcomes(aggregate, contract, [])['work-status'], 'failure');
});

test('aggregate: missing canonical jobs, duplicate shards and contradictory selection fail', t => {
  const { aggregate, children } = childFixture(t);
  for (const overrides of [{ expectedShards: {} }, { expectedShards: { test: ['linux', 'linux'] } },
    { expectedShards: { test: ['linux'], extra: [] } }, { selected: false }, { needs: {} },
    { expectedTargets: { extra: {} } }, { expectedTargets: { test: { other: 'wrong' } } }]) {
    assert.equal(aggregateOutcomes({ ...aggregate, ...overrides }, contract, children)['work-status'], 'failure');
  }
});

test('aggregate: callers cannot shrink the independently required static shard inventory', t => {
  const staticContract = structuredClone(contract);
  staticContract.execution.checks.jobs.test.shards = ['linux', 'windows'];
  const { aggregate, children } = childFixture(t, staticContract);
  const omitted = aggregateOutcomes(aggregate, staticContract, children);
  assert.equal(omitted['work-status'], 'failure');
  assert.match(omitted.errors.join(' '), /canonical shard inventory/);
  const skipped = aggregateOutcomes({ ...aggregate, selected: false,
    expectedShards: { test: [] }, needs: { test: { result: 'skipped' } } }, staticContract, []);
  assert.equal(skipped['work-status'], 'planned-skip');
  staticContract.execution.checks.jobs.test.shards = ['linux'];
  assert.equal(aggregateOutcomes(aggregate, staticContract, children)['work-status'], 'success');
});

test('record: shard identity must belong to the canonical static inventory', t => {
  const workspace = fixture(t);
  const staticContract = structuredClone(contract);
  staticContract.execution.checks.jobs.test.shards = ['windows'];
  const receipt = recordOutcome(options(workspace), staticContract);
  assert.equal(receipt['work-status'], 'failure');
  assert.match(receipt.errors.join(' '), /Unexpected canonical shard/);
});

test('aggregate: immutable image targets bind to independent discovery outputs', t => {
  const dynamic = structuredClone(contract);
  dynamic.execution.checks.jobs.test.discovery = { job: 'discover',
    'shards-output': 'shards', 'targets-output': 'targets' };
  dynamic.execution.checks.jobs.test.shards = ['obsolete-static-shard'];
  const target = `registry.example/image@sha256:${'f'.repeat(64)}`;
  const { aggregate, children } = childFixture(t, dynamic, { target });
  aggregate.expectedTargets = { test: { linux: target } };
  aggregate.needs.discover = { result: 'success', outputs: {
    shards: '["linux"]', targets: JSON.stringify({ linux: target }),
  } };
  assert.equal(aggregateOutcomes(aggregate, dynamic, children)['work-status'], 'success');
  for (const [key, value] of [['shards', '[]'], ['targets', '{}'], ['targets', '{"linux":"mutable:latest"}']]) {
    const previous = aggregate.needs.discover.outputs[key];
    aggregate.needs.discover.outputs[key] = value;
    assert.equal(aggregateOutcomes(aggregate, dynamic, children)['work-status'], 'failure');
    aggregate.needs.discover.outputs[key] = previous;
  }
  aggregate.needs.discover.result = 'failure';
  assert.equal(aggregateOutcomes(aggregate, dynamic, children)['work-status'], 'failure');
});

test('aggregate: canonical fixed target cannot be overridden by expected targets', t => {
  const fixed = structuredClone(contract);
  fixed.execution.checks.jobs.test.target = 'immutable-{shard}';
  const { aggregate, children } = childFixture(t, fixed, { target: 'immutable-linux' });
  assert.equal(aggregateOutcomes(aggregate, fixed, children)['work-status'], 'success');
  aggregate.expectedTargets = { test: { linux: 'wrong' } };
  assert.equal(aggregateOutcomes(aggregate, fixed, children)['work-status'], 'failure');
});

test('artifact reader: absent, malformed and renamed artifact layouts fail', t => {
  const workspace = fixture(t);
  assert.throws(() => readReceipts(join(workspace, 'missing')));
  write(workspace, 'flat.json', '{}');
  assert.throws(() => readReceipts(workspace));
  rmSync(join(workspace, 'flat.json'));
  write(workspace, 'renamed/receipt.json', { 'artifact-name': 'other' });
  assert.throws(() => readReceipts(workspace), /artifact identity/);
});

function cliEnvironment(workspace, extra = {}) {
  return { ...process.env, GITHUB_WORKSPACE: workspace, GITHUB_JOB: 'test', GITHUB_RUN_ID: '123',
    GITHUB_RUN_ATTEMPT: '1', GITHUB_SHA: sha, GITHUB_OUTPUT: join(workspace, 'outputs'),
    GITHUB_STEP_SUMMARY: join(workspace, 'summary'), CI_CONTRACT_PATH: write(workspace, 'contract.json', contract),
    INPUT_WORKFLOW: 'checks', INPUT_SHARD: 'linux', INPUT_SELECTED: 'true', INPUT_TOOLS: '[]',
    INPUT_STEPS_JSON: JSON.stringify(options(workspace).steps),
    INPUT_REQUIRED_STEPS: '["setup","test"]', INPUT_REPORTS: '[{"path":"results/linux.xml","kind":"junit"}]',
    ...extra };
}

function runCli(workspace, extra = {}) {
  return spawnSync(process.execPath, [helper], { cwd: workspace, env: cliEnvironment(workspace, extra),
    encoding: 'utf8', timeout: 30_000 });
}

function resolveDownloadPath(workspace, expectedShards, extra = {}) {
  const action = parseDocument(readFileSync(join(root, '.github/actions/ci-outcome/action.yml'), 'utf8')).toJS();
  const step = action.runs.steps.find(item => item.id === 'download-path');
  assert.ok(step, 'Missing expected-inventory download path resolver');
  const script = /^node --input-type=module <<'NODE'\r?\n([\s\S]*?)\r?\nNODE\s*$/.exec(step.run);
  assert.ok(script, 'Download resolver must execute the tested Node program');
  const output = join(workspace, 'download-output');
  const result = spawnSync(process.execPath, ['--input-type=module', '-e', script[1]], {
    cwd: root, encoding: 'utf8', timeout: 30_000,
    env: { ...process.env, GITHUB_JOB: 'aggregate', GITHUB_RUN_ID: '123', GITHUB_RUN_ATTEMPT: '1',
      GITHUB_SHA: sha, GITHUB_OUTPUT: output, INPUT_WORKFLOW: 'checks',
      INPUT_EXPECTED_SHARDS: JSON.stringify(expectedShards), ...extra },
  });
  const path = existsSync(output) ? /^path=(.+)$/m.exec(readFileSync(output, 'utf8'))?.[1] : undefined;
  return { result, path };
}

test('composite download: destination follows the complete expected shard inventory', t => {
  for (const [expected, path] of [
    [{ test: ['linux'] }, '.ci-outcome-downloads/ci-outcome-checks-test-linux-123-1'],
    [{ test: [], other: ['linux'] }, '.ci-outcome-downloads/ci-outcome-checks-other-linux-123-1'],
    [{ test: ['linux', 'windows'] }, '.ci-outcome-downloads'],
    [{ test: ['linux'], other: ['linux'] }, '.ci-outcome-downloads'],
    [{ test: [] }, '.ci-outcome-downloads'],
  ]) {
    const resolved = resolveDownloadPath(fixture(t), expected);
    assert.equal(resolved.result.status, 0, resolved.result.stderr);
    assert.equal(resolved.path, path);
  }
});

test('composite download: unsafe identity and malformed inventory fail before exposing a path', t => {
  for (const extra of [
    { INPUT_WORKFLOW: '../checks' }, { GITHUB_RUN_ID: '0' }, { GITHUB_RUN_ATTEMPT: '1\npath=other' },
    { GITHUB_SHA: 'main' }, { INPUT_EXPECTED_SHARDS: '{bad' }, { INPUT_EXPECTED_SHARDS: 'null' },
    { INPUT_EXPECTED_SHARDS: '[]' }, { INPUT_EXPECTED_SHARDS: '{"../job":["linux"]}' },
    { INPUT_EXPECTED_SHARDS: '{"test":["../linux"]}' }, { INPUT_EXPECTED_SHARDS: '{"test":"linux"}' },
    { INPUT_EXPECTED_SHARDS: '{"test":["linux","linux"]}' },
  ]) {
    const resolved = resolveDownloadPath(fixture(t), { test: ['linux'] }, extra);
    assert.notEqual(resolved.result.status, 0);
    assert.equal(resolved.path, undefined);
  }
});

test('composite download: singleton extraction aggregates without weakening artifact validation', t => {
  for (const { expected, actual, success, stale = false } of [
    { expected: ['linux'], actual: ['linux'], success: true },
    { expected: ['linux', 'windows'], actual: ['linux', 'windows'], success: true },
    { expected: [], actual: [], success: true },
    { expected: ['linux'], actual: [], success: false },
    { expected: ['linux', 'windows'], actual: ['linux'], success: false },
    { expected: ['linux'], actual: ['linux', 'windows'], success: false },
    { expected: ['linux'], actual: ['windows'], success: false },
    { expected: [], actual: ['linux'], success: false },
    { expected: ['linux'], actual: ['linux'], success: false, stale: true },
  ]) {
    const workspace = fixture(t);
    const expectedShards = { test: expected };
    const resolved = resolveDownloadPath(workspace, expectedShards);
    assert.equal(resolved.result.status, 0, resolved.result.stderr);
    for (const shard of actual) {
      const path = `results/${shard}.xml`;
      write(workspace, path, junit);
      const child = recordOutcome(options(workspace, { shard, target: shard, runAttempt: stale ? '2' : '1',
        reports: [{ path, kind: 'junit' }] }), contract);
      assert.equal(child['work-status'], 'success');
      // The pinned download action flattens one match, even with merge-multiple=false.
      const outputDirectory = actual.length === 1 ? resolved.path : `${resolved.path}/${child['artifact-name']}`;
      publishOutcome(child, { workspace, outputDirectory });
    }
    const result = runCli(workspace, { GITHUB_JOB: 'aggregate', INPUT_SHARD: 'aggregate',
      INPUT_EXPECTED_SHARDS: JSON.stringify(expectedShards), INPUT_SELECTED: String(expected.length > 0),
      INPUT_NEEDS_JSON: JSON.stringify({ test: { result: expected.length ? 'success' : 'skipped' } }) });
    assert.equal(result.status, success ? 0 : 1, JSON.stringify({ expected, actual, stale }) + result.stderr);
    if (success) {
      assert.match(readFileSync(join(workspace, 'outputs'), 'utf8'), new RegExp(`^test-count=${actual.length}$`, 'm'));
    }
  }
});

test('CLI subprocess: success publishes all evidence outputs and named raw reports', t => {
  const workspace = fixture(t);
  write(workspace, 'results/linux.xml', junit);
  const result = runCli(workspace);
  assert.ifError(result.error);
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /checks\/linux: success/);
  assert.match(readFileSync(join(workspace, 'outputs'), 'utf8'), /test-count=1/);
  assert.match(readFileSync(join(workspace, 'outputs'), 'utf8'), /^first-failure=$/m);
  assert.ok(existsSync(join(workspace, '.ci-outcomes', 'ci-outcome-checks-test-linux-123-1', 'receipt.json')));
});

test('CLI subprocess: caller working directory cannot redirect workspace report resolution', t => {
  const workspace = fixture(t);
  mkdirSync(join(workspace, 'gpu-offload'));
  write(workspace, 'results/linux.xml', junit);
  const result = spawnSync(process.execPath, [helper], {
    cwd: join(workspace, 'gpu-offload'), env: cliEnvironment(workspace), encoding: 'utf8', timeout: 30_000,
  });
  assert.equal(result.status, 0, result.stderr);
  assert.match(readFileSync(join(workspace, 'outputs'), 'utf8'), /work-status=success/);
  assert.ok(existsSync(join(workspace, '.ci-outcomes', 'ci-outcome-checks-test-linux-123-1', 'receipt.json')));
  assert.equal(existsSync(join(workspace, 'gpu-offload', '.ci-outcomes')), false);
});

test('CLI subprocess: failed steps still publish even with advisory fail-on-error=false', t => {
  const workspace = fixture(t);
  write(workspace, 'results/linux.xml', junit);
  const input = options(workspace);
  input.steps.test.outcome = 'failure';
  const result = runCli(workspace, { INPUT_STEPS_JSON: JSON.stringify(input.steps), INPUT_FAIL_ON_ERROR: 'false' });
  assert.equal(result.status, 1);
  assert.match(readFileSync(join(workspace, 'outputs'), 'utf8'), /work-status=failure/);
});

test('CLI subprocess: malformed inputs publish a failure receipt rather than silently succeeding', t => {
  for (const extra of [{ INPUT_REQUIRED_STEPS: '{bad' }, { INPUT_SELECTED: 'yes' },
    { INPUT_FAIL_ON_ERROR: 'maybe' }, { CI_CONTRACT_PATH: join(root, 'missing-contract.json') }]) {
    const workspace = fixture(t);
    const result = runCli(workspace, extra);
    assert.equal(result.status, 1);
    assert.match(readFileSync(join(workspace, 'outputs'), 'utf8'), /work-status=failure/);
  }
});

test('CLI subprocess: invalid receipt identity reports publication failure without leaking input', t => {
  const workspace = fixture(t);
  const result = runCli(workspace, { INPUT_WORKFLOW: 'unsafe\nsecret' });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /Unable to publish/);
  assert.doesNotMatch(result.stderr, /secret/);
});

test('CLI subprocess: aggregate downloads are validated and zero selected shards can skip', t => {
  const { workspace } = childFixture(t);
  const result = runCli(workspace, { GITHUB_JOB: 'aggregate', INPUT_SHARD: 'aggregate',
    INPUT_EXPECTED_SHARDS: '{"test":["linux"]}', INPUT_NEEDS_JSON: '{"test":{"result":"success"}}' });
  assert.equal(result.status, 0, result.stderr);
  const empty = fixture(t);
  const skipped = runCli(empty, { GITHUB_JOB: 'aggregate', INPUT_SHARD: 'aggregate', INPUT_SELECTED: 'false',
    INPUT_EXPECTED_SHARDS: '{"test":[]}', INPUT_NEEDS_JSON: '{"test":{"result":"skipped"}}' });
  assert.equal(skipped.status, 0, skipped.stderr);
  assert.match(readFileSync(join(empty, 'outputs'), 'utf8'), /work-status=planned-skip/);
});

test('tool probes: known bounded commands report versions, unknown commands are rejected', () => {
  assert.match(probeTools(['node']).node, /^v\d/);
  assert.throws(() => probeTools(['arbitrary-command']), /Unknown/);
  assert.throws(() => probeTools(['node', 'node']), /Invalid/);
});

test('actual process: cold, warm and unavailable caches never suppress the validation operation', t => {
  for (const state of ['cold', 'warm', 'unavailable']) {
    const workspace = fixture(t);
    const cache = join(workspace, 'cache');
    if (state === 'warm') write(workspace, 'cache', 'cached dependency');
    const operation = `
      const fs = require('node:fs');
      const state = process.argv[1];
      if (state !== 'unavailable' && !fs.existsSync('cache')) fs.writeFileSync('cache', 'installed dependency');
      fs.appendFileSync('invocations', 'validate\\n');
      fs.mkdirSync('results', {recursive:true});
      fs.writeFileSync('results/linux.xml', ${JSON.stringify(passingXml)});
    `;
    const processResult = spawnSync(process.execPath, ['-e', operation, state], { cwd: workspace, encoding: 'utf8' });
    assert.equal(processResult.status, 0, processResult.stderr);
    assert.equal(readFileSync(join(workspace, 'invocations'), 'utf8'), 'validate\n');
    assert.equal(existsSync(cache), state !== 'unavailable');
    const receipt = recordOutcome(options(workspace), contract);
    assert.equal(receipt['work-status'], 'success');
    assert.equal(receipt.counts.tests, 1);
  }
});

test('actual process: cold and warm diagnostic logs require successful operations, not nonempty output', t => {
  const diagnosticContract = structuredClone(contract);
  const reports = [{ path: 'diagnostics/ruff-check-results.txt', kind: 'file' }];
  diagnosticContract.execution.checks.jobs.test = { 'required-steps': ['lint'], reports };
  for (const state of ['cold', 'warm']) {
    const workspace = fixture(t);
    if (state === 'warm') write(workspace, 'cache', 'available');
    const operation = `
      const fs = require('node:fs');
      fs.appendFileSync('invocations', 'lint\\n');
      if (!fs.existsSync('cache')) {
        fs.writeFileSync('cache', 'available');
        process.stdout.write('Preparing checker cache\\n');
      }
    `;
    const processResult = spawnSync(process.execPath, ['-e', operation],
      { cwd: workspace, encoding: 'utf8', timeout: 30_000 });
    assert.equal(processResult.status, 0, processResult.stderr);
    assert.equal(readFileSync(join(workspace, 'invocations'), 'utf8'), 'lint\n');
    assert.equal(processResult.stdout.length === 0, state === 'warm');
    write(workspace, reports[0].path, processResult.stdout);
    const input = options(workspace, { requiredSteps: ['lint'], reports,
      steps: { lint: { outcome: 'success', conclusion: 'success' } } });
    const receipt = recordOutcome(input, diagnosticContract);
    assert.equal(receipt['work-status'], 'success');
    assert.equal(receipt.counts.executed, 1);
    if (state === 'warm') {
      assert.equal(receipt.reports[0].files[0].bytes, 0);
      assert.equal(receipt.reports[0].files[0].sha256,
        'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855');
    }
    const published = publishOutcome(receipt, { workspace,
      outputDirectory: `.ci-outcome-downloads/${receipt['artifact-name']}` });
    assert.equal(readFileSync(join(published['artifact-path'], 'reports', reports[0].path), 'utf8'),
      processResult.stdout);
    const artifacts = readReceipts(join(workspace, '.ci-outcome-downloads'));
    const aggregate = aggregateOutcomes({ ...input, job: 'aggregate', shard: 'aggregate', target: 'aggregate',
      expectedShards: { test: ['linux'] }, needs: { test: { result: 'success' } },
      artifactRoots: artifacts.artifactRoots }, diagnosticContract, artifacts.children);
    assert.equal(aggregate['work-status'], 'success');
    input.steps.lint.outcome = 'failure';
    assert.equal(recordOutcome(input, diagnosticContract)['work-status'], 'failure');
    delete input.steps.lint;
    assert.equal(recordOutcome(input, diagnosticContract)['work-status'], 'failure');
    rmSync(join(workspace, reports[0].path));
    input.steps.lint = { outcome: 'success', conclusion: 'success' };
    assert.equal(recordOutcome(input, diagnosticContract)['work-status'], 'failure');
    assert.equal(readFileSync(join(workspace, 'invocations'), 'utf8'), 'lint\n');
  }
});

test('actual process: transient failure is recorded once and only a fresh attempt succeeds', t => {
  const workspace = fixture(t);
  const operation = `
    const fs = require('node:fs');
    const runs = fs.existsSync('invocations') ? Number(fs.readFileSync('invocations','utf8')) : 0;
    fs.writeFileSync('invocations', String(runs+1));
    fs.mkdirSync('results', {recursive:true});
    fs.writeFileSync('results/linux.xml', runs === 0 ?
      '<testsuite><testcase name="transient"><failure/></testcase></testsuite>' : ${JSON.stringify(passingXml)});
    process.exit(runs === 0 ? 1 : 0);
  `;
  const first = spawnSync(process.execPath, ['-e', operation], { cwd: workspace });
  assert.equal(first.status, 1);
  const firstInput = options(workspace);
  firstInput.steps.test.outcome = 'failure';
  const failed = recordOutcome(firstInput, contract);
  const published = publishOutcome(failed, { workspace });
  assert.equal(readFileSync(join(workspace, 'invocations'), 'utf8'), '1');
  assert.equal(failed['work-status'], 'failure');
  const second = spawnSync(process.execPath, ['-e', operation], { cwd: workspace });
  assert.equal(second.status, 0);
  const recovered = recordOutcome(options(workspace, { runAttempt: '2' }), contract);
  assert.equal(recovered['work-status'], 'success');
  assert.equal(readFileSync(join(workspace, 'invocations'), 'utf8'), '2');
  assert.equal(JSON.parse(readFileSync(join(published['artifact-path'], 'receipt.json'), 'utf8'))['work-status'], 'failure');
  assert.notEqual(failed['artifact-name'], recovered['artifact-name']);
});

test('actual process: persistent failure is not automatically retried or rewritten by publisher', t => {
  const workspace = fixture(t);
  const operation = `
    require('node:fs').appendFileSync('invocations', 'run\\n');
    process.exit(3);
  `;
  const result = spawnSync(process.execPath, ['-e', operation], { cwd: workspace });
  assert.equal(result.status, 3);
  const input = options(workspace);
  input.steps.test.outcome = 'failure';
  const receipt = recordOutcome(input, contract);
  publishOutcome(receipt, { workspace });
  assert.equal(readFileSync(join(workspace, 'invocations'), 'utf8'), 'run\n');
  assert.equal(receipt['work-status'], 'failure');
  assert.equal(receipt['first-failure'], 'test');
  assert.equal(receipt.counts.tests, 0);
});

test('composite: receipt always publishes before explicit failure and exposes artifact transport outputs', () => {
  const action = readFileSync(join(root, '.github', 'actions', 'ci-outcome', 'action.yml'), 'utf8');
  const steps = parseDocument(action).toJS().runs.steps;
  const resolver = steps.find(step => step.id === 'download-path');
  const download = steps.find(step => step.uses?.startsWith('actions/download-artifact@'));
  assert.ok(steps.indexOf(resolver) < steps.indexOf(download));
  assert.ok(steps.indexOf(download) < steps.findIndex(step => step.id === 'receipt'));
  assert.equal(resolver.if, "${{ always() && inputs.expected-shards != '' }}");
  assert.equal(resolver.env.INPUT_WORKFLOW, '${{ inputs.workflow }}');
  assert.equal(resolver.env.INPUT_EXPECTED_SHARDS, '${{ inputs.expected-shards }}');
  assert.equal(resolver['working-directory'], '${{ github.workspace }}');
  assert.equal(download.with.path, '${{ steps.download-path.outputs.path }}');
  assert.equal(download.with['merge-multiple'], false);
  assert.equal(download.if, "${{ always() && inputs.expected-shards != '' && steps.download-path.outcome == 'success' }}");
  assert.ok(action.indexOf('id: receipt') < action.indexOf('id: upload'));
  assert.ok(action.indexOf('id: upload') < action.indexOf('name: Enforce'));
  assert.match(action, /continue-on-error: true/);
  assert.match(action, /if-no-files-found: error/);
  assert.match(action, /merge-multiple: false/);
  assert.match(action, /steps\.upload\.outputs\.artifact-id/);
  assert.match(action, /steps\.upload\.outputs\.artifact-url/);
  assert.match(action, /working-directory: \$\{\{ github\.workspace \}\}/);
  assert.doesNotMatch(action, /retry/i);
});
