// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { cpSync, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { buildSummary, evaluateChecks, readReceiptTools, requiredLanes, visibleLanes } from '../../ci/evaluate-checks.mjs';
import { aggregateOutcomes, outcomeOutputs, publishOutcome, recordOutcome } from '../../ci/publish-outcome.mjs';
import { comparePaths, loadContract, parseChangedPaths, selectChecks, selectionOutputs } from '../../ci/select-checks.mjs';
import { readWorkflowGraph, validateWorkflows } from '../../ci/validate-workflows.mjs';

const root = fileURLToPath(new URL('../../../', import.meta.url));
const selectorPath = join(root, 'scripts/ci/select-checks.mjs');
const evaluatorPath = join(root, 'scripts/ci/evaluate-checks.mjs');
const validatorPath = join(root, 'scripts/ci/validate-workflows.mjs');
const contract = loadContract();
const graph = readWorkflowGraph(root);
const allSelectors = [
  'training', 'rl', 'il', 'vla', 'evaluation', 'osmo_replay', 'dm_tools', 'data_pipeline',
  'inference', 'gpu_offload', 'gpu_offload_e2e', 'shared_ci', 'dv_backend', 'dv_frontend',
  'fuzz', 'docusaurus', 'pester', 'containers',
];
const pythonConsumers = [
  'training', 'rl', 'il', 'vla', 'evaluation', 'osmo_replay', 'dm_tools', 'data_pipeline',
  'inference', 'gpu_offload', 'gpu_offload_e2e', 'shared_ci', 'dv_backend', 'fuzz',
];
const imageConsumers = ['rl', 'il', 'vla', 'evaluation', 'osmo_replay', 'dv_backend', 'containers'];
const cpuDomains = ['rl', 'il', 'vla', 'evaluation', 'vlm-judge', 'osmo-replay'];
const prPath = '.github/workflows/pr-validation.yml';
const mainPath = '.github/workflows/main.yml';
const docsPath = '.github/workflows/deploy-docs.yml';
const smokePath = '.github/workflows/smoke-cpu.yml';
const weeklyPath = '.github/workflows/weekly-validation.yml';
const summaryId = 'pr-validation-summary';
const unknownSha = '0'.repeat(40);

function expectedSelection(selected, strings = false) {
  return Object.fromEntries(allSelectors.map(key => [key, strings ? String(selected.includes(key)) : selected.includes(key)]));
}

function expectedReasons(selected, reason = 'path-or-dependency-match') {
  return JSON.stringify(Object.fromEntries(allSelectors.map(key => [key, selected.includes(key) ? reason : 'not-selected'])));
}

function executionOutputs(overrides = {}) {
  return {
    'work-status': 'success', 'expected-count': '2', 'executed-count': '2', 'test-count': '3',
    'skipped-count': '0', 'first-failure': '', 'artifact-id': '10',
    'artifact-url': 'https://github.com/microsoft/physical-ai-toolchain/actions/runs/123/artifacts/10',
    ...overrides,
  };
}

function assertSelection(actual, selected) {
  assert.deepEqual(actual, expectedSelection(selected));
}

function temporaryDirectory(t) {
  const directory = join(root, 'logs', 'ci-contract-fixtures', randomUUID());
  mkdirSync(directory, { recursive: true });
  t.after(() => rmSync(directory, { recursive: true, force: true, maxRetries: 8, retryDelay: 50 }));
  return directory;
}

function writeFixture(directory, path, content) {
  const destination = join(directory, path);
  mkdirSync(dirname(destination), { recursive: true });
  writeFileSync(destination, content, 'utf8');
}

function createRepository(t, files = {}) {
  const cwd = temporaryDirectory(t);
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.toUpperCase().startsWith('GIT_')));
  Object.assign(env, {
    GIT_CONFIG_NOSYSTEM: '1',
    GIT_CONFIG_GLOBAL: join(cwd, 'unused-global-config'),
    GIT_AUTHOR_NAME: 'CI Contract Tests',
    GIT_AUTHOR_EMAIL: 'ci-contract@example.invalid',
    GIT_COMMITTER_NAME: 'CI Contract Tests',
    GIT_COMMITTER_EMAIL: 'ci-contract@example.invalid',
    GIT_AUTHOR_DATE: '2026-01-01T00:00:00Z',
    GIT_COMMITTER_DATE: '2026-01-01T00:00:00Z',
    GIT_TERMINAL_PROMPT: '0',
  });
  const git = (...args) => {
    const result = spawnSync('git', [
      '-c', 'core.autocrlf=false', '-c', `core.hooksPath=${join(cwd, 'unused-hooks')}`, ...args,
    ], { cwd, env, encoding: 'utf8', timeout: 30_000 });
    assert.ifError(result.error);
    assert.equal(result.status, 0, `git ${args.join(' ')}: ${result.stderr}`);
    return result.stdout;
  };
  const commit = () => {
    git('add', '--all', '--', '.');
    git('commit', '--quiet', '--no-gpg-sign', '--allow-empty', '-m', 'CI fixture');
    return git('rev-parse', 'HEAD').trim();
  };
  git('init', '--quiet', '--initial-branch=main', '--object-format=sha1', '--template=');
  for (const [path, content] of Object.entries(files)) writeFixture(cwd, path, content);
  return { cwd, env, git, commit, base: commit() };
}

function runSelector(repo, base, head, output, args = []) {
  const result = spawnSync(process.execPath, [selectorPath, ...args], {
    cwd: repo.cwd,
    env: { ...repo.env, BASE_SHA: base, HEAD_SHA: head, GITHUB_OUTPUT: output },
    encoding: 'utf8',
    timeout: 30_000,
  });
  assert.ifError(result.error);
  return result;
}

const selectionCases = [
  ['root uv.lock selects every Python consumer', 'uv.lock', pythonConsumers],
  ['root pyproject selects every Python consumer', 'pyproject.toml', pythonConsumers],
  ['root conftest selects every Python consumer', 'tests/conftest.py', pythonConsumers],
  ['shared fixtures select every Python consumer', 'tests/fixtures/episodes/sample.json', pythonConsumers],
  ['docs source outside the site selects Docusaurus', 'docs/training/overview.md', ['docusaurus']],
  ['lint wrapper changes select every consumer', 'scripts/linting/Invoke-TerraformValidation.ps1', allSelectors],
  ['workflow changes select every consumer', '.github/workflows/python-lint.yml', allSelectors],
  ['local action changes select every consumer', '.github/actions/setup/action.yml', allSelectors],
  ['training image defaults also select training and fuzz tests', 'training/setup/defaults.conf', [...imageConsumers, 'training', 'fuzz']],
  ['shared shell defaults conservatively select every consumer', 'scripts/lib/common.sh', allSelectors],
  ['shared image scripts also select their own tests', 'shared/ci/smoke-image.sh', [...imageConsumers, 'shared_ci']],
  ['VLM judge selects evaluation and the viewer backend', 'evaluation/vlm_judge/judge.py', ['evaluation', 'dv_backend']],
  ['data-pipeline config selects its Python tests', 'data-pipeline/pyproject.toml', ['data_pipeline']],
  ['Terraform provider locks retain unconditional validation', 'infrastructure/terraform/.terraform.lock.hcl', []],
  ['unrelated data does not select conditional consumers', 'archives/episode.bin', []],
];

for (const [name, path, expected] of selectionCases) {
  test(`selector: ${name}`, () => assertSelection(selectChecks([path], contract), expected));
}

test('selector: full mode selects every consumer even without changed paths', () => {
  assertSelection(selectChecks([], contract, true), allSelectors);
});

const fileContent = 'Stable content for rename detection.\n'.repeat(40);
const gitCases = [
  { name: 'added path', initial: {}, path: 'docs/new guide.md', operation: 'add', expected: ['docusaurus'] },
  { name: 'modified path', initial: { 'docs/guide.md': fileContent }, path: 'docs/guide.md', operation: 'modify', expected: ['docusaurus'] },
  { name: 'deleted path', initial: { 'docs/guide.md': fileContent }, path: 'docs/guide.md', operation: 'delete', expected: ['docusaurus'] },
  { name: 'rename between consumers', initial: { 'docs/guide.md': fileContent }, path: 'docs/guide.md', to: 'data-pipeline/config.txt', expected: ['docusaurus', 'data_pipeline'] },
  { name: 'reverse rename between consumers', initial: { 'data-pipeline/config.txt': fileContent }, path: 'data-pipeline/config.txt', to: 'docs/guide.md', expected: ['docusaurus', 'data_pipeline'] },
  { name: 'rename where only the old path matches', initial: { 'docs/guide.md': fileContent }, path: 'docs/guide.md', to: 'archives/guide.txt', expected: ['docusaurus'] },
  { name: 'rename where only the new path matches', initial: { 'archives/guide.txt': fileContent }, path: 'archives/guide.txt', to: 'docs/guide.md', expected: ['docusaurus'] },
];

for (const fixture of gitCases) {
  test(`Git comparison: ${fixture.name}`, t => {
    const repo = createRepository(t, fixture.initial);
    if (fixture.to) {
      mkdirSync(dirname(join(repo.cwd, fixture.to)), { recursive: true });
      repo.git('mv', '--', fixture.path, fixture.to);
    } else if (fixture.operation === 'delete') {
      rmSync(join(repo.cwd, fixture.path));
    } else {
      writeFixture(repo.cwd, fixture.path, `${fileContent}Changed.\n`);
    }
    const head = repo.commit();
    const expectedPaths = fixture.to ? [fixture.path, fixture.to] : [fixture.path];
    assert.deepEqual(comparePaths(repo.base, head, repo.cwd), expectedPaths);
    if (fixture.to || fixture.operation === 'delete') assert.equal(existsSync(join(repo.cwd, fixture.path)), false);
    assert.deepEqual(selectionOutputs({ ...repo, head, contract }), {
      ...expectedSelection(fixture.expected, true),
      selection_reasons: expectedReasons(fixture.expected),
      selection_status: 'verified', base_sha: repo.base, head_sha: head, file_count: String(expectedPaths.length),
    });
  });
}

test('Git comparison: identical trees provide verified-empty evidence, not a failed comparison', t => {
  const repo = createRepository(t);
  const head = repo.commit();
  assert.notEqual(head, repo.base);
  assert.deepEqual(comparePaths(repo.base, repo.base, repo.cwd), []);
  assert.deepEqual(selectionOutputs({ ...repo, head, contract }), {
    ...expectedSelection([], true),
    selection_reasons: expectedReasons([]),
    selection_status: 'verified-empty', base_sha: repo.base, head_sha: head, file_count: '0',
  });
});

test('Git comparison: symbolic or option-like refs cannot replace exact SHAs', t => {
  const repo = createRepository(t);
  assert.throws(() => comparePaths('--help', repo.base, repo.cwd), /exact base and tested commit SHAs/);
  assert.throws(() => comparePaths(repo.base, 'HEAD', repo.cwd), /exact base and tested commit SHAs/);
});

test('Git comparison: unavailable commit objects fail closed', t => {
  const repo = createRepository(t);
  assert.throws(() => selectionOutputs({ ...repo, head: unknownSha, contract }), /Git comparison failed/);
});

for (const [name, base, head, message] of [
  ['invalid SHA', 'HEAD', unknownSha, 'Comparison requires exact base and tested commit SHAs'],
  ['failed Git comparison', unknownSha, unknownSha, 'Git comparison failed'],
]) {
  test(`selector CLI: ${name} writes no outputs`, t => {
    const repo = createRepository(t);
    const output = join(repo.cwd, 'github-output');
    const sentinel = 'existing_output=preserved\n';
    writeFileSync(output, sentinel);
    const result = runSelector(repo, base, head, output);
    assert.equal(result.status, 1);
    assert.equal(result.stdout, '');
    assert.ok(result.stderr.includes(message), result.stderr);
    assert.equal(readFileSync(output, 'utf8'), sentinel);
  });
}

test('selector CLI: committed changes emit complete matching JSON and GitHub outputs', t => {
  const repo = createRepository(t);
  writeFixture(repo.cwd, 'docs/new guide.md', fileContent);
  const head = repo.commit();
  const output = join(repo.cwd, 'github-output');
  const result = runSelector(repo, repo.base, head, output);
  assert.equal(result.status, 0, result.stderr);
  const expected = selectionOutputs({ ...repo, head, contract });
  assert.deepEqual(JSON.parse(result.stdout), expected);
  const rows = readFileSync(output, 'utf8').trimEnd().split('\n');
  assert.equal(rows.length, Object.keys(expected).length);
  assert.deepEqual(Object.fromEntries(rows.map(row => row.split('='))), expected);
});

test('selector CLI: main full mode needs no repository or comparison SHAs', t => {
  const cwd = temporaryDirectory(t);
  const output = join(cwd, 'github-output');
  const result = runSelector({ cwd, env: process.env }, '', '', output, ['--full']);
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(result.stdout), {
    ...expectedSelection(allSelectors, true),
    selection_reasons: expectedReasons(allSelectors, 'full-run'),
    selection_status: 'full', base_sha: '', head_sha: '', file_count: '0',
  });
});

test('Git parser: an empty NUL stream has no paths', () => {
  assert.deepEqual(parseChangedPaths(''), []);
});

test('Git parser: NUL delimiters preserve spaces, tabs, newlines, and Unicode', () => {
  const paths = ['docs/a guide.md', 'docs/line\nbreak.md', 'docs/tab\tname.md', 'docs/로봇.md'];
  assert.deepEqual(parseChangedPaths(paths.map(path => `M\0${path}\0`).join('')), paths);
});

test('Git parser: rename and copy include both paths and deduplicate without losing order', () => {
  const stream = ['R100', 'docs/old.md', 'archives/new.txt', 'C075', 'archives/new.txt', 'data-pipeline/copy.txt', 'D', 'docs/old.md', ''].join('\0');
  assert.deepEqual(parseChangedPaths(stream), ['docs/old.md', 'archives/new.txt', 'data-pipeline/copy.txt']);
});

for (const [name, stream] of [
  ['truncated final field', 'M\0docs/page.md'],
  ['unsupported status', 'Q\0docs/page.md\0'],
  ['missing path', 'M\0'],
  ['missing rename destination', 'R100\0docs/old.md\0'],
  ['out-of-range similarity score', 'R101\0docs/old.md\0docs/new.md\0'],
]) {
  test(`Git parser: rejects ${name}`, () => {
    assert.throws(() => parseChangedPaths(stream), Error, `Invalid Git record accepted: ${JSON.stringify(stream)}`);
  });
}

test('workflow graph: actual parsed repository and declared invariants pass', () => {
  assert.deepEqual(validateWorkflows(graph, contract), []);
  assert.deepEqual(graph[smokePath].jobs['import-smoke'].strategy.matrix.domain, cpuDomains);
  for (const [owner, path, aggregate] of [['pr', prPath, summaryId], ['main', mainPath, 'main-validation-summary']]) {
    assert.deepEqual([...graph[path].jobs[aggregate].needs].sort(), visibleLanes(contract, owner).map(lane => lane.owners[owner]).sort());
  }
  const osv = contract.lanes.find(lane => lane.id === 'osv-scanner');
  assert.equal(osv.classification, 'advisory');
  assert.equal(osv.outcomeSchema, 'advisory');
  assert.equal(graph[prPath].jobs[summaryId].needs.includes('osv-scanner'), true);
  assert.deepEqual(Object.keys(graph[docsPath].on).sort(), ['push', 'workflow_dispatch']);
  assert.equal(graph[docsPath].jobs.test.uses, './.github/workflows/docusaurus-tests.yml');
  assert.equal(graph[docsPath].jobs.build.needs, 'test');
  assert.equal(graph[docsPath].jobs.deploy.needs, 'build');
});

function writeActionGraph(cwd) {
  writeFixture(cwd, '.github/workflows/test.yml', 'on: workflow_call\njobs:\n  check:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: ./.github/actions/outer\n');
  writeFixture(cwd, '.github/actions/outer/action.yml', 'name: Outer\nruns:\n  using: composite\n  steps:\n    - uses: ./.github/actions/inner\n');
}

test('workflow reader: rejects a composite action cycle with its traversal chain', t => {
  const cwd = temporaryDirectory(t);
  writeActionGraph(cwd);
  writeFixture(cwd, '.github/actions/inner/action.yaml', 'name: Inner\nruns:\n  using: composite\n  steps:\n    - uses: ./.github/actions/outer\n');
  assert.throws(() => readWorkflowGraph(cwd), {
    message: 'Cyclic local action reference: .github/actions/outer/action.yml -> .github/actions/inner/action.yaml -> .github/actions/outer/action.yml',
  });
});

test('workflow reader: recursively parses acyclic local composite action references', t => {
  const cwd = temporaryDirectory(t);
  writeActionGraph(cwd);
  writeFixture(cwd, '.github/actions/inner/action.yaml', 'name: Inner\nruns:\n  using: composite\n  steps:\n    - run: echo done\n      shell: bash\n');
  const actual = readWorkflowGraph(cwd);
  assert.deepEqual(Object.keys(actual).sort(), [
    '.github/actions/inner/action.yaml', '.github/actions/outer/action.yml', '.github/workflows/test.yml',
  ]);
  assert.equal(actual['.github/actions/outer/action.yml'].runs.steps[0].uses, './.github/actions/inner');
  assert.equal(actual['.github/actions/inner/action.yaml'].runs.steps[0].run, 'echo done');
});

test('workflow reader: rejects a direct composite self-cycle', t => {
  const cwd = temporaryDirectory(t);
  writeActionGraph(cwd);
  writeFixture(cwd, '.github/actions/inner/action.yaml', 'name: Inner\nruns:\n  using: composite\n  steps:\n    - uses: ./.github/actions/inner\n');
  assert.throws(() => readWorkflowGraph(cwd), {
    message: 'Cyclic local action reference: .github/actions/outer/action.yml -> .github/actions/inner/action.yaml -> .github/actions/inner/action.yaml',
  });
});

test('workflow reader: accepts shared descendants and repeated actions across jobs and workflows', t => {
  const cwd = temporaryDirectory(t);
  writeFixture(cwd, '.github/workflows/test.yml', 'on: workflow_call\njobs:\n  first:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: ./.github/actions/outer-a\n      - uses: ./.github/actions/outer-b\n      - uses: ./.github/actions/outer-a\n  second:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: ./.github/actions/outer-b\n');
  writeFixture(cwd, '.github/workflows/reuse.yml', 'on: workflow_call\njobs:\n  check:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: ./.github/actions/outer-a\n');
  for (const name of ['outer-a', 'outer-b']) {
    writeFixture(cwd, `.github/actions/${name}/action.yml`, `name: ${name}\nruns:\n  using: composite\n  steps:\n    - uses: ./.github/actions/shared\n`);
  }
  writeFixture(cwd, '.github/actions/shared/action.yaml', 'name: Shared\nruns:\n  using: composite\n  steps:\n    - run: echo shared\n      shell: bash\n');
  const actual = readWorkflowGraph(cwd);
  assert.deepEqual(Object.keys(actual).sort(), [
    '.github/actions/outer-a/action.yml', '.github/actions/outer-b/action.yml', '.github/actions/shared/action.yaml',
    '.github/workflows/reuse.yml', '.github/workflows/test.yml',
  ]);
  for (const name of ['outer-a', 'outer-b']) {
    assert.equal(actual[`.github/actions/${name}/action.yml`].runs.steps[0].uses, './.github/actions/shared');
  }
  assert.equal(actual['.github/actions/shared/action.yaml'].runs.steps[0].run, 'echo shared');
});

test('workflow reader: excludes completed siblings from a later cycle diagnostic', t => {
  const cwd = temporaryDirectory(t);
  writeActionGraph(cwd);
  writeFixture(cwd, '.github/actions/outer/action.yml', 'name: Outer\nruns:\n  using: composite\n  steps:\n    - uses: ./.github/actions/shared\n    - uses: ./.github/actions/inner\n');
  writeFixture(cwd, '.github/actions/shared/action.yml', 'name: Shared\nruns:\n  using: node24\n  main: index.js\n');
  writeFixture(cwd, '.github/actions/inner/action.yaml', 'name: Inner\nruns:\n  using: composite\n  steps:\n    - uses: ./.github/actions/outer\n');
  assert.throws(() => readWorkflowGraph(cwd), {
    message: 'Cyclic local action reference: .github/actions/outer/action.yml -> .github/actions/inner/action.yaml -> .github/actions/outer/action.yml',
  });
});

test('workflow reader: propagates malformed nested action YAML', t => {
  const cwd = temporaryDirectory(t);
  writeActionGraph(cwd);
  writeFixture(cwd, '.github/actions/inner/action.yaml', 'name: Inner\nname: Duplicate\n');
  assert.throws(() => readWorkflowGraph(cwd), /inner\/action\.yaml: Map keys must be unique/);
});

test('workflow reader: rejects an unresolved nested local action', t => {
  const cwd = temporaryDirectory(t);
  writeActionGraph(cwd);
  assert.throws(() => readWorkflowGraph(cwd), /Unresolved local action: \.\/\.github\/actions\/inner/);
});

for (const on of [{ schedule: [{ cron: '0 9 * * 1' }], workflow_dispatch: null }, 'workflow_dispatch', ['schedule', 'workflow_dispatch']]) {
  test(`secondary callers: explicitly declared weekly events pass (${JSON.stringify(on)})`, () => {
    const candidate = { graph: structuredClone(graph), contract: structuredClone(contract) };
    assert.deepEqual(candidate.contract.secondaryCallers[weeklyPath].jobs, {
      'msdate-freshness': 'msdate-freshness-check.yml', 'container-rescan': 'container-scan.yml',
    });
    candidate.graph[weeklyPath].on = on;
    candidate.contract.secondaryCallers[weeklyPath].events = typeof on === 'string' ? [on] : Array.isArray(on) ? on : Object.keys(on);
    assert.deepEqual(validateWorkflows(candidate.graph, candidate.contract), []);
  });
}

test('secondary callers: callable weekly root preserves its own declared reuse', () => {
  const candidate = structuredClone(graph);
  candidate[weeklyPath].on.workflow_call = {};
  assert.deepEqual(validateWorkflows(candidate, contract), []);
});

test('secondary callers: a separately declared caller passes without a validator-specific exception', () => {
  const candidate = { graph: structuredClone(graph), contract: structuredClone(contract) };
  const caller = '.github/workflows/manual-validation.yml';
  candidate.graph[caller] = { on: 'workflow_dispatch', jobs: { check: { uses: './.github/workflows/spell-check.yml' } } };
  candidate.contract.secondaryCallers[caller] = { events: ['workflow_dispatch'], jobs: { check: 'spell-check.yml' } };
  assert.deepEqual(validateWorkflows(candidate.graph, candidate.contract), []);
});

test('secondary callers: pure reusable workflows without an executable root are not event owners', () => {
  const candidate = structuredClone(graph);
  candidate['.github/workflows/unused-wrapper.yml'] = {
    on: 'workflow_call', jobs: { check: { uses: './.github/workflows/spell-check.yml' } },
  };
  assert.deepEqual(validateWorkflows(candidate, contract), []);
});

for (const on of [
  { workflow_dispatch: {} }, { schedule: [{ cron: '0 9 * * 1' }] },
  { workflow_run: { workflows: ['CI'], types: ['completed'] } }, { merge_group: {} },
  'workflow_dispatch', ['workflow_call', 'workflow_dispatch'], { workflow_call: {}, workflow_dispatch: {} },
]) {
  test(`secondary callers: undeclared executable root is rejected (${JSON.stringify(on)})`, () => {
    const candidate = structuredClone(graph);
    const caller = '.github/workflows/undeclared.yml';
    candidate[caller] = { on, jobs: { check: { uses: './.github/workflows/spell-check.yml' } } };
    const errors = validateWorkflows(candidate, contract);
    assert.ok(errors.some(error => error.includes(`${caller}:check`) && error.includes('spell-check.yml') && error.includes('duplicate event ownership')), JSON.stringify(errors));
  });
}

test('secondary callers: nested rejection identifies the executable root and immediate caller', () => {
  const candidate = structuredClone(graph);
  candidate['.github/workflows/undeclared.yml'] = {
    on: { workflow_dispatch: {} }, jobs: { nested: { uses: './.github/workflows/wrapper.yml' } },
  };
  candidate['.github/workflows/wrapper.yml'] = {
    on: 'workflow_call', jobs: { check: { uses: './.github/workflows/spell-check.yml' } },
  };
  const errors = validateWorkflows(candidate, contract);
  assert.ok(errors.some(error => error.includes('undeclared.yml') && error.includes('wrapper.yml:check') && error.includes('spell-check.yml')), JSON.stringify(errors));
});

test('secondary callers: an undeclared root cannot inherit a weekly caller permission', () => {
  const candidate = structuredClone(graph);
  candidate[weeklyPath].on.workflow_call = {};
  candidate['.github/workflows/undeclared.yml'] = {
    on: 'workflow_dispatch', jobs: { nested: { uses: `./${weeklyPath}` } },
  };
  const errors = validateWorkflows(candidate, contract);
  assert.ok(errors.some(error => error.includes('undeclared.yml') && error.includes(`${weeklyPath}:container-rescan`) && error.includes('container-scan.yml')), JSON.stringify(errors));
});

test('secondary callers: workflow cycles from manual roots still fail', () => {
  const candidate = structuredClone(graph);
  candidate['.github/workflows/cycle.yml'] = {
    on: { workflow_dispatch: {}, workflow_call: {} }, jobs: { again: { uses: './.github/workflows/cycle.yml' } },
  };
  assert.ok(validateWorkflows(candidate, contract).some(error => error.includes('Cyclic local workflow call: .github/workflows/cycle.yml')));
});

test('secondary callers: an undeclared root cannot inherit primary ownership', () => {
  const candidate = structuredClone(graph);
  candidate[mainPath].on.workflow_call = {};
  candidate['.github/workflows/undeclared.yml'] = {
    on: 'workflow_dispatch', jobs: { nested: { uses: `./${mainPath}` } },
  };
  const errors = validateWorkflows(candidate, contract);
  assert.ok(errors.some(error => error.includes('undeclared.yml') && error.includes(`${mainPath}:spell-check`) && error.includes('duplicate event ownership')), JSON.stringify(errors));
});

test('secondary callers: allowed targets do not stop descendant ownership checks', () => {
  const candidate = structuredClone(graph);
  candidate['.github/workflows/container-scan.yml'].jobs.nested = { uses: './.github/workflows/spell-check.yml' };
  const errors = validateWorkflows(candidate, contract);
  assert.ok(errors.some(error => error.includes(weeklyPath) && error.includes('container-scan.yml:nested') && error.includes('spell-check.yml')), JSON.stringify(errors));
});

for (const [name, mutate, diagnostic] of [
  ['missing policy', candidate => { delete candidate.contract.secondaryCallers; }, 'secondaryCallers must be an object'],
  ['null policy', candidate => { candidate.contract.secondaryCallers = null; }, 'secondaryCallers must be an object'],
  ['array policy', candidate => { candidate.contract.secondaryCallers = []; }, 'secondaryCallers must be an object'],
  ['null declaration', candidate => { candidate.contract.secondaryCallers[weeklyPath] = null; }, 'invalid secondary caller declaration'],
  ['missing caller', candidate => { delete candidate.graph[weeklyPath]; }, 'missing secondary caller workflow'],
  ['non-workflow caller', candidate => { candidate.contract.secondaryCallers['.github/actions/setup-node-deps/action.yml'] = candidate.contract.secondaryCallers[weeklyPath]; }, 'invalid secondary caller path'],
  ['scalar events', candidate => { candidate.contract.secondaryCallers[weeklyPath].events = 'workflow_dispatch'; }, 'secondary caller events must be a nonempty array'],
  ['empty events', candidate => { candidate.contract.secondaryCallers[weeklyPath].events = []; }, 'secondary caller events must be a nonempty array'],
  ['duplicate events', candidate => { candidate.contract.secondaryCallers[weeklyPath].events.push('schedule'); }, 'secondary caller events must be unique'],
  ['non-string event', candidate => { candidate.contract.secondaryCallers[weeklyPath].events = [null]; }, 'secondary caller events must be a nonempty array'],
  ['workflow_call permission', candidate => { candidate.contract.secondaryCallers[weeklyPath].events.push('workflow_call'); }, 'workflow_call is not a secondary caller event'],
  ['event drift', candidate => { candidate.graph[weeklyPath].on.push = {}; }, 'secondary caller event mismatch'],
  ['non-executable caller', candidate => { candidate.graph[weeklyPath].on = 'workflow_call'; }, 'secondary caller event mismatch'],
  ['missing jobs', candidate => { delete candidate.contract.secondaryCallers[weeklyPath].jobs; }, 'secondary caller jobs must be a nonempty object'],
  ['empty jobs', candidate => { candidate.contract.secondaryCallers[weeklyPath].jobs = {}; }, 'secondary caller jobs must be a nonempty object'],
  ['array jobs', candidate => { candidate.contract.secondaryCallers[weeklyPath].jobs = []; }, 'secondary caller jobs must be a nonempty object'],
  ['missing job', candidate => { delete candidate.graph[weeklyPath].jobs['container-rescan']; }, 'secondary caller target mismatch'],
  ['unprotected target', candidate => { candidate.contract.secondaryCallers[weeklyPath].jobs['container-rescan'] = 'create-stale-docs-issues.yml'; }, 'unknown secondary caller target'],
  ['non-string target', candidate => { candidate.contract.secondaryCallers[weeklyPath].jobs['container-rescan'] = null; }, 'unknown secondary caller target'],
  ['target substitution', candidate => { candidate.graph[weeklyPath].jobs['container-rescan'].uses = './.github/workflows/spell-check.yml'; }, 'secondary caller target mismatch'],
  ['undeclared weekly job', candidate => { candidate.graph[weeklyPath].jobs.duplicate = { uses: './.github/workflows/container-scan.yml' }; }, 'weekly-validation.yml:duplicate: duplicate event ownership'],
  ['removed weekly exception', candidate => { delete candidate.contract.secondaryCallers[weeklyPath].jobs['container-rescan']; }, 'weekly-validation.yml:container-rescan: duplicate event ownership'],
]) {
  test(`secondary callers: rejects ${name}`, () => {
    const candidate = { graph: structuredClone(graph), contract: structuredClone(contract) };
    mutate(candidate);
    const errors = validateWorkflows(candidate.graph, candidate.contract);
    assert.ok(errors.some(error => error.includes(diagnostic)), `Expected ${diagnostic}; received ${JSON.stringify(errors)}`);
  });
}

const graphMutations = [
  ['main summary no always', ({ main }) => { delete main['main-validation-summary'].if; }, 'Required summary must always execute'],
  ['main evaluator wrong owner', ({ main }) => { main['main-validation-summary'].steps.find(step => step.env?.NEEDS_JSON).env.CI_OWNER = 'pr'; }, 'unconditional canonical evaluator'],
  ['summary receipt directory omitted', ({ summary }) => { delete summary.steps.find(step => step.env?.NEEDS_JSON).env.CI_RECEIPTS_DIRECTORY; }, 'summary comparison and receipt context mismatch'],
  ['main comparison head replaced', ({ main }) => { main['main-validation-summary'].steps.find(step => step.env?.NEEDS_JSON).env.HEAD_SHA = 'HEAD'; }, 'summary comparison and receipt context mismatch'],
  ['summary cancellation context omitted', ({ summary }) => { delete summary.steps.find(step => step.env?.NEEDS_JSON).env.CI_CANCELLED; }, 'explicit workflow cancellation context'],
  ['main cancellation context forced false', ({ main }) => { main['main-validation-summary'].steps.find(step => step.env?.NEEDS_JSON).env.CI_CANCELLED = 'false'; }, 'explicit workflow cancellation context'],
  ['cancellation capture condition inverted', ({ summary }) => { summary.steps.find(step => step.id === 'cancellation').if = '!cancelled()'; }, 'explicit workflow cancellation context'],
  ['cancellation capture falsifies output', ({ summary }) => { summary.steps.find(step => step.id === 'cancellation').run = 'echo cancelled=false'; }, 'explicit workflow cancellation context'],
  ['release bypasses complete main gate', ({ main }) => { main['release-please'].needs = ['workflow-refs-check']; }, 'depend only on the main validation summary'],
  ['PR concurrency missing workflow identity', ({ graph }) => { graph[prPath].concurrency.group = 'pr-${{ github.event.pull_request.number }}'; }, 'PR concurrency must isolate'],
  ['PR concurrency missing cancellation', ({ graph }) => { graph[prPath].concurrency['cancel-in-progress'] = false; }, 'PR concurrency must isolate'],
  ['main global cancellation', ({ graph }) => { graph[mainPath].concurrency = { group: 'main', 'cancel-in-progress': true }; }, 'Main and release cancellation is forbidden'],
  ['release cancellation', ({ main }) => { main['release-please'].concurrency['cancel-in-progress'] = true; }, 'Main and release cancellation is forbidden'],
  ['summary timeout removed', ({ summary }) => { delete summary['timeout-minutes']; }, 'summary timeout exceeds budget'],
  ['summary timeout unbounded', ({ summary }) => { summary['timeout-minutes'] = 60; }, 'summary timeout exceeds budget'],
  ['discovery timeout removed', ({ pr }) => { delete pr.changes['timeout-minutes']; }, 'Discovery and advisory jobs require bounded timeouts'],
  ['summary artifact publication removed', ({ summary }) => { summary.steps = summary.steps.filter(step => !step.uses?.startsWith('actions/upload-artifact@')); }, 'summary artifacts must always publish'],
  ['advisory visibility removed', ({ summary }) => { summary.needs = summary.needs.filter(id => id !== 'terraform-security'); }, 'aggregate must include exactly all mandatory jobs'],
  ['main full-run declaration absent', ({ main }) => { delete main['go-tests'].with['changed-files-only']; }, 'explicitly disable it'],
  ['whole-step tolerated lane promoted', ({ contract }) => { contract.lanes.find(lane => lane.id === 'terraform-tests').classification = 'mandatory'; }, 'whole-step tolerated execution must remain advisory'],
  ['container execution made advisory', ({ contract }) => { contract.lanes.find(lane => lane.id === 'container-scan').classification = 'advisory'; }, 'execution remains mandatory'],
  ['execution contract missing', ({ contract }) => { delete contract.execution; }, 'Missing execution contract'],
  ['execution metadata missing workflow', ({ contract }) => { delete contract.execution['python-lint']; }, 'missing execution job metadata'],
  ['execution metadata stale workflow', ({ contract }) => { contract.execution.stale = { jobs: {} }; }, 'stale execution contract'],
  ['missing workflow_call', ({ graph }) => { delete graph['.github/workflows/python-lint.yml'].on.workflow_call; }, 'missing workflow_call'],
  ['unresolved nested local workflow', ({ graph }) => {
    graph[smokePath].jobs.nested = { uses: './.github/workflows/missing-contract-fixture.yml' };
  }, 'unresolved local call'],
  ['duplicate event owner through a separate workflow wrapper', ({ graph, pr }) => {
    graph['.github/workflows/wrapper.yml'] = { on: { workflow_call: {} }, jobs: { duplicate: structuredClone(pr['spell-check']) } };
    graph['.github/workflows/duplicate-owner.yml'] = { on: { push: {} }, jobs: { wrapper: { uses: './.github/workflows/wrapper.yml' } } };
  }, 'duplicate event ownership'],
  ['new duplicate call in the PR owner', ({ pr }) => { pr.duplicate = structuredClone(pr['spell-check']); }, 'duplicate event ownership'],
  ['push trigger on a reusable validation', ({ graph }) => { graph['.github/workflows/python-lint.yml'].on.push = {}; }, 'duplicate event ownership (push)'],
  ['PR trigger on a reusable validation', ({ graph }) => { graph['.github/workflows/python-lint.yml'].on.pull_request = {}; }, 'duplicate event ownership (pull_request)'],
  ['inline owned validation implementation', ({ pr }) => {
    delete pr['python-lint'].uses;
    pr['python-lint']['runs-on'] = 'ubuntu-latest';
    pr['python-lint'].steps = [{ run: 'ruff check .' }];
  }, 'inline duplication or incorrect reusable target'],
  ['inline validation inside the discovery exception', ({ pr }) => { pr.changes.steps.push({ run: 'npm run lint:yaml' }); }, 'inline duplication of validation logic'],
  ['missing mandatory PR aggregate need', ({ summary }) => { summary.needs = summary.needs.filter(id => id !== 'go-tests'); }, 'aggregate must include exactly all mandatory jobs'],
  ['missing mandatory main aggregate need', ({ main }) => { main['main-validation-summary'].needs = main['main-validation-summary'].needs.filter(id => id !== 'uv-lock-consistency'); }, 'aggregate must include exactly all mandatory jobs'],
  ['caller permission escalation', ({ pr }) => { pr['python-lint'].permissions.contents = 'write'; }, 'permission profile mismatch'],
  ['substitution with a different callable target', ({ pr }) => { pr['python-lint'].uses = './.github/workflows/shellcheck.yml'; }, 'incorrect reusable target'],
  ['secondary PR changed-file filter', ({ pr }) => { pr['terraform-validation'].with['changed-files-only'] = true; }, 'secondary changed-file filter'],
  ['secondary main changed-file filter', ({ main }) => { main['pester-tests'].with['changed-files-only'] = true; }, 'secondary changed-file filter'],
  ['CPU domain drift', ({ cpu }) => { cpu.strategy.matrix.domain = cpu.strategy.matrix.domain.filter(domain => domain !== 'vla'); }, 'CPU smoke domain matrix changed'],
  ['boolean-false CPU smoke condition', ({ cpu }) => { cpu.if = false; }, 'CPU smoke must remain unconditional'],
  ['PR head substituted for event base SHA', ({ pr }) => { pr.changes.steps.find(step => step.id === 'filter').env.BASE_SHA = '${{ github.event.pull_request.head.sha }}'; }, 'event base SHA to tested merge SHA'],
  ['PR head substituted for tested merge SHA', ({ pr }) => { pr.changes.steps.find(step => step.id === 'filter').env.HEAD_SHA = '${{ github.event.pull_request.head.sha }}'; }, 'event base SHA to tested merge SHA'],
  ['renamed required summary job ID', ({ pr, summary }) => { pr['renamed-summary'] = summary; delete pr[summaryId]; }, 'Missing owned job'],
  ['renamed required summary check context', ({ summary }) => { summary.name = 'Different Required Check'; }, 'check context must remain stable'],
  ['required summary without always', ({ summary }) => { summary.if = 'success()'; }, 'Required summary must always execute'],
  ['summary bypassing the evaluator', ({ summary }) => {
    summary.steps.find(step => step.env?.NEEDS_JSON).run = 'node scripts/ci/select-checks.mjs';
  }, 'unconditional canonical evaluator'],
  ['main VLA image smoke disabled', ({ main }) => { main.smoke.with.vla = false; }, 'Main must run smoke input: vla'],
  ['OSV scanner made blocking', ({ pr }) => {
    pr['osv-scanner'].steps.find(step => step.uses?.startsWith('google/osv-scanner-action/'))['continue-on-error'] = false;
  }, 'OSV must remain advisory'],
  ['OSV advisory scan outcome hidden', ({ pr }) => { pr['osv-scanner'].outputs['work-status'] = '${{ steps.osv-scan.conclusion }}'; }, 'OSV advisory summary must expose the scan outcome'],
  ['docs chained to another workflow instead of independent push', ({ docs }) => {
    delete docs.on.push;
    docs.on.workflow_run = { workflows: ['CI'], types: ['completed'] };
  }, 'Documentation deployment must remain independent'],
  ['missing selector output wiring', ({ pr }) => { delete pr.changes.outputs.dv_backend; }, 'Missing selector output: dv_backend'],
  ['inverted selection allowing an unexpected skip', ({ pr }) => { pr['docusaurus-tests'].if = "needs.changes.outputs.docusaurus == 'false'"; }, 'selector condition mismatch'],
  ['required outcome schema permitting selected skips', ({ contract }) => { contract.outcomeSchemas.required.selected.push('skipped'); }, 'Required outcomes must fail closed'],
  ['OSV promoted to mandatory by contract and aggregate', ({ contract, summary }) => {
    const osv = contract.lanes.find(lane => lane.id === 'osv-scanner');
    osv.classification = 'mandatory';
    osv.outcomeSchema = 'required';
    summary.needs.push('osv-scanner');
  }, 'OSV must remain advisory'],
  ['summary omitting complete needs evidence', ({ summary }) => {
    summary.steps.find(step => step.env?.NEEDS_JSON).env.NEEDS_JSON = '${{ toJSON(needs.changes) }}';
  }, 'complete needs evidence'],
  ['summary evaluator condition', ({ summary }) => {
    summary.steps.find(step => step.env?.NEEDS_JSON).if = 'false';
  }, 'unconditional canonical evaluator'],
  ['summary evaluator failure suppression', ({ summary }) => {
    summary.steps.find(step => step.env?.NEEDS_JSON)['continue-on-error'] = true;
  }, 'unconditional canonical evaluator'],
  ['release coordinator always condition', ({ main }) => { main['release-please'].if = 'always()'; }, 'Release coordinator must retain success gating'],
  ['release coordinator failure suppression', ({ main }) => { main['release-please']['continue-on-error'] = true; }, 'Release coordinator must retain success gating'],
  ['contract test step condition', ({ graph }) => {
    graph['.github/workflows/workflow-refs-check.yml'].jobs['workflow-refs-check'].steps.find(step => step.run === 'npm run test:ci').if = 'false';
  }, 'unconditional canonical command: npm run test:ci'],
  ['contract job prerequisite', ({ graph }) => {
    graph['.github/workflows/workflow-refs-check.yml'].jobs.setup = { 'runs-on': 'ubuntu-latest', steps: [] };
    graph['.github/workflows/workflow-refs-check.yml'].jobs['workflow-refs-check'].needs = 'setup';
  }, 'CI contract job must remain present and unconditional'],
  ['hard-fail caller escalation', ({ pr }) => { pr['go-tests'].with['soft-fail'] = true; }, 'effective soft-fail policy mismatch'],
  ['callee-default escalation', ({ graph }) => { graph['.github/workflows/python-lint.yml'].on.workflow_call.inputs['soft-fail'].default = true; }, 'effective soft-fail policy mismatch'],
  ['malformed caller soft-fail expression', ({ pr }) => { pr['go-tests'].with['soft-fail'] = '${{ always() }}'; }, 'soft-fail must resolve from a boolean'],
  ['malformed callee soft-fail default', ({ graph }) => { graph['.github/workflows/go-tests.yml'].on.workflow_call.inputs['soft-fail'].default = 'false'; }, 'soft-fail must resolve from a boolean'],
  ['missing callee soft-fail declaration', ({ graph }) => { delete graph['.github/workflows/go-tests.yml'].on.workflow_call.inputs['soft-fail']; }, 'soft-fail policy declaration mismatch'],
  ['missing declared soft-fail policy', ({ contract }) => { delete contract.lanes.find(lane => lane.id === 'go-tests').softFailPolicy; }, 'soft-fail policy declaration mismatch'],
  ['duplicate contract lane ownership', ({ contract }) => { contract.lanes.push(structuredClone(contract.lanes.find(lane => lane.id === 'python-lint'))); }, 'Duplicate job ownership'],
  ['mandatory lane assigned advisory outcomes', ({ contract }) => { contract.lanes.find(lane => lane.id === 'python-lint').outcomeSchema = 'advisory'; }, 'mandatory outcomes cannot be advisory'],
];

for (const [name, mutate, diagnostic] of graphMutations) {
  test(`workflow mutation: rejects ${name}`, () => {
    const candidate = { graph: structuredClone(graph), contract: structuredClone(contract) };
    assert.deepEqual(validateWorkflows(candidate.graph, candidate.contract), [], 'Original graph must be valid');
    const pr = candidate.graph[prPath].jobs;
    mutate({
      ...candidate, pr, main: candidate.graph[mainPath].jobs, docs: candidate.graph[docsPath],
      summary: pr[summaryId], cpu: candidate.graph[smokePath].jobs['import-smoke'],
    });
    const errors = validateWorkflows(candidate.graph, candidate.contract);
    assert.ok(errors.length > 0, `Validator accepted invalid mutation: ${name}`);
    assert.ok(errors.some(error => error.includes(diagnostic)), `Expected ${diagnostic}; received ${JSON.stringify(errors)}`);
  });
}

const executionMutations = [
  ['missing reusable output', ({ workflow }) => { delete workflow.on.workflow_call.outputs['work-status']; }, 'broken reusable output work-status'],
  ['constant reusable output', ({ workflow }) => { workflow.on.workflow_call.outputs['expected-count'].value = '1'; }, 'broken reusable output expected-count'],
  ['incorrect job output source', ({ job }) => { job.outputs['executed-count'] = '${{ steps.fake.outputs.executed-count }}'; }, 'broken producer output executed-count'],
  ['deleted evidence producer', ({ job, producer }) => { job.steps = job.steps.filter(step => step !== producer); }, 'missing unique evidence producer'],
  ['evidence condition suppression', ({ producer }) => { producer.if = 'success()'; }, 'evidence publication must always execute'],
  ['evidence failure suppression', ({ producer }) => { producer['continue-on-error'] = true; }, 'evidence publication must always execute'],
  ['producer implementation replacement', ({ producer }) => { producer.uses = 'actions/upload-artifact@fake'; }, 'missing unique evidence producer'],
  ['missing declared operation', ({ producer }) => { producer.with['required-steps'] = '[]'; }, 'required operation declaration mismatch'],
  ['malformed declared operation', ({ producer }) => { producer.with['required-steps'] = '{'; }, 'required operation declaration mismatch'],
  ['removed operation implementation', ({ job, metadata }) => { job.steps = job.steps.filter(step => step.id !== metadata['required-steps'][0]); }, 'required operation implementation mismatch'],
  ['substituted operation implementation', ({ job, metadata }) => { const step = job.steps.find(step => step.id === metadata['required-steps'][0]); delete step.uses; step.run = 'echo bypass'; }, 'required operation implementation mismatch'],
  ['conditional operation', ({ job, metadata }) => { job.steps.find(step => step.id === metadata['required-steps'][0]).if = 'false'; }, 'unexpected operation condition'],
  ['producer before operations', ({ job, producer }) => { job.steps = [producer, ...job.steps.filter(step => step !== producer)]; }, 'evidence precedes operation'],
  ['partial step context', ({ producer }) => { producer.with['steps-json'] = '${{ toJSON(steps.checkout) }}'; }, 'incorrect producer identity or step context'],
  ['wrong producing workflow', ({ producer }) => { producer.with.workflow = 'other'; }, 'incorrect producer identity or step context'],
  ['unexpected target substitution', ({ producer }) => { producer.with.target = 'other'; }, 'shard or target identity mismatch'],
  ['evidence disables required failures', ({ producer }) => { producer.with['fail-on-error'] = 'false'; }, 'required producer cannot suppress evidence failure'],
  ['selected work forced skip', ({ producer }) => { producer.with.selected = 'false'; }, 'selected execution cannot become a planned skip'],
  ['record replaced by aggregation', ({ producer }) => { producer.with['expected-shards'] = '{}'; }, 'record producer cannot aggregate instead'],
  ['missing budget', ({ job }) => { delete job['timeout-minutes']; }, 'timeout must be within the execution budget'],
  ['excessive budget', ({ job }) => { job['timeout-minutes'] = 999; }, 'timeout must be within the execution budget'],
  ['producer permission escalation', ({ job }) => { job.permissions = { contents: 'write' }; }, 'producer permission escalation'],
  ['missing operation bindings', ({ metadata }) => { delete metadata.bindings; }, 'required operation bindings incomplete'],
  ['duplicate step identity', ({ job, metadata }) => { job.steps.push(structuredClone(job.steps.find(step => step.id === metadata['required-steps'][0]))); }, 'duplicate step identity'],
  ['missing expected shards', ({ metadata }) => { metadata.shards = []; }, 'missing or duplicate expected shards'],
  ['stale execution job entry', ({ spec }) => { spec.jobs.stale = structuredClone(Object.values(spec.jobs)[0]); }, 'stale execution job metadata'],
  ['invalid report kind', ({ metadata }) => { metadata.reports = [{ kind: 'unknown', path: 'file' }]; }, 'invalid required report metadata'],
  ['matrix added behind single outputs', ({ job }) => { job.strategy = { matrix: { shard: ['a', 'b'] } }; }, 'single non-matrix producer'],
];

for (const [name, mutate, diagnostic] of executionMutations) {
  test(`execution graph mutation: rejects ${name}`, () => {
    const candidate = { graph: structuredClone(graph), contract: structuredClone(contract) };
    const spec = candidate.contract.execution['python-lint'];
    const workflow = candidate.graph['.github/workflows/python-lint.yml'];
    const metadata = spec.jobs[spec['output-job']];
    const job = workflow.jobs[spec['output-job']];
    const producer = job.steps.find(step => step.id === metadata.producer);
    mutate({ spec, workflow, metadata, job, producer });
    const errors = validateWorkflows(candidate.graph, candidate.contract);
    assert.ok(errors.some(error => error.includes(diagnostic)), `Expected ${diagnostic}; received ${JSON.stringify(errors)}`);
  });
}

for (const [name, mutate, diagnostic] of [
  ['omitted matrix shard', ({ metadata }) => { metadata.shards = metadata.shards.slice(1); }, 'aggregate expected shard inventory mismatch'],
  ['altered matrix', ({ job }) => { job.strategy.matrix = { shard: ['fake'] }; }, 'execution matrix mismatch'],
  ['removed aggregate dependency', ({ aggregate }) => { aggregate.needs = []; }, 'complete execution inventory'],
  ['changed aggregate expected inventory', ({ producer }) => { producer.with['expected-shards'] = '{}'; }, 'aggregate expected shards mismatch'],
  ['matrix output producer', ({ aggregate }) => { aggregate.strategy = { matrix: { shard: ['a'] } }; }, 'non-matrix producer'],
  ['conditional aggregate', ({ aggregate }) => { aggregate.if = 'success()'; }, 'aggregate must always execute'],
  ['missing aggregate context', ({ producer }) => { producer.with['needs-json'] = '{}'; }, 'aggregate identity or needs context mismatch'],
  ['wrong aggregate output shard', ({ producer }) => { producer.with.shard = 'other'; }, 'aggregate output shard mismatch'],
  ['aggregate suppresses evidence failure', ({ producer }) => { producer.with['fail-on-error'] = 'false'; }, 'aggregate cannot suppress evidence failure'],
]) {
  test(`aggregate graph mutation: rejects ${name}`, () => {
    const candidate = { graph: structuredClone(graph), contract: structuredClone(contract) };
    const spec = candidate.contract.execution['pytest-gpu-offload'];
    const workflow = candidate.graph['.github/workflows/pytest-gpu-offload.yml'];
    const [id, metadata] = Object.entries(spec.jobs)[0];
    const aggregate = workflow.jobs[spec.aggregate.job];
    const producer = aggregate.steps.find(step => step.id === spec.aggregate.producer);
    mutate({ metadata, job: workflow.jobs[id], aggregate, producer });
    const errors = validateWorkflows(candidate.graph, candidate.contract);
    assert.ok(errors.some(error => error.includes(diagnostic)), `Expected ${diagnostic}; received ${JSON.stringify(errors)}`);
  });
}

for (const [name, mutate, diagnostic] of [
  ['constant status output', action => { action.outputs['work-status'].value = 'success'; }, 'Shared action output chain mismatch'],
  ['wrong artifact output source', action => { action.outputs['artifact-id'].value = '${{ steps.receipt.outputs.artifact-id }}'; }, 'Shared action output chain mismatch'],
  ['replaced publisher command', action => { action.runs.steps.find(step => step.id === 'receipt').run = 'echo success'; }, 'canonical receipt publisher'],
  ['publisher partial input context', action => { action.runs.steps.find(step => step.id === 'receipt').env.INPUT_STEPS_JSON = '{}'; }, 'Shared action input chain mismatch'],
  ['missing report upload requirement', action => { action.runs.steps.find(step => step.id === 'upload').with['if-no-files-found'] = 'ignore'; }, 'publish the validated artifact'],
  ['unrelated artifact publication', action => { action.runs.steps.find(step => step.id === 'upload').with.path = '.'; }, 'publish the validated artifact'],
  ['upload failure suppression', action => { action.runs.steps.find(step => step.id === 'upload')['continue-on-error'] = true; }, 'publish the validated artifact'],
  ['missing final failure enforcement', action => { action.runs.steps.pop(); }, 'enforce published status'],
  ['failure enforcement bypass', action => { action.runs.steps.at(-1).run = 'exit 0'; }, 'enforce published status'],
  ['status enforcement context changed', action => { action.runs.steps.at(-1).env.WORK_STATUS = 'success'; }, 'enforce published status'],
]) {
  test(`shared output graph mutation: rejects ${name}`, () => {
    const candidate = structuredClone(graph);
    mutate(candidate['.github/actions/ci-outcome/action.yml']);
    const errors = validateWorkflows(candidate, contract);
    assert.ok(errors.some(error => error.includes(diagnostic)), `Expected ${diagnostic}; received ${JSON.stringify(errors)}`);
  });
}

for (const [name, mutate, diagnostic] of [
  ['missing discovery target output', workflow => { delete workflow.jobs.discover.outputs.targets; }, 'discovered target output chain mismatch'],
  ['wrong discovery output source', workflow => { workflow.jobs.discover.outputs.shards = '${{ steps.outcome.outputs.shards }}'; }, 'discovered target output chain mismatch'],
  ['missing scanner discovery dependency', workflow => { workflow.jobs.scan.needs = []; }, 'dynamic matrix requires verified discovery'],
  ['missing expected target mapping', workflow => { delete workflow.jobs.aggregate.steps.find(step => step.id === 'outcome').with['expected-targets']; }, 'aggregate expected targets mismatch'],
  ['different image scanned than recorded', workflow => { workflow.jobs.scan.steps.find(step => step.id === 'scan').with['image-ref'] = 'another-image'; }, 'required action input context mismatch'],
  ['different scanner report location', workflow => { workflow.jobs.scan.steps.find(step => step.id === 'scan').with.output = 'other.sarif'; }, 'required action input context mismatch'],
  ['scanner report tool identity removed', workflow => {
    const producer = workflow.jobs.scan.steps.find(step => step.id === 'outcome');
    const reports = JSON.parse(producer.with.reports);
    delete reports[0].tool;
    producer.with.reports = JSON.stringify(reports);
  }, 'required report declaration mismatch'],
  ['scanner report tool identity substituted', workflow => {
    const producer = workflow.jobs.scan.steps.find(step => step.id === 'outcome');
    const reports = JSON.parse(producer.with.reports);
    reports[0].tool = 'Other scanner';
    producer.with.reports = JSON.stringify(reports);
  }, 'required report declaration mismatch'],
]) {
  test(`scanner identity mutation: rejects ${name}`, () => {
    const candidate = structuredClone(graph);
    mutate(candidate['.github/workflows/container-scan.yml']);
    const errors = validateWorkflows(candidate, contract);
    assert.ok(errors.some(error => error.includes(diagnostic)), `Expected ${diagnostic}; received ${JSON.stringify(errors)}`);
  });
}

for (const [name, mutate, diagnostic] of [
  ['canonical CPU shard omitted', ({ spec }) => { spec.jobs['import-smoke'].shards = spec.jobs['import-smoke'].shards.filter(shard => shard !== 'cpu-vla'); }, 'canonical shards must cover the complete matrix'],
  ['CPU aggregate shard omitted even with matching declaration', ({ producer, spec }) => {
    producer.with['expected-shards'] = producer.with['expected-shards'].replace('"cpu-vla", ', '');
    spec.aggregate['expected-shards'] = producer.with['expected-shards'];
  }, 'aggregate expected shard inventory mismatch'],
  ['wrong aggregate job mapping even with matching declaration', ({ producer, spec }) => {
    producer.with['expected-shards'] = '{"wrong-job":["cpu-rl"]}';
    spec.aggregate['expected-shards'] = producer.with['expected-shards'];
  }, 'aggregate expected jobs mismatch'],
  ['selected VLA image expectation replaced by empty array', ({ producer, spec }) => {
    producer.with['expected-shards'] = producer.with['expected-shards'].replace("${{ inputs.vla && '[\"image-vla\"]' || '[]' }}", '[]');
    spec.aggregate['expected-shards'] = producer.with['expected-shards'];
  }, 'aggregate expected shard inventory mismatch'],
]) {
  test(`smoke inventory mutation: rejects ${name}`, () => {
    const candidate = { graph: structuredClone(graph), contract: structuredClone(contract) };
    const spec = candidate.contract.execution['smoke-cpu'];
    const producer = candidate.graph[smokePath].jobs.outcome.steps.find(step => step.id === 'evidence');
    mutate({ producer, spec });
    const errors = validateWorkflows(candidate.graph, candidate.contract);
    assert.ok(errors.some(error => error.includes(diagnostic)), `Expected ${diagnostic}; received ${JSON.stringify(errors)}`);
  });
}

const smokeChildren = {
  rl: ['image-smoke-rl', 'image-rl'],
  il: ['image-smoke-il', 'image-il'],
  evaluation: ['image-smoke-evaluation', 'image-evaluation'],
  vla: ['image-smoke-vla', 'image-vla'],
  osmo_replay: ['image-smoke-osmo-replay', 'image-osmo-replay'],
  dataviewer: ['build-smoke-dataviewer', 'compose-dataviewer'],
};

for (const selected of [[], Object.keys(smokeChildren), ...Object.keys(smokeChildren).map(input => [input])]) {
  test(`smoke selection: exact shards and operation counts for ${selected.join(', ') || 'CPU only'}`, () => {
    const workflow = graph[smokePath];
    const spec = contract.execution['smoke-cpu'];
    const producer = workflow.jobs.outcome.steps.find(step => step.id === 'evidence');
    const inventory = JSON.parse(producer.with['expected-shards'].replace(
      /\$\{\{\s*inputs\.(\w+)\s*&&\s*'(\[[^']*\])'\s*\|\|\s*'\[\]'\s*\}\}/g,
      (_, input, shards) => selected.includes(input) ? shards : '[]',
    ));
    const expected = {
      'import-smoke': cpuDomains.map(domain => `cpu-${domain}`),
      ...Object.fromEntries(Object.entries(smokeChildren).map(([input, [job, shard]]) => [
        job, selected.includes(input) ? [shard] : [],
      ])),
    };
    assert.deepEqual(inventory, expected);
    assert.deepEqual([...workflow.jobs.outcome.needs].sort(), Object.keys(expected).sort());
    assert.deepEqual(Object.keys(spec.jobs).sort(), Object.keys(expected).sort());
    const operations = Object.entries(inventory).reduce((total, [job, shards]) =>
      total + shards.length * spec.jobs[job]['required-steps'].length, 0);
    assert.equal(operations, 6 + selected.length);
    assert.equal(new Set(Object.values(inventory).flat()).size, operations);
    for (const [input, [job]] of Object.entries(smokeChildren)) {
      assert.equal(workflow.jobs[job].if, `inputs.${input}`);
    }
    if (selected.includes('vla')) assert.deepEqual(inventory['image-smoke-vla'], ['image-vla']);
  });
}

test('smoke budgets: preserve CPU/image/compose/aggregate and fuzz/kind limits', () => {
  const jobs = graph[smokePath].jobs;
  assert.equal(jobs['import-smoke']['timeout-minutes'], 10);
  for (const [input, [job]] of Object.entries(smokeChildren)) {
    assert.equal(jobs[job]['timeout-minutes'], input === 'dataviewer' ? 20 : 15);
  }
  assert.equal(jobs.outcome['timeout-minutes'], 10);
  assert.equal(graph['.github/workflows/fuzz-regression-tests.yml'].jobs['fuzz-regression']['timeout-minutes'], 20);
  assert.equal(graph['.github/workflows/gpu-offload-kind-smoke.yml'].jobs['kind-smoke']['timeout-minutes'], 20);
});

function outcomeFixture(t, { all = false, empty = false } = {}) {
  const repo = createRepository(t);
  if (!empty) writeFixture(repo.cwd, all ? '.github/workflows/fixture.yml' : 'docs/guide.md', fileContent);
  const head = repo.commit();
  const outputs = selectionOutputs({ ...repo, head, contract });
  const needs = Object.fromEntries(requiredLanes(contract, 'pr').map(lane => [lane.owners.pr, {
    result: lane.selector === 'always' || outputs[lane.selector] === 'true' ? 'success' : 'skipped',
    outputs: executionOutputs(),
  }]));
  needs.changes.outputs = outputs;
  return needs;
}

function freshEvidence(t, needs, owner = 'main') {
  const workspace = temporaryDirectory(t);
  const context = {
    runId: '123', runAttempt: '2', head: needs.changes?.outputs?.head_sha ?? 'b'.repeat(40),
    base: 'a'.repeat(40), receiptsDirectory: join(workspace, 'downloads'),
  };
  mkdirSync(context.receiptsDirectory);
  const roots = new Map();
  const artifacts = [];
  const image = `registry.example/image:stable@sha256:${'a'.repeat(64)}`;
  const inventory = [{ shard: 'image', image }];
  const writeReport = report => {
    let content;
    if (report.kind === 'junit') {
      content = '<testsuite tests="1" failures="0" errors="0" skipped="0"><testcase classname="fixture" name="passes"/></testsuite>';
    } else if (report.kind === 'nunit') {
      content = '<test-results total="1" errors="0" failures="0" not-run="0"><test-suite name="fixture"><results><test-case name="passes" executed="True" result="Success"/></results></test-suite></test-results>';
    } else if (report.kind === 'jest') {
      content = JSON.stringify({ success: true, numTotalTests: 1, numPassedTests: 1, numFailedTests: 0,
        numPendingTests: 0, numTodoTests: 0,
        testResults: [{ name: 'suite.test.js', status: 'passed', assertionResults: [{ fullName: 'passes', status: 'passed' }] }] });
    } else if (report.kind === 'sarif') {
      content = JSON.stringify({ version: '2.1.0', runs: [{ tool: { driver: { name: report.tool } }, results: [] }] });
    } else if (report.kind === 'json') {
      content = JSON.stringify(report.path === 'logs/container/manifest.json'
        ? { summary: { images_count: inventory.length, overall_passed: true }, results: inventory }
        : { projects_count: 1, drift_count: 0, check_passed: true, results: [{ Project: '.', Passed: true }] });
    } else {
      content = 'Native fixture operation completed.\n';
    }
    writeFixture(workspace, report.kind === 'sarif' && !report.path.endsWith('.sarif') ? `${report.path}/result.sarif` : report.path, content);
  };
  const publish = receipt => {
    receipt.tools = { node: process.version, git: 'git version 2.50.0' };
    const outputs = publishOutcome(receipt, { workspace, outputDirectory: `downloads/${receipt['artifact-name']}` });
    const entry = { receipt, path: join(outputs['artifact-path'], 'receipt.json'), directory: outputs['artifact-path'] };
    artifacts.push(entry);
    return entry;
  };
  for (const lane of visibleLanes(contract, owner).filter(lane => lane.target)) {
    if (owner === 'pr' && lane.selector !== 'always' && needs.changes.outputs[lane.selector] !== 'true') continue;
    const workflow = lane.target.replace(/\.ya?ml$/, '');
    const spec = contract.execution[workflow];
    const children = [];
    const artifactRoots = new Map();
    const expectedShards = {};
    const expectedTargets = {};
    const innerNeeds = {};
    for (const [job, metadata] of Object.entries(spec.jobs)) {
      const input = metadata.if?.match(/^inputs\.(\w+)$/)?.[1];
      const selected = !input || owner === 'main' || contract.smokeInputs[input].some(selector => needs.changes.outputs[selector] === 'true');
      const shards = metadata.discovery ? inventory.map(item => item.shard) : selected ? metadata.shards : [];
      expectedShards[job] = shards;
      innerNeeds[job] ??= { result: selected ? 'success' : 'skipped' };
      if (metadata.discovery) {
        expectedTargets[job] = Object.fromEntries(inventory.map(item => [item.shard, item.image]));
        innerNeeds[metadata.discovery.job] = { result: 'success', outputs: {
          [metadata.discovery['shards-output']]: JSON.stringify(shards),
          [metadata.discovery['targets-output']]: JSON.stringify(expectedTargets[job]),
        } };
      }
      for (const shard of shards) {
        const reports = metadata.reports.map(report => ({ ...report, path: report.path.replaceAll('{shard}', shard) }));
        reports.forEach(writeReport);
        const receipt = recordOutcome({
          workflow, job, shard, target: expectedTargets[job]?.[shard] ?? shard,
          runId: context.runId, runAttempt: context.runAttempt, sha: context.head,
          selected: true, selectionReason: 'Selected fixture operation', tools: [], workspace,
          requiredSteps: metadata['required-steps'], reports,
          steps: Object.fromEntries(metadata['required-steps'].map(id => [id, { outcome: 'success', conclusion: 'success' }])),
        }, contract);
        assert.equal(receipt['work-status'], 'success', `${workflow}/${job}: ${receipt.errors.join('; ')}`);
        const artifact = publish(receipt);
        children.push(receipt);
        artifactRoots.set(receipt, artifact.directory);
      }
    }
    let root = artifacts.find(artifact => artifact.receipt.workflow === workflow && artifact.receipt.job === spec['output-job']);
    if (spec.aggregate) {
      const receipt = aggregateOutcomes({
        workflow, job: spec['output-job'], shard: spec.aggregate.shard ?? 'default',
        runId: context.runId, runAttempt: context.runAttempt, sha: context.head,
        selected: true, selectionReason: 'All selected fixture shards', tools: [],
        expectedShards, expectedTargets, needs: innerNeeds, artifactRoots,
      }, contract, children);
      assert.equal(receipt['work-status'], 'success', `${workflow}: ${receipt.errors.join('; ')}`);
      root = publish(receipt);
    }
    roots.set(workflow, root);
    const id = String(artifacts.length);
    needs[lane.owners[owner]] ??= { result: 'success' };
    needs[lane.owners[owner]].outputs = {
      ...outcomeOutputs(root.receipt), 'artifact-id': id,
      'artifact-url': `https://github.com/microsoft/physical-ai-toolchain/actions/runs/${context.runId}/artifacts/${id}`,
    };
  }
  return { context, roots, artifacts, workspace, env: {
    CI_RECEIPTS_DIRECTORY: context.receiptsDirectory, GITHUB_RUN_ID: context.runId,
    GITHUB_RUN_ATTEMPT: context.runAttempt, HEAD_SHA: context.head, BASE_SHA: context.base, CI_CANCELLED: 'false',
  } };
}

test('outcomes: every selected and unconditional lane succeeds', t => {
  const needs = outcomeFixture(t, { all: true });
  assert.ok(Object.values(needs).every(job => job.result === 'success'));
  assert.deepEqual(evaluateChecks(needs, contract), []);
});

test('outcomes: GitHub may omit the empty first-failure output after successful publication', t => {
  const needs = outcomeFixture(t);
  delete needs.smoke.outputs['first-failure'];
  assert.deepEqual(evaluateChecks(needs, contract), []);
  delete needs.smoke.outputs['work-status'];
  assert.ok(evaluateChecks(needs, contract).some(error => error.startsWith('smoke:')));
});

test('outcomes: verified unselected lanes may legitimately skip', t => {
  const needs = outcomeFixture(t);
  assert.equal(needs.changes.outputs.training, 'false');
  assert.equal(needs['pytest-training'].result, 'skipped');
  assert.equal(needs['docusaurus-tests'].result, 'success');
  assert.deepEqual(evaluateChecks(needs, contract), []);
});

const outcomeMutations = [
  ['missing execution evidence despite green result', needs => { delete needs['docusaurus-tests'].outputs; }, 'docusaurus-tests: expected work-status success but received missing'],
  ['selected lane reports planned skip', needs => { needs['docusaurus-tests'].outputs['work-status'] = 'planned-skip'; }, 'docusaurus-tests: expected work-status success but received planned-skip'],
  ['cancelled evidence masked by green job', needs => { needs['docusaurus-tests'].outputs['work-status'] = 'cancelled'; }, 'docusaurus-tests: expected work-status success but received cancelled'],
  ['missing expected count', needs => { delete needs.smoke.outputs['expected-count']; }, 'smoke: invalid or missing expected-count'],
  ['missing executed count', needs => { delete needs.smoke.outputs['executed-count']; }, 'smoke: invalid or missing executed-count'],
  ['partial operations', needs => { needs.smoke.outputs['executed-count'] = '1'; }, 'smoke: incomplete execution counts'],
  ['no expected work', needs => { needs.smoke.outputs['expected-count'] = '0'; needs.smoke.outputs['executed-count'] = '0'; }, 'smoke: incomplete execution counts'],
  ['unsafe integer count', needs => { needs.smoke.outputs['test-count'] = '9007199254740992'; }, 'smoke: invalid or missing test-count'],
  ['negative skipped count', needs => { needs.smoke.outputs['skipped-count'] = '-1'; }, 'smoke: invalid or missing skipped-count'],
  ['zero executed tests', needs => { needs['docusaurus-tests'].outputs['test-count'] = '0'; }, 'docusaurus-tests: no executed testcases'],
  ['missing artifact identity', needs => { delete needs.smoke.outputs['artifact-id']; }, 'smoke: missing or invalid artifact identity'],
  ['artifact URL mismatch', needs => { needs.smoke.outputs['artifact-id'] = '11'; }, 'smoke: missing or invalid artifact identity'],
  ['unsafe artifact link', needs => { needs.smoke.outputs['artifact-url'] = 'javascript:alert(1)'; }, 'smoke: missing or invalid artifact identity'],
  ['failed operation masked by green result', needs => { needs.smoke.outputs['first-failure'] = 'smoke'; }, 'smoke: first-failure contradicts successful evidence'],
  ['missing selection reasons', needs => { delete needs.changes.outputs.selection_reasons; }, 'Invalid selection reason: training'],
  ['malformed selection reasons', needs => { needs.changes.outputs.selection_reasons = '{'; }, 'Invalid selection reason: training'],
  ['non-object selection reasons', needs => { needs.changes.outputs.selection_reasons = '[]'; }, 'Invalid selection reason: training'],
  ['selected lane skips', needs => { needs['docusaurus-tests'].result = 'skipped'; }, 'docusaurus-tests: expected success but received skipped'],
  ['unconditional lane skips', needs => { needs.smoke.result = 'skipped'; }, 'smoke: expected success but received skipped'],
  ['selected lane cancelled', needs => { needs['docusaurus-tests'].result = 'cancelled'; }, 'docusaurus-tests: expected success but received cancelled'],
  ['unselected lane failed', needs => { needs['pytest-training'].result = 'failure'; }, 'pytest-training: expected success/skipped but received failure'],
  ['missing result', needs => { delete needs['docusaurus-tests'].result; }, 'docusaurus-tests: expected success but received missing'],
  ['missing required job', needs => { delete needs.smoke; }, 'smoke: expected success but received missing'],
  ['missing selector outputs', needs => { delete needs.changes.outputs; }, 'Missing verified selection evidence'],
  ['invalid comparison SHA', needs => { needs.changes.outputs.base_sha = 'HEAD'; }, 'Missing comparison SHAs'],
  ['unverified full comparison metadata on PR', needs => { needs.changes.outputs.selection_status = 'full'; }, 'Missing verified selection evidence'],
  ['malformed boolean output', needs => { needs.changes.outputs.training = true; }, 'Invalid selection output: training'],
  ['invalid comparison file count', needs => { needs.changes.outputs.file_count = '-1'; }, 'Invalid comparison file count'],
  ['verified-empty status contradicts changed-file count', needs => { needs.changes.outputs.selection_status = 'verified-empty'; }, 'Invalid comparison file count'],
  ['verified-empty metadata contradicts selected lanes', needs => {
    needs.changes.outputs.selection_status = 'verified-empty';
    needs.changes.outputs.file_count = '0';
  }, 'Nonempty selection for verified-empty comparison: docusaurus'],
];

for (const [name, mutate, diagnostic] of outcomeMutations) {
  test(`outcome mutation: rejects ${name}`, t => {
    const needs = outcomeFixture(t);
    assert.deepEqual(evaluateChecks(needs, contract), [], 'Original outcomes must be valid');
    mutate(needs);
    const errors = evaluateChecks(needs, contract);
    assert.ok(errors.includes(diagnostic), `Expected ${diagnostic}; received ${JSON.stringify(errors)}`);
  });
}

test('outcomes: a real verified-empty comparison still requires unconditional success', t => {
  const needs = outcomeFixture(t, { empty: true });
  assert.equal(needs.changes.outputs.selection_status, 'verified-empty');
  assert.equal(needs.smoke.result, 'success');
  assert.equal(needs['docusaurus-tests'].result, 'skipped');
  assert.deepEqual(evaluateChecks(needs, contract), []);
});

test('outcomes: advisory OSV and Terraform security cannot block the required aggregate', t => {
  const needs = outcomeFixture(t);
  needs['osv-scanner'] = { result: 'failure' };
  needs['terraform-security'] = { result: 'cancelled' };
  assert.deepEqual(evaluateChecks(needs, contract), []);
});

test('outcomes: all whole-step tolerated lanes remain advisory but container execution is required', t => {
  const needs = outcomeFixture(t, { all: true });
  for (const id of ['markdown-link-check', 'terraform-tests', 'terraform-docs-check']) needs[id] = { result: 'failure' };
  assert.deepEqual(evaluateChecks(needs, contract), []);
  needs['container-scan'].outputs['work-status'] = 'failure';
  assert.ok(evaluateChecks(needs, contract).some(error => error.startsWith('container-scan:')));
});

test('outcomes: unselected successful job requires an explicit zero-work planned-skip receipt', t => {
  const needs = outcomeFixture(t);
  needs['pytest-training'] = { result: 'success', outputs: executionOutputs({
    'work-status': 'planned-skip', 'expected-count': '0', 'executed-count': '0', 'test-count': '0',
  }) };
  assert.deepEqual(evaluateChecks(needs, contract), []);
  needs['pytest-training'].outputs['test-count'] = '2';
  assert.ok(evaluateChecks(needs, contract).includes('pytest-training: planned skip has executed testcases'));
  needs['pytest-training'].outputs['expected-count'] = '2';
  assert.ok(evaluateChecks(needs, contract).includes('pytest-training: incomplete execution counts'));
});

test('outcomes: main requires every lane without PR selection metadata', () => {
  const needs = Object.fromEntries(requiredLanes(contract, 'main').map(lane => [
    lane.owners.main, { result: 'success', outputs: executionOutputs() },
  ]));
  assert.deepEqual(evaluateChecks(needs, contract, 'main'), []);
  needs['pytest-training'].result = 'skipped';
  assert.ok(evaluateChecks(needs, contract, 'main').includes('pytest-training: expected success but received skipped'));
  assert.deepEqual(evaluateChecks(needs, contract, 'unknown'), ['Unknown validation owner']);
  assert.deepEqual(evaluateChecks([], contract), ['Missing needs object']);
});

test('summary: cancellation differs from failure and advisory results are visible', t => {
  const needs = outcomeFixture(t);
  needs['docusaurus-tests'].result = 'cancelled';
  needs['terraform-security'] = { result: 'failure', outputs: executionOutputs({ 'work-status': 'failure', 'first-failure': 'checkov' }) };
  const summary = buildSummary(needs, contract);
  assert.equal(summary.report.status, 'cancelled');
  assert.equal(summary.report.rows.find(row => row.job === 'docusaurus-tests').status, 'cancelled');
  assert.equal(summary.report.rows.find(row => row.job === 'pytest-training').status, 'planned-skip');
  assert.equal(summary.report.rows.find(row => row.job === 'terraform-security').classification, 'advisory');
  assert.ok(summary.markdown.includes('### Advisory checks'));
  assert.ok(summary.markdown.includes('[artifact](https://github.com/'));
  assert.equal(summary.report.comparison.base, needs.changes.outputs.base_sha);
  assert.equal(summary.report.tools.node, process.version);
  needs['docusaurus-tests'].result = 'failure';
  assert.equal(buildSummary(needs, contract).report.status, 'failure');
});

test('summary: cancellation before any child starts is not mislabeled as validation failure', () => {
  const needs = Object.fromEntries(visibleLanes(contract, 'pr').map(lane => [lane.owners.pr, { result: 'skipped' }]));
  assert.ok(Object.values(needs).every(job => job.result !== 'cancelled'));
  const summary = buildSummary(needs, contract, 'pr', { cancelled: true });
  assert.equal(summary.report.status, 'cancelled');
  assert.ok(summary.report.rows.every(row => row.status === 'cancelled'));
  assert.ok(summary.report.errors.includes('Workflow run was cancelled or superseded'));
  assert.ok(summary.markdown.includes('PR validation: cancelled'));
  assert.equal(buildSummary({}, contract, 'pr', { cancelled: true }).report.status, 'cancelled');
});

test('summary: explicit cancellation never permits release despite successful child receipts', t => {
  const needs = Object.fromEntries(visibleLanes(contract, 'main').map(lane => [
    lane.owners.main, { result: 'success', outputs: executionOutputs() },
  ]));
  const { context } = freshEvidence(t, needs);
  const summary = buildSummary(needs, contract, 'main', { ...context, cancelled: true });
  assert.equal(summary.report.status, 'cancelled');
  assert.ok(summary.report.rows.every(row => row.status === 'success'));
  assert.deepEqual(summary.report.errors, ['Workflow run was cancelled or superseded']);
  assert.equal(buildSummary(needs, contract, 'main', { ...context, cancelled: false }).report.status, 'success');
  assert.deepEqual(buildSummary(needs, contract, 'main', { ...context, cancelled: 'invalid' }).report.errors, ['Invalid workflow cancellation context']);
});

test('summary: verified unselected lanes retain planned-skip identity during cancellation', t => {
  const needs = outcomeFixture(t);
  needs['docusaurus-tests'].result = 'skipped';
  const summary = buildSummary(needs, contract, 'pr', { cancelled: true });
  assert.equal(summary.report.rows.find(row => row.job === 'pytest-training').status, 'planned-skip');
  assert.equal(summary.report.rows.find(row => row.job === 'docusaurus-tests').status, 'cancelled');
  assert.equal(summary.report.status, 'cancelled');
});

test('summary CLI: explicit run cancellation fails the gate and persists cancellation evidence', t => {
  const cwd = temporaryDirectory(t);
  const needs = Object.fromEntries(visibleLanes(contract, 'main').map(lane => [
    lane.owners.main, { result: 'success', outputs: executionOutputs() },
  ]));
  const evidence = freshEvidence(t, needs);
  for (const [cancelled, exitCode, status] of [['true', 1, 'cancelled'], ['false', 0, 'success'], ['invalid', 1, 'failure']]) {
    const path = join(cwd, `${cancelled}.json`);
    const result = spawnSync(process.execPath, [evaluatorPath], {
      cwd, encoding: 'utf8', env: {
        ...process.env, ...evidence.env, NEEDS_JSON: JSON.stringify(needs), CI_OWNER: 'main',
        CI_CANCELLED: cancelled, CI_SUMMARY_PATH: path,
      },
    });
    assert.equal(result.status, exitCode, result.stderr);
    assert.equal(JSON.parse(readFileSync(path, 'utf8')).status, status);
  }
});

test('summary: bounds and escapes display text and omits unsafe artifacts and arbitrary outputs', t => {
  const needs = outcomeFixture(t);
  needs.smoke.outputs['first-failure'] = '<script>|[link](javascript:bad)\n' + 'a'.repeat(400);
  needs.smoke.outputs['artifact-url'] = 'https://evil.invalid/secret';
  needs.smoke.outputs.secret = 'DO_NOT_SERIALIZE';
  const summary = buildSummary(needs, contract);
  assert.ok(!summary.markdown.includes('<script>'));
  assert.ok(summary.markdown.includes('&#60;script&#62;'));
  assert.ok(!JSON.stringify(summary.report).includes('DO_NOT_SERIALIZE'));
  assert.ok(!JSON.stringify(summary.report).includes('evil.invalid'));
  assert.equal(summary.report.rows.find(row => row.job === 'smoke').firstFailure.length, 300);
  const missing = buildSummary(null, contract, 'main', { base: 'bad', head: unknownSha });
  assert.equal(missing.report.comparison.base, '');
  assert.equal(missing.report.comparison.head, unknownSha);
  assert.equal(missing.report.rows[0].expected, null);
});

test('summary: main reports full selection and supplied comparison SHAs', t => {
  const needs = Object.fromEntries(visibleLanes(contract, 'main').map(lane => [
    lane.owners.main, { result: 'success', outputs: executionOutputs() },
  ]));
  const { context } = freshEvidence(t, needs);
  const summary = buildSummary(needs, contract, 'main', context);
  assert.equal(summary.report.status, 'success');
  assert.equal(summary.report.comparison.status, 'full');
  assert.equal(summary.report.comparison.base, 'a'.repeat(40));
  assert.ok(summary.report.rows.every(row => row.selected && row.reason === 'full-run'));
});

function mainSuccessNeeds() {
  return Object.fromEntries(visibleLanes(contract, 'main').map(lane => [
    lane.owners.main, { result: 'success', outputs: executionOutputs() },
  ]));
}

test('receipt gate: real main success outputs without downloaded receipts never pass', () => {
  const summary = buildSummary(mainSuccessNeeds(), contract, 'main', { runId: '123', runAttempt: '2', head: 'b'.repeat(40) });
  assert.equal(summary.report.status, 'failure');
  for (const lane of requiredLanes(contract, 'main')) {
    assert.ok(summary.report.errors.includes(`${lane.owners.main}: Missing current-attempt output receipt`));
  }
});

test('receipt gate: a complete fresh main rerun validates every native report and matches outputs', t => {
  const needs = mainSuccessNeeds();
  const evidence = freshEvidence(t, needs);
  const summary = buildSummary(needs, contract, 'main', evidence.context);
  assert.equal(summary.report.status, 'success', JSON.stringify(summary.report.errors));
  assert.deepEqual(summary.report.errors, []);
  assert.deepEqual(summary.report.warnings, []);
  assert.equal(summary.report.rows.find(row => row.job === 'smoke').expected, 12);
  assert.equal(summary.report.rows.find(row => row.job === 'container-scan').expected, 2);
  assert.ok(summary.report.tools.producers.some(producer => producer.workflow === 'codeql-analysis' && producer.shard === 'python'));
});

test('receipt gate: partial rerun cannot reuse an earlier successful job output', t => {
  const needs = mainSuccessNeeds();
  const evidence = freshEvidence(t, needs);
  rmSync(evidence.roots.get('pytest-training').directory, { recursive: true });
  assert.equal(needs['pytest-training'].result, 'success');
  assert.equal(needs['pytest-training'].outputs['work-status'], 'success');
  const summary = buildSummary(needs, contract, 'main', evidence.context);
  assert.equal(summary.report.status, 'failure');
  assert.deepEqual(summary.report.errors, ['pytest-training: Missing current-attempt output receipt']);
});

for (const [name, mutate, diagnostic] of [
  ['stale run', ({ receipt }) => { receipt['run-id'] = '122'; }, 'Stale or mismatched output receipt identity'],
  ['stale attempt', ({ receipt }) => { receipt['run-attempt'] = '1'; }, 'Stale or mismatched output receipt identity'],
  ['stale commit', ({ receipt }) => { receipt.sha = 'c'.repeat(40); }, 'Stale or mismatched output receipt identity'],
  ['wrong output shard', ({ receipt }) => { receipt.shard = 'other'; }, 'Stale or mismatched output receipt identity'],
  ['invalid schema', ({ receipt }) => { receipt.version = 2; }, 'Invalid output receipt schema'],
  ['wrong receipt mode', ({ receipt }) => { receipt.mode = 'aggregate'; }, 'Invalid output receipt schema'],
  ['wrong workflow', ({ receipt }) => { receipt.workflow = 'other-workflow'; }, 'Stale or mismatched output receipt identity'],
  ['wrong output job', ({ receipt }) => { receipt.job = 'other-job'; }, 'Missing current-attempt output receipt'],
  ['wrong artifact identity', ({ receipt }) => { receipt['artifact-name'] = 'other-artifact'; }, 'Mismatched output artifact identity'],
  ['failure hidden by green outputs', ({ receipt }) => { receipt['work-status'] = 'failure'; }, 'Output receipt did not successfully execute'],
  ['missing required operation', ({ receipt }) => { receipt.operations.pop(); }, 'Invalid or incomplete current-attempt operations'],
  ['missing required report', ({ receipt }) => { receipt.reports = []; }, 'Invalid or incomplete current-attempt operations'],
  ['contradictory receipt counts', ({ receipt }) => { receipt.counts.executed = 99; }, 'Invalid or incomplete current-attempt operations'],
  ['contradictory exposed operation counts', ({ needs }) => {
    needs['python-lint'].outputs['expected-count'] = '4';
    needs['python-lint'].outputs['executed-count'] = '4';
  }, 'Exposed outputs contradict current-attempt receipt'],
  ['contradictory exposed test counts', ({ needs }) => { needs['python-lint'].outputs['test-count'] = '2'; }, 'Exposed outputs contradict current-attempt receipt'],
  ['contradictory exposed skipped counts', ({ needs }) => { needs['python-lint'].outputs['skipped-count'] = '2'; }, 'Exposed outputs contradict current-attempt receipt'],
  ['artifact URL from a different run', ({ needs }) => {
    needs['python-lint'].outputs['artifact-url'] = needs['python-lint'].outputs['artifact-url'].replace('/runs/123/', '/runs/122/');
  }, 'Exposed artifact belongs to another run'],
]) {
  test(`receipt gate mutation: rejects ${name}`, t => {
    const needs = mainSuccessNeeds();
    const evidence = freshEvidence(t, needs);
    const root = evidence.roots.get('python-lint');
    mutate({ needs, receipt: root.receipt });
    writeFileSync(root.path, JSON.stringify(root.receipt));
    const summary = buildSummary(needs, contract, 'main', evidence.context);
    assert.equal(summary.report.status, 'failure');
    assert.ok(summary.report.errors.some(error => error.includes(diagnostic)), JSON.stringify(summary.report.errors));
  });
}

test('receipt gate: duplicate output receipts fail even with identical successful content', t => {
  const needs = mainSuccessNeeds();
  const evidence = freshEvidence(t, needs);
  const root = evidence.roots.get('python-lint');
  cpSync(root.directory, join(evidence.context.receiptsDirectory, 'duplicate-output'), { recursive: true });
  assert.ok(buildSummary(needs, contract, 'main', evidence.context).report.errors.includes('python-lint: Duplicate output receipts'));
});

test('receipt gate: malformed mandatory receipt blocks but absent or malformed advisory evidence only warns', t => {
  const needs = mainSuccessNeeds();
  const evidence = freshEvidence(t, needs);
  const advisory = evidence.roots.get('markdown-link-check');
  writeFileSync(advisory.path, '{');
  let summary = buildSummary(needs, contract, 'main', evidence.context);
  assert.equal(summary.report.status, 'success');
  assert.ok(summary.report.warnings.includes('markdown-link-check: Malformed execution receipt artifact'));
  rmSync(advisory.directory, { recursive: true });
  summary = buildSummary(needs, contract, 'main', evidence.context);
  assert.equal(summary.report.status, 'success');
  assert.ok(summary.report.warnings.includes('markdown-link-check: Missing current-attempt output receipt'));
  writeFileSync(evidence.roots.get('python-lint').path, '{');
  assert.ok(buildSummary(needs, contract, 'main', evidence.context).report.errors.includes('python-lint: Malformed execution receipt artifact'));
});

test('receipt gate: aggregate cannot hide missing or stale child artifacts behind successful root counts', t => {
  const needs = mainSuccessNeeds();
  const evidence = freshEvidence(t, needs);
  const child = evidence.artifacts.find(artifact => artifact.receipt.workflow === 'smoke-cpu' && artifact.receipt.shard === 'cpu-vla');
  child.receipt['run-attempt'] = '1';
  writeFileSync(child.path, JSON.stringify(child.receipt));
  assert.ok(buildSummary(needs, contract, 'main', evidence.context).report.errors.some(error => error.startsWith('smoke: Invalid or incomplete')));
  child.receipt['run-attempt'] = '2';
  child.receipt['first-failure'] = 'smoke';
  writeFileSync(child.path, JSON.stringify(child.receipt));
  assert.ok(buildSummary(needs, contract, 'main', evidence.context).report.errors.includes('smoke: Receipt failure identity contradicts successful execution'));
  rmSync(child.directory, { recursive: true });
  assert.ok(buildSummary(needs, contract, 'main', evidence.context).report.errors.some(error => error.startsWith('smoke: Invalid or incomplete')));
});

test('receipt gate: aggregate output counts and child declarations must match fresh raw receipts', t => {
  const needs = mainSuccessNeeds();
  const evidence = freshEvidence(t, needs);
  const root = evidence.roots.get('smoke-cpu');
  root.receipt.counts.expected = 1;
  writeFileSync(root.path, JSON.stringify(root.receipt));
  assert.ok(buildSummary(needs, contract, 'main', evidence.context).report.errors.includes('smoke: Output receipt counts contradict validated execution'));
  root.receipt.counts.expected = 12;
  root.receipt.children.pop();
  writeFileSync(root.path, JSON.stringify(root.receipt));
  assert.ok(buildSummary(needs, contract, 'main', evidence.context).report.errors.includes('smoke: Aggregate receipt contradicts validated children'));
});

test('receipt gate: downloaded raw report changes invalidate an otherwise successful receipt', t => {
  const needs = mainSuccessNeeds();
  const evidence = freshEvidence(t, needs);
  const root = evidence.roots.get('pytest-training');
  writeFileSync(join(root.directory, 'reports', 'logs', 'pytest-training-results.xml'),
    '<testsuite tests="1"><testcase name="different"/></testsuite>');
  assert.ok(buildSummary(needs, contract, 'main', evidence.context).report.errors.some(error => error.startsWith('pytest-training: Invalid or incomplete')));
});

test('receipt gate: PR image expectations come from verified selection rather than aggregate claims', t => {
  const needs = outcomeFixture(t);
  const evidence = freshEvidence(t, needs, 'pr');
  assert.equal(buildSummary(needs, contract, 'pr', evidence.context).report.status, 'success');
  needs.changes.outputs.vla = 'true';
  const reasons = JSON.parse(needs.changes.outputs.selection_reasons);
  reasons.vla = 'path-or-dependency-match';
  needs.changes.outputs.selection_reasons = JSON.stringify(reasons);
  assert.ok(buildSummary(needs, contract, 'pr', evidence.context).report.errors.some(error => error.startsWith('smoke: Invalid or incomplete')));
});

test('receipt gate CLI: advisory absence warns but retained success outputs and stale attempts fail', t => {
  const needs = mainSuccessNeeds();
  const evidence = freshEvidence(t, needs);
  const output = join(evidence.workspace, 'summary.json');
  const run = overrides => {
    const result = spawnSync(process.execPath, [evaluatorPath], {
      cwd: evidence.workspace, encoding: 'utf8', env: {
        ...process.env, ...evidence.env, NEEDS_JSON: JSON.stringify(needs), CI_OWNER: 'main',
        CI_SUMMARY_PATH: output, ...overrides,
      },
    });
    return { exitCode: result.status, report: JSON.parse(readFileSync(output, 'utf8')) };
  };
  rmSync(evidence.roots.get('markdown-link-check').directory, { recursive: true });
  let result = run({});
  assert.equal(result.exitCode, 0);
  assert.ok(result.report.warnings.includes('markdown-link-check: Missing current-attempt output receipt'));
  rmSync(evidence.roots.get('pytest-training').directory, { recursive: true });
  result = run({});
  assert.equal(result.exitCode, 1);
  assert.deepEqual(result.report.errors, ['pytest-training: Missing current-attempt output receipt']);
  result = run({ GITHUB_RUN_ATTEMPT: '3' });
  assert.equal(result.exitCode, 1);
  assert.ok(result.report.errors.some(error => error.includes('Stale or mismatched output receipt identity')));
});

test('summary tools: read current attempt producer versions without copying arbitrary fields', t => {
  const cwd = temporaryDirectory(t);
  const receipt = {
    workflow: 'pytest-training', job: 'pytest-training', shard: 'default',
    'run-id': '10', 'run-attempt': '2', sha: unknownSha,
    tools: { node: 'v24.14.1', git: 'git version 2.50.0', SECRET_TOKEN: 'not-for-publication' },
    steps: { secret: 'not-for-publication' },
  };
  writeFixture(cwd, 'ci-outcome-training/receipt.json', JSON.stringify(receipt));
  const producerTools = readReceiptTools(cwd, { runId: '10', runAttempt: '2', head: unknownSha });
  assert.deepEqual(producerTools[0].versions, { node: 'v24.14.1', git: 'git version 2.50.0' });
  const needs = Object.fromEntries(visibleLanes(contract, 'main').map(lane => [lane.owners.main, { result: 'success' }]));
  const { context, roots } = freshEvidence(t, needs);
  roots.get('pytest-training').receipt.tools.SECRET_TOKEN = 'not-for-publication';
  writeFileSync(roots.get('pytest-training').path, JSON.stringify(roots.get('pytest-training').receipt));
  const summary = buildSummary(needs, contract, 'main', context);
  assert.ok(summary.markdown.includes('### Producer tool versions'));
  assert.ok(summary.markdown.includes('git version 2.50.0'));
  assert.ok(!JSON.stringify(summary).includes('not-for-publication'));
  assert.throws(() => readReceiptTools(cwd, { runId: '10', runAttempt: '3', head: unknownSha }), /Stale summary receipt/);
  receipt.job = 'bad\njob';
  writeFixture(cwd, 'ci-outcome-training/receipt.json', JSON.stringify(receipt));
  assert.throws(() => readReceiptTools(cwd, { runId: '10', runAttempt: '2', head: unknownSha }), /Invalid summary producer/);
});

test('summary tools: reject loose files and malformed receipts', t => {
  const cwd = temporaryDirectory(t);
  writeFixture(cwd, 'untrusted.txt', 'bad');
  assert.throws(() => readReceiptTools(cwd, {}), /Invalid summary artifact directory/);
  rmSync(join(cwd, 'untrusted.txt'));
  writeFixture(cwd, 'artifact/receipt.json', '{');
  assert.throws(() => readReceiptTools(cwd, {}));
});

test('summary CLI: missing receipt directory fails closed and persists JSON and Markdown', t => {
  const cwd = temporaryDirectory(t);
  const needs = outcomeFixture(t);
  const evidence = freshEvidence(t, needs, 'pr');
  const result = spawnSync(process.execPath, [evaluatorPath], {
    cwd, encoding: 'utf8', env: {
      ...process.env, ...evidence.env, NEEDS_JSON: JSON.stringify(needs), CI_OWNER: 'pr',
      CI_RECEIPTS_DIRECTORY: join(cwd, 'missing'), GITHUB_STEP_SUMMARY: join(cwd, 'step-summary.md'),
    },
  });
  assert.equal(result.status, 1, result.stderr);
  const report = JSON.parse(readFileSync(join(cwd, 'logs/ci-validation-summary.json'), 'utf8'));
  assert.equal(report.status, 'failure');
  assert.ok(report.errors.some(error => error.includes('Missing current-attempt output receipt')));
  assert.ok(readFileSync(join(cwd, 'logs/ci-validation-summary.md'), 'utf8').includes('Reporting warnings'));
  assert.ok(readFileSync(join(cwd, 'step-summary.md'), 'utf8').includes('PR validation: failure'));
});

test('soft-fail policy: explicit false overrides a mutated true callee default', () => {
  const candidate = { graph: structuredClone(graph), contract: structuredClone(contract) };
  candidate.graph['.github/workflows/go-tests.yml'].on.workflow_call.inputs['soft-fail'].default = true;
  candidate.graph[prPath].jobs['go-tests'].with['soft-fail'] = false;
  candidate.graph[mainPath].jobs['go-tests'].with['soft-fail'] = false;
  assert.deepEqual(validateWorkflows(candidate.graph, candidate.contract), []);
});

test('soft-fail policy: every declared advisory exception passes unchanged', () => {
  for (const id of ['markdown-link-check', 'container-scan', 'terraform-security', 'terraform-tests', 'terraform-docs-check']) {
    const lane = contract.lanes.find(candidate => candidate.id === id);
    assert.ok(Object.values(lane.softFailPolicy).every(Boolean), `${id} must remain explicitly advisory`);
  }
  assert.deepEqual(validateWorkflows(graph, contract), []);
});

test('release policy: an explicit success condition is allowed', () => {
  const candidate = structuredClone(graph);
  candidate[mainPath].jobs['release-please'].if = 'success()';
  assert.deepEqual(validateWorkflows(candidate, contract), []);
});

test('evaluator CLI: valid needs succeed and malformed or missing input fails closed', t => {
  const needs = outcomeFixture(t);
  const evidence = freshEvidence(t, needs, 'pr');
  for (const [input, status, diagnostic] of [
    [JSON.stringify(needs), 0, 'All mandatory checks match'],
    ['{', 1, 'Expected property name'],
    [undefined, 1, 'Missing needs object'],
  ]) {
    const env = { ...process.env, ...evidence.env, CI_SUMMARY_PATH: join(temporaryDirectory(t), 'summary.json') };
    if (input === undefined) delete env.NEEDS_JSON;
    else env.NEEDS_JSON = input;
    const result = spawnSync(process.execPath, [evaluatorPath], { cwd: root, env, encoding: 'utf8' });
    assert.ifError(result.error);
    assert.equal(result.status, status, result.stderr);
    assert.ok(`${result.stdout}${result.stderr}`.includes(diagnostic));
  }
});

test('validator CLI: repository succeeds and a missing workflow directory fails closed', t => {
  const success = spawnSync(process.execPath, [validatorPath], { cwd: root, encoding: 'utf8' });
  assert.ifError(success.error);
  assert.equal(success.status, 0, success.stderr);
  assert.ok(success.stdout.includes('CI workflow graph and dependency contract are valid.'));
  const failure = spawnSync(process.execPath, [validatorPath], { cwd: temporaryDirectory(t), encoding: 'utf8' });
  assert.ifError(failure.error);
  assert.equal(failure.status, 1);
  assert.ok(failure.stderr.includes('.github'));
});
