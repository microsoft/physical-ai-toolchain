// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { evaluateChecks, requiredLanes } from '../../ci/evaluate-checks.mjs';
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

function assertSelection(actual, selected) {
  assert.deepEqual(actual, expectedSelection(selected));
}

function temporaryDirectory(t) {
  const directory = mkdtempSync(join(tmpdir(), 'ci-contract-'));
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
  for (const [owner, path, aggregate] of [['pr', prPath, summaryId], ['main', mainPath, 'release-please']]) {
    assert.deepEqual([...graph[path].jobs[aggregate].needs].sort(), requiredLanes(contract, owner).map(lane => lane.owners[owner]).sort());
  }
  const osv = contract.lanes.find(lane => lane.id === 'osv-scanner');
  assert.equal(osv.classification, 'advisory');
  assert.equal(osv.outcomeSchema, 'advisory');
  assert.equal(graph[prPath].jobs[summaryId].needs.includes('osv-scanner'), false);
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
  ['missing mandatory main aggregate need', ({ main }) => { main['release-please'].needs = main['release-please'].needs.filter(id => id !== 'uv-lock-consistency'); }, 'aggregate must include exactly all mandatory jobs'],
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

function outcomeFixture(t, { all = false, empty = false } = {}) {
  const repo = createRepository(t);
  if (!empty) writeFixture(repo.cwd, all ? '.github/workflows/fixture.yml' : 'docs/guide.md', fileContent);
  const head = repo.commit();
  const outputs = selectionOutputs({ ...repo, head, contract });
  const needs = Object.fromEntries(requiredLanes(contract, 'pr').map(lane => [lane.owners.pr, {
    result: lane.selector === 'always' || outputs[lane.selector] === 'true' ? 'success' : 'skipped',
  }]));
  needs.changes.outputs = outputs;
  return needs;
}

test('outcomes: every selected and unconditional lane succeeds', t => {
  const needs = outcomeFixture(t, { all: true });
  assert.ok(Object.values(needs).every(job => job.result === 'success'));
  assert.deepEqual(evaluateChecks(needs, contract), []);
});

test('outcomes: verified unselected lanes may legitimately skip', t => {
  const needs = outcomeFixture(t);
  assert.equal(needs.changes.outputs.training, 'false');
  assert.equal(needs['pytest-training'].result, 'skipped');
  assert.equal(needs['docusaurus-tests'].result, 'success');
  assert.deepEqual(evaluateChecks(needs, contract), []);
});

const outcomeMutations = [
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
  for (const [input, status, diagnostic] of [
    [JSON.stringify(needs), 0, 'All mandatory checks match'],
    ['{', 1, 'Expected property name'],
    [undefined, 1, 'Missing needs object'],
  ]) {
    const env = { ...process.env };
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
