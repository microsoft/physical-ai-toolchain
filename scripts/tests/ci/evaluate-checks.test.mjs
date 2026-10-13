// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

import assert from 'node:assert/strict';
import test from 'node:test';
import { evaluateChecks, requiredLanes, visibleLanes } from '../../ci/evaluate-checks.mjs';

const contract = {
  selectors: { training: {}, docusaurus: {}, accessibility: {} },
  outcomeSchemas: {
    required: { selected: ['success'], unselected: ['success', 'skipped'] },
    advisory: { selected: ['success', 'failure', 'cancelled', 'skipped'], unselected: ['success', 'failure', 'cancelled', 'skipped'] },
  },
  execution: {
    docs: { jobs: { build: { reports: [{ kind: 'junit', path: 'logs/docs.xml' }] } } },
    smoke: { jobs: { run: { reports: [] } } },
  },
  lanes: [
    { id: 'changes', owners: { pr: 'changes' }, classification: 'discovery', selector: 'always', outcomeSchema: 'required' },
    { id: 'smoke', owners: { pr: 'smoke', main: 'smoke' }, classification: 'mandatory', selector: 'always', outcomeSchema: 'required', target: 'smoke.yml' },
    { id: 'training', owners: { pr: 'pytest-training', main: 'pytest-training' }, classification: 'mandatory', selector: 'training', outcomeSchema: 'required' },
    { id: 'docs', owners: { pr: 'docusaurus-tests', main: 'docusaurus-tests' }, classification: 'mandatory', selector: 'docusaurus', outcomeSchema: 'required', target: 'docs.yml' },
    { id: 'accessibility', owners: { pr: 'accessibility-evidence' }, classification: 'mandatory', selector: 'accessibility', outcomeSchema: 'required' },
    { id: 'osv', owners: { pr: 'osv-scanner' }, classification: 'advisory', selector: 'always', outcomeSchema: 'advisory' },
    { id: 'terraform', owners: { pr: 'terraform-security' }, classification: 'advisory', selector: 'always', outcomeSchema: 'advisory' },
    { id: 'publish', owners: { main: 'publish' }, classification: 'mandatory', selector: 'always', outcomeSchema: 'required' },
    { id: 'release', owners: { main: 'release-please' }, classification: 'release' },
  ],
};

function executionOutputs(overrides = {}) {
  return {
    'work-status': 'success', 'expected-count': '2', 'executed-count': '2', 'test-count': '3',
    'skipped-count': '0', 'first-failure': '', 'artifact-id': '10',
    'artifact-url': 'https://github.com/example-org/example-repo/actions/runs/123/artifacts/10',
    ...overrides,
  };
}

function plannedSkipOutputs(overrides = {}) {
  return executionOutputs({
    'work-status': 'planned-skip', 'expected-count': '0', 'executed-count': '0', 'test-count': '0', ...overrides,
  });
}

function prNeeds({ all = false, empty = false } = {}) {
  const selected = { training: all, docusaurus: !empty, accessibility: all };
  const outputs = {
    selection_status: empty ? 'verified-empty' : 'verified',
    base_sha: '1'.repeat(40),
    head_sha: '2'.repeat(40),
    file_count: empty ? '0' : '1',
    selection_reasons: JSON.stringify(Object.fromEntries(Object.entries(selected).map(([selector, value]) =>
      [selector, value ? 'path-or-dependency-match' : 'not-selected']))),
    ...Object.fromEntries(Object.entries(selected).map(([selector, value]) => [selector, String(value)])),
  };
  const lane = isSelected => isSelected ? { result: 'success', outputs: executionOutputs() } : { result: 'skipped' };
  return {
    changes: { result: 'success', outputs },
    smoke: lane(true),
    'pytest-training': lane(selected.training),
    'docusaurus-tests': lane(selected.docusaurus),
    'accessibility-evidence': lane(selected.accessibility),
  };
}

function mainNeeds() {
  return Object.fromEntries(requiredLanes(contract, 'main').map(lane =>
    [lane.owners.main, { result: 'success', outputs: executionOutputs() }]));
}

// Exact error count plus one identifying pattern per expected diagnostic.
function assertOnly(errors, patterns) {
  assert.equal(errors.length, patterns.length, JSON.stringify(errors));
  for (const pattern of patterns) {
    assert.ok(errors.some(error => pattern.test(error)), `Expected ${pattern}; received ${JSON.stringify(errors)}`);
  }
}

// Rejection with identifying diagnostics when one defect legitimately causes dependent errors.
function assertRejects(errors, patterns) {
  assert.ok(errors.length > 0, 'Expected the evaluation to fail closed');
  for (const pattern of patterns) {
    assert.ok(errors.some(error => pattern.test(error)), `Expected ${pattern}; received ${JSON.stringify(errors)}`);
  }
}

test('lanes: required lanes include discovery and mandatory jobs only for the requested owner', () => {
  assert.deepEqual(requiredLanes(contract, 'pr').map(lane => lane.id).sort(),
    ['accessibility', 'changes', 'docs', 'smoke', 'training']);
  assert.deepEqual(requiredLanes(contract, 'main').map(lane => lane.id).sort(), ['docs', 'publish', 'smoke', 'training']);
});

test('lanes: visible lanes add advisory jobs but never release jobs', () => {
  assert.deepEqual(visibleLanes(contract, 'pr').map(lane => lane.id).sort(),
    ['accessibility', 'changes', 'docs', 'osv', 'smoke', 'terraform', 'training']);
  assert.deepEqual(visibleLanes(contract, 'main').map(lane => lane.id).sort(), ['docs', 'publish', 'smoke', 'training']);
});

test('outcomes: every selected and unconditional lane succeeds with execution evidence', () => {
  assert.deepEqual(evaluateChecks(prNeeds({ all: true }), contract), []);
});

test('outcomes: verified unselected lanes may skip without execution evidence', () => {
  assert.deepEqual(evaluateChecks(prNeeds(), contract), []);
});

test('outcomes: verified-empty comparisons still require unconditional success', () => {
  const needs = prNeeds({ empty: true });
  assert.deepEqual(evaluateChecks(needs, contract), []);
  needs.smoke.result = 'skipped';
  assertOnly(evaluateChecks(needs, contract), [/^smoke:.*skipped/]);
});

const resultCases = [
  ['success', true, true],
  ['skipped', false, true],
  ['failure', false, false],
  ['cancelled', false, false],
  [undefined, false, false],
  ['unknown', false, false],
];

for (const [role, job, outputs] of [
  ['selected', 'docusaurus-tests', executionOutputs],
  ['unselected', 'pytest-training', plannedSkipOutputs],
  ['unconditional', 'smoke', executionOutputs],
  ['discovery', 'changes', null],
]) {
  for (const [result, requiredPasses, unselectedPasses] of resultCases) {
    const passes = role === 'unselected' ? unselectedPasses : requiredPasses;
    test(`outcomes: ${role} job ${result ?? 'missing result'} ${passes ? 'passes' : 'fails'}`, () => {
      const needs = prNeeds();
      if (outputs) needs[job] = { outputs: outputs() };
      needs[job].result = result;
      const errors = evaluateChecks(needs, contract);
      if (passes) assert.deepEqual(errors, []);
      else assertOnly(errors, [new RegExp(`^${job}:.*${result ?? 'missing'}`)]);
    });
  }
}

test('outcomes: a missing required job fails', () => {
  const needs = prNeeds();
  delete needs.smoke;
  assertRejects(evaluateChecks(needs, contract), [/^smoke:.*missing/]);
});

test('selection evidence: missing selector outputs fail closed', () => {
  const needs = prNeeds();
  delete needs.changes.outputs;
  assertRejects(evaluateChecks(needs, contract), [/selection evidence/]);
});

const selectionMutations = [
  ['unverified full comparison metadata', outputs => { outputs.selection_status = 'full'; }, [/selection evidence/]],
  ['symbolic base SHA', outputs => { outputs.base_sha = 'HEAD'; }, [/comparison SHAs/]],
  ['missing head SHA', outputs => { delete outputs.head_sha; }, [/comparison SHAs/]],
  ['truncated head SHA', outputs => { outputs.head_sha = '2'.repeat(39); }, [/comparison SHAs/]],
  ['non-hexadecimal head SHA', outputs => { outputs.head_sha = 'g'.repeat(40); }, [/comparison SHAs/]],
  ['negative file count', outputs => { outputs.file_count = '-1'; }, [/comparison file count/]],
  ['numeric file count', outputs => { outputs.file_count = 1; }, [/comparison file count/]],
  ['unsafe integer file count', outputs => { outputs.file_count = '9007199254740993'; }, [/comparison file count/]],
  ['verified status with no changed files', outputs => { outputs.file_count = '0'; }, [/comparison file count/]],
  ['verified-empty status with changed files', outputs => { outputs.selection_status = 'verified-empty'; },
    [/comparison file count/, /Nonempty selection.*docusaurus/]],
  ['verified-empty metadata with a selected lane', outputs => {
    outputs.selection_status = 'verified-empty';
    outputs.file_count = '0';
  }, [/Nonempty selection.*docusaurus/]],
  ['boolean instead of string selection output', outputs => { outputs.training = true; }, [/selection output: training/]],
  ['missing selection reasons', outputs => { delete outputs.selection_reasons; },
    [/selection reason: training/, /selection reason: docusaurus/, /selection reason: accessibility/]],
  ['malformed selection reasons', outputs => { outputs.selection_reasons = '{'; },
    [/selection reason: training/, /selection reason: docusaurus/, /selection reason: accessibility/]],
  ['non-object selection reasons', outputs => { outputs.selection_reasons = '[]'; },
    [/selection reason: training/, /selection reason: docusaurus/, /selection reason: accessibility/]],
  ['selected lane explained as not selected', outputs => {
    outputs.selection_reasons = JSON.stringify({ ...JSON.parse(outputs.selection_reasons), docusaurus: 'not-selected' });
  }, [/selection reason: docusaurus/]],
  ['unselected lane explained as a path match', outputs => {
    outputs.selection_reasons = JSON.stringify({ ...JSON.parse(outputs.selection_reasons), training: 'path-or-dependency-match' });
  }, [/selection reason: training/]],
];

for (const [name, mutate, expected] of selectionMutations) {
  test(`selection evidence: rejects ${name}`, () => {
    const needs = prNeeds();
    mutate(needs.changes.outputs);
    assertOnly(evaluateChecks(needs, contract), expected);
  });
}

for (const length of [40, 64]) {
  test(`selection evidence: ${length}-character hexadecimal SHAs are accepted in either case`, () => {
    const needs = prNeeds();
    needs.changes.outputs.base_sha = 'A'.repeat(length);
    needs.changes.outputs.head_sha = 'b'.repeat(length);
    assert.deepEqual(evaluateChecks(needs, contract), []);
  });
}

test('selection evidence: shared CI changes are a valid reason for selection', () => {
  const needs = prNeeds();
  needs.changes.outputs.selection_reasons = JSON.stringify({
    ...JSON.parse(needs.changes.outputs.selection_reasons), docusaurus: 'shared-ci-change',
  });
  assert.deepEqual(evaluateChecks(needs, contract), []);
});

test('execution evidence: a green job without published evidence fails', () => {
  const needs = prNeeds();
  delete needs['docusaurus-tests'].outputs;
  assertRejects(evaluateChecks(needs, contract), [/^docusaurus-tests:.*work-status.*missing/]);
});

test('execution evidence: a missing count fails closed', () => {
  const needs = prNeeds();
  delete needs.smoke.outputs['expected-count'];
  assertRejects(evaluateChecks(needs, contract), [/^smoke:.*expected-count/]);
});

const evidenceMutations = [
  ['selected lane reporting a planned skip', 'docusaurus-tests', { 'work-status': 'planned-skip' }, /work-status.*planned-skip/],
  ['cancelled work behind a green result', 'docusaurus-tests', { 'work-status': 'cancelled' }, /work-status.*cancelled/],
  ['missing work status', 'smoke', { 'work-status': undefined }, /work-status.*missing/],
  ['partial operations', 'smoke', { 'executed-count': '1' }, /incomplete execution counts/],
  ['no expected work for a selected lane', 'smoke', { 'expected-count': '0', 'executed-count': '0' }, /incomplete execution counts/],
  ['unsafe integer test count', 'smoke', { 'test-count': '9007199254740992' }, /test-count/],
  ['negative skipped count', 'smoke', { 'skipped-count': '-1' }, /skipped-count/],
  ['numeric executed count', 'smoke', { 'executed-count': 2 }, /executed-count/],
  ['zero testcases where test reports are declared', 'docusaurus-tests', { 'test-count': '0' }, /no executed testcases/],
  ['missing artifact identity', 'smoke', { 'artifact-id': undefined }, /artifact identity/],
  ['artifact URL for another artifact', 'smoke', { 'artifact-id': '11' }, /artifact identity/],
  ['non-GitHub artifact link', 'smoke', { 'artifact-url': 'javascript:alert(1)' }, /artifact identity/],
  ['failure recorded behind a green result', 'smoke', { 'first-failure': 'smoke' }, /first-failure/],
];

for (const [name, job, overrides, pattern] of evidenceMutations) {
  test(`execution evidence: rejects ${name}`, () => {
    const needs = prNeeds();
    needs[job].outputs = executionOutputs(overrides);
    for (const [field, value] of Object.entries(overrides)) if (value === undefined) delete needs[job].outputs[field];
    assertOnly(evaluateChecks(needs, contract), [new RegExp(`^${job}:.*${pattern.source}`)]);
  });
}

test('execution evidence: an omitted empty first-failure output is accepted', () => {
  const needs = prNeeds();
  delete needs.smoke.outputs['first-failure'];
  assert.deepEqual(evaluateChecks(needs, contract), []);
});

test('execution evidence: zero testcases are accepted when the lane declares no test reports', () => {
  const needs = prNeeds();
  needs.smoke.outputs['test-count'] = '0';
  assert.deepEqual(evaluateChecks(needs, contract), []);
});

test('planned skip: an unselected job may succeed with a zero-work receipt', () => {
  const needs = prNeeds();
  needs['pytest-training'] = { result: 'success', outputs: plannedSkipOutputs() };
  assert.deepEqual(evaluateChecks(needs, contract), []);
});

for (const [name, overrides, pattern] of [
  ['executed testcases', { 'test-count': '2' }, /planned skip has executed testcases/],
  ['expected work', { 'expected-count': '2' }, /incomplete execution counts/],
  ['a success work status', { 'work-status': 'success' }, /work-status planned-skip/],
]) {
  test(`planned skip: rejects a receipt with ${name}`, () => {
    const needs = prNeeds();
    needs['pytest-training'] = { result: 'success', outputs: plannedSkipOutputs(overrides) };
    assertOnly(evaluateChecks(needs, contract), [new RegExp(`^pytest-training:.*${pattern.source}`)]);
  });
}

test('owners: main requires every main lane without PR selection metadata', () => {
  assert.deepEqual(evaluateChecks(mainNeeds(), contract, 'main'), []);
});

test('owners: main ignores PR selection and treats every lane as selected', () => {
  const needs = mainNeeds();
  needs.changes = { result: 'success', outputs: { selection_status: 'verified', training: 'false' } };
  needs['pytest-training'].result = 'skipped';
  assertOnly(evaluateChecks(needs, contract, 'main'), [/^pytest-training:.*skipped/]);
});

test('owners: main requires execution evidence from every lane', () => {
  const needs = mainNeeds();
  needs.publish.outputs['executed-count'] = '1';
  assertOnly(evaluateChecks(needs, contract, 'main'), [/^publish:.*incomplete execution counts/]);
});

test('owners: an unknown owner cannot pass', () => {
  assert.deepEqual(evaluateChecks(prNeeds(), contract, 'release'), ['Unknown validation owner']);
});

for (const [name, needs] of [
  ['missing', undefined], ['null', null], ['array', []], ['boolean', true], ['number', 1], ['string', 'success'],
]) {
  test(`needs: ${name} needs cannot count as a successful run`, () => {
    assertOnly(evaluateChecks(needs, contract), [/needs object/]);
  });
}

test('policy: advisory failures cannot block the required aggregate', () => {
  const needs = prNeeds();
  needs['osv-scanner'] = { result: 'failure' };
  needs['terraform-security'] = { result: 'cancelled' };
  assert.deepEqual(evaluateChecks(needs, contract), []);
});

test('policy: main-only, release and unrelated results do not block the PR aggregate', () => {
  const needs = prNeeds();
  needs.publish = { result: 'failure' };
  needs['release-please'] = { result: 'failure' };
  needs.unrelated = { result: 'failure' };
  assert.deepEqual(evaluateChecks(needs, contract), []);
});

test('policy: the supplied classification determines whether a scanner failure blocks', () => {
  const policy = structuredClone(contract);
  Object.assign(policy.lanes.find(lane => lane.id === 'osv'), { classification: 'mandatory', outcomeSchema: 'required' });
  const needs = prNeeds();
  needs['osv-scanner'] = { result: 'failure', outputs: executionOutputs() };
  assertOnly(evaluateChecks(needs, policy), [/^osv-scanner:.*failure/]);
});

test('policy: a selected lane declared advisory does not block on failure', () => {
  const policy = structuredClone(contract);
  policy.lanes.find(lane => lane.id === 'docs').classification = 'advisory';
  const needs = prNeeds();
  needs['docusaurus-tests'].result = 'failure';
  assert.deepEqual(evaluateChecks(needs, policy), []);
});

test('policy: renamed job and selector identities are resolved from the supplied contract', () => {
  const policy = structuredClone(contract);
  const lane = policy.lanes.find(candidate => candidate.id === 'docs');
  lane.owners.pr = 'site-check';
  lane.selector = 'site';
  delete policy.selectors.docusaurus;
  policy.selectors.site = {};
  const needs = prNeeds();
  const outputs = needs.changes.outputs;
  const reasons = JSON.parse(outputs.selection_reasons);
  reasons.site = reasons.docusaurus;
  delete reasons.docusaurus;
  outputs.selection_reasons = JSON.stringify(reasons);
  outputs.site = outputs.docusaurus;
  delete outputs.docusaurus;
  needs['site-check'] = needs['docusaurus-tests'];
  delete needs['docusaurus-tests'];
  assert.deepEqual(evaluateChecks(needs, policy), []);
  needs['site-check'].result = 'skipped';
  assertOnly(evaluateChecks(needs, policy), [/^site-check:.*skipped/]);
});

test('diagnostics: invalid evidence and every failed required job are reported together', () => {
  const needs = prNeeds();
  needs.changes.outputs.base_sha = 'HEAD';
  needs.smoke.result = 'cancelled';
  needs['docusaurus-tests'].result = 'failure';
  assertOnly(evaluateChecks(needs, contract), [/comparison SHAs/, /^smoke:.*cancelled/, /^docusaurus-tests:.*failure/]);
});

test('diagnostics: repeated evaluation is stable and preserves caller-owned evidence and policy', () => {
  const needs = prNeeds();
  needs.smoke.result = 'failure';
  const before = structuredClone({ needs, contract });
  const errors = evaluateChecks(needs, contract);
  assertOnly(errors, [/^smoke:.*failure/]);
  assert.deepEqual(evaluateChecks(needs, contract), errors);
  assert.deepEqual({ needs, contract }, before);
});
