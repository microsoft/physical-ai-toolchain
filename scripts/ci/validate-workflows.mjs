// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { isDeepStrictEqual } from 'node:util';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseDocument } from 'yaml';
import { loadContract } from './select-checks.mjs';
import { visibleLanes } from './evaluate-checks.mjs';

const array = value => value === undefined ? [] : Array.isArray(value) ? value : [value];
const expression = value => String(value ?? '').replace(/^\s*\$\{\{\s*|\s*\}\}\s*$/g, '').trim();
const events = workflow => typeof workflow.on === 'string' ? [workflow.on] : Array.isArray(workflow.on) ? workflow.on : Object.keys(workflow.on ?? {});
const executableEvents = workflow => events(workflow).filter(event => event !== 'workflow_call');
const isObject = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const hasOwn = (value, key) => Object.hasOwn(value ?? {}, key);
const evidenceOutputs = ['work-status', 'expected-count', 'executed-count', 'artifact-id', 'artifact-url', 'test-count', 'skipped-count', 'first-failure'];
const outcomeAction = './.github/actions/ci-outcome';

function jsonInput(value) {
  try { return JSON.parse(value); } catch { return undefined; }
}

function timeoutLimit(workflow, job) {
  if (/aggregate|summary|discover|outcome/.test(job)) return 10;
  if (workflow === 'gpu-offload-kind-smoke' || /pytest|pytests|frontend-tests|pester-tests|go-tests|fuzz-regression|terraform/.test(workflow)) return 20;
  if (workflow === 'docusaurus-tests') return 15;
  if (workflow === 'smoke-cpu') return job === 'import-smoke' ? 10 : job === 'build-smoke-dataviewer' ? 20 : 15;
  return 10;
}

function matrixShards(matrix, template) {
  let rows = [{}];
  for (const [axis, values] of Object.entries(matrix ?? {})) {
    if (axis === 'include') continue;
    if (!Array.isArray(values)) return undefined;
    rows = rows.flatMap(row => values.map(value => ({ ...row, [axis]: value })));
  }
  if (matrix?.include) {
    if (!Array.isArray(matrix.include)) return undefined;
    rows = Object.keys(matrix).length === 1 ? matrix.include : [...rows, ...matrix.include];
  }
  return rows.map(row => template.replace(/\$\{\{\s*matrix\.(\w+)\s*\}\}/g, (_, axis) => String(row[axis] ?? 'missing')));
}

function aggregateInventory(spec, aggregate) {
  let declaration = aggregate['expected-shards'] ?? '';
  declaration = declaration.replace(/\$\{\{\s*inputs\.\w+\s*&&\s*'(\[[^']*\])'\s*\|\|\s*'\[\]'\s*\}\}/g, '$1');
  for (const metadata of Object.values(spec.jobs)) {
    if (metadata.discovery) {
      const discovery = metadata.discovery;
      declaration = declaration.replaceAll(`\${{ needs.${discovery.job}.outputs.${discovery['shards-output']} || '[]' }}`, '[]');
    }
  }
  return jsonInput(declaration);
}

function validateExecution(graph, contract, check) {
  const execution = contract.execution;
  check(isObject(execution), 'Missing execution contract');
  const targets = new Set(contract.lanes.filter(lane => lane.target).map(lane => lane.target.replace(/\.ya?ml$/, '')));
  for (const workflow of Object.keys(execution ?? {})) check(targets.has(workflow), `${workflow}: stale execution contract`);
  for (const lane of contract.lanes.filter(lane => lane.target)) {
    const name = lane.target.replace(/\.ya?ml$/, '');
    const label = `.github/workflows/${lane.target}`;
    const workflow = graph[label];
    const spec = execution?.[name];
    if (!isObject(spec) || !isObject(spec.jobs) || Object.keys(spec.jobs).length === 0) {
      check(false, `${name}: missing execution job metadata`);
      continue;
    }
    const jobs = workflow?.jobs ?? {};
    const aggregate = spec.aggregate;
    const outputJob = spec['output-job'];
    check(typeof outputJob === 'string' && Boolean(jobs[outputJob]) && !jobs[outputJob]?.strategy?.matrix, `${name}: workflow outputs require a single non-matrix producer`);
    check(aggregate ? outputJob === aggregate.job : Object.keys(spec.jobs).length === 1 && hasOwn(spec.jobs, outputJob), `${name}: output producer must cover every execution job`);
    const outputProducer = aggregate?.producer ?? spec.jobs[outputJob]?.producer;
    for (const output of evidenceOutputs) {
      check(workflow?.on?.workflow_call?.outputs?.[output]?.value === `\${{ jobs.${outputJob}.outputs.${output} }}`, `${name}: broken reusable output ${output}`);
      check(jobs[outputJob]?.outputs?.[output] === `\${{ steps.${outputProducer}.outputs.${output} }}`, `${name}: broken producer output ${output}`);
    }
    const permissionCeiling = contract.permissionProfiles[lane.permissionProfile];
    for (const [id, job] of Object.entries(jobs)) {
      check(Number.isInteger(job['timeout-minutes']) && job['timeout-minutes'] > 0 && job['timeout-minutes'] <= timeoutLimit(name, id), `${name}:${id}: timeout must be within the execution budget`);
      const permissions = job.permissions ?? workflow.permissions;
      check(isObject(permissions) && Object.entries(permissions).every(([permission, level]) =>
        ['none', 'read', 'write'].includes(level) && (level === 'none' || permissionCeiling[permission] === level ||
          level === 'read' && permissionCeiling[permission] === 'write')), `${name}:${id}: producer permission escalation`);
      const producers = (job.steps ?? []).filter(step => step.uses === outcomeAction);
      if (producers.length) check(hasOwn(spec.jobs, id) || aggregate?.job === id, `${name}:${id}: undeclared evidence producer`);
      check(new Set((job.steps ?? []).filter(step => step.id).map(step => step.id)).size === (job.steps ?? []).filter(step => step.id).length, `${name}:${id}: duplicate step identity`);
    }
    for (const [id, metadata] of Object.entries(spec.jobs)) {
      const job = jobs[id];
      if (!job || !isObject(metadata)) { check(false, `${name}:${id}: stale execution job metadata`); continue; }
      const producers = (job.steps ?? []).filter(step => step.uses === outcomeAction);
      const producer = producers.find(step => step.id === metadata.producer);
      check(producers.length === 1 && Boolean(producer), `${name}:${id}: missing unique evidence producer`);
      check(expression(producer?.if) === 'always()' && producer?.['continue-on-error'] === undefined, `${name}:${id}: evidence publication must always execute without suppression`);
      check(producer?.with?.workflow === name && producer?.with?.['steps-json'] === '${{ toJSON(steps) }}', `${name}:${id}: incorrect producer identity or step context`);
      check(isDeepStrictEqual(jsonInput(producer?.with?.['required-steps']), metadata['required-steps']), `${name}:${id}: required operation declaration mismatch`);
      const reports = metadata.reports?.map(report => ({ ...report, path: report.path?.replaceAll('{shard}', metadata['shard-input'] ?? 'default') }));
      check(isDeepStrictEqual(jsonInput(producer?.with?.reports ?? '[]'), reports), `${name}:${id}: required report declaration mismatch`);
      check((producer?.with?.shard ?? 'default') === (metadata['shard-input'] ?? 'default') &&
        (producer?.with?.target ?? producer?.with?.shard ?? 'default') === (metadata['target-input'] ?? metadata['shard-input'] ?? 'default'), `${name}:${id}: shard or target identity mismatch`);
      check(isDeepStrictEqual(job.strategy?.matrix, metadata.matrix), `${name}:${id}: execution matrix mismatch`);
      check(metadata.discovery ? metadata.discovery.job && metadata.discovery['shards-output'] && metadata.discovery['targets-output'] :
        Array.isArray(metadata.shards) && metadata.shards.length > 0 && new Set(metadata.shards).size === metadata.shards.length, `${name}:${id}: missing or duplicate expected shards`);
      if (!metadata.discovery) {
        check(isDeepStrictEqual(metadata.shards, matrixShards(metadata.matrix, metadata['shard-input'] ?? 'default')), `${name}:${id}: canonical shards must cover the complete matrix`);
      }
      if (metadata.discovery) {
        const discovery = metadata.discovery;
        check(array(job.needs).includes(discovery.job) && Boolean(spec.jobs[discovery.job]), `${name}:${id}: dynamic matrix requires verified discovery`);
        const operation = spec.jobs[discovery.job]?.['required-steps']?.[0];
        for (const output of [discovery['shards-output'], discovery['targets-output']]) {
          check(jobs[discovery.job]?.outputs?.[output] === `\${{ steps.${operation}.outputs.${output} }}`, `${name}:${id}: discovered target output chain mismatch`);
        }
        check(Boolean(aggregate?.['expected-targets']), `${name}:${id}: aggregate requires independent target mapping`);
      }
      check(expression(job.if) === expression(metadata.if), `${name}:${id}: unexpected execution job condition`);
      check(!producer?.with?.['expected-shards'] && !producer?.with?.['needs-json'], `${name}:${id}: record producer cannot aggregate instead`);
      check(producer?.with?.selected === undefined || producer.with.selected === 'true' || producer.with.selected === true, `${name}:${id}: selected execution cannot become a planned skip`);
      const strictFailure = producer?.with?.['fail-on-error'] === undefined || String(producer.with['fail-on-error']) === 'true' ||
        producer.with['fail-on-error'] === '${{ !inputs.soft-fail }}' && Object.values(lane.softFailPolicy ?? {}).every(value => value === false);
      if (lane.classification === 'mandatory') check(strictFailure, `${name}:${id}: required producer cannot suppress evidence failure`);
      const required = metadata['required-steps'];
      check(Array.isArray(required) && required.length > 0 && new Set(required).size === required.length, `${name}:${id}: missing or duplicate required operations`);
      check(isObject(metadata.bindings) && isDeepStrictEqual(Object.keys(metadata.bindings).sort(), [...(required ?? [])].sort()), `${name}:${id}: required operation bindings incomplete`);
      for (const operation of required ?? []) {
        const step = (job.steps ?? []).find(step => step.id === operation);
        const binding = metadata.bindings?.[operation];
        check(Boolean(step) && isObject(binding) && (binding.uses ? step.uses === binding.uses && step.run === undefined :
          typeof step.run === 'string' && binding['run-sha256'] === createHash('sha256').update(step.run).digest('hex')),
        `${name}:${id}:${operation}: required operation implementation mismatch`);
        if (binding?.uses) check(binding['with-sha256'] === createHash('sha256').update(JSON.stringify(step?.with ?? {})).digest('hex'),
          `${name}:${id}:${operation}: required action input context mismatch`);
        check(step?.if === undefined || step.if === binding?.if, `${name}:${id}:${operation}: unexpected operation condition`);
        check((job.steps ?? []).indexOf(step) < (job.steps ?? []).indexOf(producer), `${name}:${id}:${operation}: evidence precedes operation`);
      }
      check(Array.isArray(metadata.reports) && metadata.reports.every(report => isObject(report) &&
        ['junit', 'nunit', 'jest', 'json', 'file', 'sarif'].includes(report.kind) && typeof report.path === 'string' && report.path.length > 0),
      `${name}:${id}: invalid required report metadata`);
    }
    if (aggregate) {
      const job = jobs[aggregate.job];
      const producers = job?.steps?.filter(step => step.uses === outcomeAction) ?? [];
      const producer = producers.find(step => step.id === aggregate.producer);
      check(producers.length === 1 && Boolean(producer) && !job?.strategy, `${name}: aggregate must have exactly one non-matrix producer`);
      check(expression(job?.if) === 'always()' && ['', 'always()'].includes(expression(producer?.if)) && !job?.['continue-on-error'] && !producer?.['continue-on-error'], `${name}: aggregate must always execute without suppression`);
      check(isDeepStrictEqual([...array(job?.needs)].sort(), [...(aggregate.needs ?? [])].sort()) &&
        Object.keys(spec.jobs).every(id => array(job?.needs).includes(id)), `${name}: aggregate must depend on complete execution inventory`);
      check(producer?.with?.workflow === name && producer?.with?.['needs-json'] === '${{ toJSON(needs) }}', `${name}: aggregate identity or needs context mismatch`);
      check((producer?.with?.shard ?? 'default') === (aggregate.shard ?? 'default'), `${name}: aggregate output shard mismatch`);
      check(producer?.with?.['expected-shards'] === aggregate['expected-shards'] && Boolean(aggregate['expected-shards']), `${name}: aggregate expected shards mismatch`);
      check(producer?.with?.['expected-targets'] === aggregate['expected-targets'], `${name}: aggregate expected targets mismatch`);
      const expected = aggregateInventory(spec, aggregate);
      check(isObject(expected), `${name}: unverified aggregate shard expression`);
      if (expected) {
        check(isDeepStrictEqual(Object.keys(expected).sort(), Object.keys(spec.jobs).sort()), `${name}: aggregate expected jobs mismatch`);
        for (const [id, metadata] of Object.entries(spec.jobs)) {
          if (!metadata.discovery) check(isDeepStrictEqual(expected[id], metadata.shards), `${name}:${id}: aggregate expected shard inventory mismatch`);
        }
      }
      if (lane.classification === 'mandatory') check(producer?.with?.['fail-on-error'] === undefined || String(producer.with['fail-on-error']) === 'true' ||
        producer.with['fail-on-error'] === '${{ !inputs.soft-fail }}' && Object.values(lane.softFailPolicy ?? {}).every(value => value === false), `${name}: aggregate cannot suppress evidence failure`);
    }
  }
}

function validateEvidenceAction(graph, contract, check) {
  const action = graph[contract.evidenceAction?.path];
  check(action?.runs?.using === 'composite', 'Missing shared composite evidence action');
  for (const output of evidenceOutputs) {
    const step = output.startsWith('artifact-') ? 'upload' : 'receipt';
    check(action?.outputs?.[output]?.value === `\${{ steps.${step}.outputs.${output} }}`, `Shared action output chain mismatch: ${output}`);
  }
  const receipt = action?.runs?.steps?.find(step => step.id === 'receipt');
  check(receipt?.run === contract.evidenceAction?.['receipt-command'] && Boolean(receipt?.run) &&
    expression(receipt.if) === 'always()', 'Shared action must always invoke the canonical receipt publisher');
  for (const input of ['workflow', 'shard', 'target', 'steps-json', 'required-steps', 'reports', 'selected',
    'expected-shards', 'expected-targets', 'needs-json', 'fail-on-error', 'selection-reason', 'tools']) {
    check(receipt?.env?.[`INPUT_${input.toUpperCase().replaceAll('-', '_')}`] === `\${{ inputs.${input} }}`, `Shared action input chain mismatch: ${input}`);
  }
  const upload = action?.runs?.steps?.find(step => step.id === 'upload');
  check(/^actions\/upload-artifact@[a-f0-9]{40}$/.test(upload?.uses ?? '') && !upload?.['continue-on-error'] &&
    upload.with?.path === '${{ steps.receipt.outputs.artifact-path }}' && upload.with?.name === '${{ steps.receipt.outputs.artifact-name }}' &&
    upload.with?.['if-no-files-found'] === 'error' && expression(upload.if) === "always() && steps.receipt.outputs.artifact-path != ''",
  'Shared action must publish the validated artifact without suppressing upload failure');
  const enforcement = action?.runs?.steps?.at(-1);
  check(expression(enforcement?.if) === 'always()' && !enforcement?.['continue-on-error'] && typeof enforcement?.run === 'string' &&
    createHash('sha256').update(enforcement.run).digest('hex') === contract.evidenceAction?.['enforcement-sha256'] &&
    enforcement.env?.FAIL_ON_ERROR === '${{ inputs.fail-on-error }}' &&
    enforcement.env?.WORK_STATUS === '${{ steps.receipt.outputs.work-status }}' &&
    enforcement.env?.ARTIFACT_ID === '${{ steps.upload.outputs.artifact-id }}' &&
    enforcement.env?.ARTIFACT_URL === '${{ steps.upload.outputs.artifact-url }}',
  'Shared action must enforce published status and artifact identity');
}

function effectiveBooleanInput(job, workflow, input) {
  const declaration = workflow?.on?.workflow_call?.inputs?.[input];
  if (!declaration) return { declared: false };
  if (declaration.type !== 'boolean' || typeof declaration.default !== 'boolean') return { declared: true, valid: false };
  const value = hasOwn(job.with, input) ? job.with[input] : declaration.default;
  return { declared: true, valid: typeof value === 'boolean', value };
}

export function readWorkflowGraph(root = process.cwd()) {
  const graph = {};
  const readYaml = path => {
    const document = parseDocument(readFileSync(resolve(root, path), 'utf8'), { uniqueKeys: true });
    if (document.errors.length) throw new Error(`${path}: ${document.errors.map(error => error.message).join('; ')}`);
    return document.toJS();
  };
  for (const name of readdirSync(resolve(root, '.github/workflows')).filter(name => /\.ya?ml$/.test(name))) {
    const path = `.github/workflows/${name}`;
    graph[path] = readYaml(path);
  }
  const visiting = new Set();
  const visited = new Set();
  const visitSteps = steps => {
    for (const step of steps ?? []) {
      if (!step.uses?.startsWith('./')) continue;
      const dir = step.uses.slice(2);
      if (dir.split('/').some(part => part === '..')) throw new Error(`Local action escapes repository: ${dir}`);
      const path = ['action.yml', 'action.yaml'].map(name => `${dir}/${name}`).find(path => existsSync(resolve(root, path)));
      if (!path) throw new Error(`Unresolved local action: ${step.uses}`);
      if (visiting.has(path)) throw new Error(`Cyclic local action reference: ${[...visiting, path].join(' -> ')}`);
      if (visited.has(path)) continue;
      visiting.add(path);
      try {
        const action = readYaml(path);
        graph[path] = action;
        if (action.runs?.using === 'composite') visitSteps(action.runs.steps);
      } finally {
        visiting.delete(path);
      }
      visited.add(path);
    }
  };
  for (const workflow of Object.values(graph)) {
    for (const job of Object.values(workflow?.jobs ?? {})) visitSteps(job.steps);
  }
  return graph;
}

export function validateWorkflows(graph, contract = loadContract()) {
  const errors = [];
  const check = (condition, message) => { if (!condition) errors.push(message); };
  const laneIds = new Set();
  const ownership = new Map();
  const targets = new Set();
  const classifications = ['mandatory', 'advisory', 'discovery', 'aggregate', 'release', 'deployment'];

  check(contract.version === 1, 'Unsupported contract version');
  check(isDeepStrictEqual(contract.outcomeSchemas.required, { selected: ['success'], unselected: ['success', 'skipped'] }), 'Required outcomes must fail closed');
  for (const lane of contract.lanes) {
    check(typeof lane.id === 'string' && !laneIds.has(lane.id), `Duplicate or invalid lane: ${lane.id}`);
    laneIds.add(lane.id);
    check(classifications.includes(lane.classification), `${lane.id}: invalid classification`);
    check(lane.selector === 'always' || Object.hasOwn(contract.selectors, lane.selector), `${lane.id}: unknown selector`);
    check(Object.hasOwn(contract.outcomeSchemas, lane.outcomeSchema), `${lane.id}: unknown outcome schema`);
    check(Object.hasOwn(contract.permissionProfiles, lane.permissionProfile), `${lane.id}: unknown permission profile`);
    check(Boolean(lane.target) !== Boolean(lane.inlineException), `${lane.id}: requires exactly one target or inline exception`);
    check(Object.keys(lane.owners ?? {}).length > 0, `${lane.id}: missing ownership`);
    if (['mandatory', 'discovery'].includes(lane.classification)) check(lane.outcomeSchema === 'required', `${lane.id}: mandatory outcomes cannot be advisory`);
    if (lane.target) {
      check(!targets.has(lane.target), `Duplicate target ownership: ${lane.target}`);
      targets.add(lane.target);
      const path = `.github/workflows/${lane.target}`;
      const workflow = graph[path];
      check(Boolean(workflow), `${lane.id}: unresolved target ${path}`);
      check(events(workflow ?? {}).includes('workflow_call'), `${lane.id}: missing workflow_call`);
      for (const event of events(workflow ?? {})) {
        check(['workflow_call', ...(lane.allowedEvents ?? [])].includes(event), `${path}: duplicate event ownership (${event})`);
      }
      const softFail = workflow?.on?.workflow_call?.inputs?.['soft-fail'];
      check(Boolean(softFail) === Boolean(lane.softFailPolicy), `${lane.id}: soft-fail policy declaration mismatch`);
    }
    for (const [owner, jobId] of Object.entries(lane.owners ?? {})) {
      const config = contract.orchestrators[owner];
      if (!config) { errors.push(`${lane.id}: unknown event owner ${owner}`); continue; }
      const key = `${config.path}:${jobId}`;
      check(!ownership.has(key), `Duplicate job ownership: ${key}`);
      ownership.set(key, lane);
      const job = graph[config.path]?.jobs?.[jobId];
      if (!job) { errors.push(`Missing owned job: ${key}`); continue; }
      check(isDeepStrictEqual(job.permissions, contract.permissionProfiles[lane.permissionProfile]), `${key}: permission profile mismatch`);
      if (lane.target) {
        check(job.uses === `./.github/workflows/${lane.target}` && !job.steps && !job['runs-on'], `${key}: inline duplication or incorrect reusable target`);
        check(job['continue-on-error'] === undefined, `${key}: caller cannot suppress required execution`);
        const softFail = effectiveBooleanInput(job, graph[`.github/workflows/${lane.target}`], 'soft-fail');
        if (softFail.declared) {
          check(softFail.valid, `${key}: soft-fail must resolve from a boolean caller override or callee default`);
          check(softFail.value === lane.softFailPolicy?.[owner], `${key}: effective soft-fail policy mismatch`);
        }
        const innerFilter = graph[`.github/workflows/${lane.target}`]?.on?.workflow_call?.inputs?.['changed-files-only'];
        check(!innerFilter || job.with?.['changed-files-only'] === false, `${key}: secondary changed-file filter is forbidden; explicitly disable it`);
        const expected = owner === 'pr' && lane.selector !== 'always' ? `needs.changes.outputs.${lane.selector} == 'true'` : '';
        check(expression(job.if) === expected, `${key}: selector condition mismatch`);
        if (expected) check(array(job.needs).includes('changes'), `${key}: selector dependency missing`);
      } else {
        check(!job.uses && Array.isArray(job.steps), `${key}: inline exception must remain an inline job`);
        if (owner !== 'docs') {
          for (const step of job.steps ?? []) {
            check(!/(?:scripts\/linting\/|npm (?:run )?(?:lint[:\s]|test(?:[:\s]|$))|\b(?:pytest|ruff check|terraform validate)\b)/m.test(step.run ?? ''), `${key}: inline duplication of validation logic`);
          }
        }
      }
    }
  }

  const secondaryOwnership = new Map();
  check(isObject(contract.secondaryCallers), 'secondaryCallers must be an object');
  for (const [path, config] of Object.entries(isObject(contract.secondaryCallers) ? contract.secondaryCallers : {})) {
    const errorCount = errors.length;
    check(/^\.github\/workflows\/[^/]+\.ya?ml$/.test(path), `${path}: invalid secondary caller path`);
    check(Boolean(graph[path]), `${path}: missing secondary caller workflow`);
    if (!isObject(config)) { errors.push(`${path}: invalid secondary caller declaration`); continue; }
    const validEvents = Array.isArray(config.events) && config.events.length > 0 && config.events.every(event => typeof event === 'string' && event.length > 0);
    check(validEvents, `${path}: secondary caller events must be a nonempty array of event names`);
    if (validEvents) {
      check(new Set(config.events).size === config.events.length, `${path}: secondary caller events must be unique`);
      check(!config.events.includes('workflow_call'), `${path}: workflow_call is not a secondary caller event`);
      check(isDeepStrictEqual([...config.events].sort(), executableEvents(graph[path] ?? {}).sort()), `${path}: secondary caller event mismatch`);
    }
    const validJobs = isObject(config.jobs) && Object.keys(config.jobs).length > 0;
    check(validJobs, `${path}: secondary caller jobs must be a nonempty object`);
    if (!validJobs) continue;
    for (const [id, target] of Object.entries(config.jobs)) {
      check(typeof target === 'string' && targets.has(target), `${path}:${id}: unknown secondary caller target ${target}`);
      check(graph[path]?.jobs?.[id]?.uses === `./.github/workflows/${target}`, `${path}:${id}: secondary caller target mismatch`);
    }
    if (errors.length === errorCount) {
      for (const [id, target] of Object.entries(config.jobs)) secondaryOwnership.set(`${path}:${id}`, target);
    }
  }

  const checkOwnedDescendants = (path, root, stack = []) => {
    if (stack.includes(path)) { errors.push(`Cyclic local workflow call: ${path}`); return; }
    for (const [id, job] of Object.entries(graph[path]?.jobs ?? {})) {
      if (!job.uses?.startsWith('./')) continue;
      const target = job.uses.slice(2);
      const name = target.replace('.github/workflows/', '');
      if (targets.has(name)) {
        const key = `${path}:${id}`;
        const authorized = root === path && (ownership.get(key)?.target === name || secondaryOwnership.get(key) === name);
        check(authorized, `${key}: duplicate event ownership of ${target} from executable root ${root}; caller is neither a primary owner nor an allowed secondary caller`);
      }
      checkOwnedDescendants(target, root, [...stack, path]);
    }
  };

  for (const [path, workflow] of Object.entries(graph)) {
    if (!path.startsWith('.github/workflows/')) continue;
    if (executableEvents(workflow).length > 0) checkOwnedDescendants(path, path);
    for (const [id, job] of Object.entries(workflow?.jobs ?? {})) {
      for (const need of array(job.needs)) check(Object.hasOwn(workflow.jobs, need), `${path}:${id}: unresolved needs ${need}`);
      if (job.uses?.startsWith('./')) {
        const target = job.uses.slice(2);
        check(Boolean(graph[target]), `${path}:${id}: unresolved local call ${target}`);
        check(events(graph[target] ?? {}).includes('workflow_call'), `${path}:${id}: target missing workflow_call`);
      }
    }
  }

  for (const [owner, config] of Object.entries(contract.orchestrators)) {
    const workflow = graph[config.path];
    if (!workflow) { errors.push(`Missing orchestrator: ${config.path}`); continue; }
    check(isDeepStrictEqual(events(workflow).sort(), [...config.events].sort()), `${config.path}: event ownership mismatch`);
    for (const id of Object.keys(workflow.jobs ?? {})) check(ownership.has(`${config.path}:${id}`), `${config.path}: undeclared job ${id}`);
    if (config.aggregate) {
      const aggregate = workflow.jobs?.[config.aggregate];
      const expected = visibleLanes(contract, owner).map(lane => lane.owners[owner]).sort();
      check(isDeepStrictEqual([...array(aggregate?.needs)].sort(), expected), `${config.path}: aggregate must include exactly all mandatory jobs and advisory visibility`);
      check(expression(aggregate?.if) === 'always()', `${config.path}: Required summary must always execute`);
      check(aggregate?.['continue-on-error'] === undefined, `${config.path}: Required summary cannot suppress failure`);
      check(Number.isInteger(aggregate?.['timeout-minutes']) && aggregate['timeout-minutes'] > 0 && aggregate['timeout-minutes'] <= 10, `${config.path}: summary timeout exceeds budget`);
      const evaluators = aggregate?.steps?.filter(step => step.run?.trim() === 'node scripts/ci/evaluate-checks.mjs') ?? [];
      check(evaluators.length === 1 && evaluators[0].env?.NEEDS_JSON === '${{ toJSON(needs) }}' && evaluators[0].env?.CI_OWNER === owner && expression(evaluators[0].if) === 'always()' && evaluators[0]['continue-on-error'] === undefined, `${config.path}: Summary must use one unconditional canonical evaluator with complete needs evidence`);
      check(evaluators[0]?.env?.CI_RECEIPTS_DIRECTORY === '.ci-validation-receipts' && evaluators[0]?.env?.HEAD_SHA === '${{ github.sha }}' &&
        (owner !== 'main' || evaluators[0]?.env?.BASE_SHA === '${{ github.event.before }}'), `${config.path}: summary comparison and receipt context mismatch`);
      const cancellation = aggregate?.steps?.filter(step => step.id === 'cancellation') ?? [];
      check(cancellation.length === 1 && expression(cancellation[0].if) === 'cancelled()' &&
        cancellation[0].shell === 'bash' && cancellation[0].run === 'printf \'cancelled=true\\n\' >> "$GITHUB_OUTPUT"' &&
        cancellation[0]['continue-on-error'] === undefined &&
        aggregate.steps.indexOf(cancellation[0]) < aggregate.steps.indexOf(evaluators[0]) &&
        evaluators[0]?.env?.CI_CANCELLED === "${{ steps.cancellation.outputs.cancelled || 'false' }}",
      `${config.path}: summary must preserve explicit workflow cancellation context`);
      check(aggregate?.steps?.some(step => step.uses?.startsWith('actions/upload-artifact@') && expression(step.if) === 'always()' && step.with?.['if-no-files-found'] === 'error' && step.with?.path === 'logs/ci-validation-summary.*'), `${config.path}: summary artifacts must always publish`);
      check(aggregate?.steps?.some(step => step.uses?.startsWith('actions/download-artifact@') && expression(step.if) === 'always()' &&
        step.with?.pattern === 'ci-outcome-*-${{ github.run_id }}-${{ github.run_attempt }}' &&
        step.with?.path === '.ci-validation-receipts' && step.with?.['merge-multiple'] === false), `${config.path}: summary must download current-attempt receipt evidence without merging shards`);
    }
  }

  const pr = graph[contract.orchestrators.pr.path]?.jobs ?? {};
  const changes = pr.changes;
  const filter = changes?.steps?.find(step => step.id === 'filter');
  check(filter?.if === undefined && filter?.['continue-on-error'] === undefined && changes?.if === undefined && changes?.['continue-on-error'] === undefined, 'Discovery must execute without failure suppression');
  check(filter?.env?.BASE_SHA === '${{ github.event.pull_request.base.sha }}' && filter?.env?.HEAD_SHA === '${{ github.sha }}', 'PR selection must compare event base SHA to tested merge SHA');
  check(filter?.run?.trim() === 'node scripts/ci/select-checks.mjs', 'Discovery must use the canonical selector');
  check(changes?.steps?.some(step => step.uses?.startsWith('actions/checkout@') && step.with?.['fetch-depth'] === 0 && !step.with?.ref), 'Selection checkout must fetch the tested merge tree and history');
  for (const output of [...Object.keys(contract.selectors), 'selection_status', 'base_sha', 'head_sha', 'file_count', 'selection_reasons']) {
    check(changes?.outputs?.[output] === `\${{ steps.filter.outputs.${output} }}`, `Missing selector output: ${output}`);
  }
  const summary = pr['pr-validation-summary'];
  check(expression(summary?.if) === 'always()', 'Required summary must always execute');
  check(summary?.['continue-on-error'] === undefined, 'Required summary cannot suppress failure');
  check(!summary?.name || summary.name === 'pr-validation-summary', 'Required summary check context must remain stable');
  const evaluators = summary?.steps?.filter(step => step.run?.trim() === 'node scripts/ci/evaluate-checks.mjs') ?? [];
  check(evaluators.length === 1 && evaluators[0].env?.NEEDS_JSON === '${{ toJSON(needs) }}' && expression(evaluators[0].if) === 'always()' && evaluators[0]['continue-on-error'] === undefined, 'Summary must use one unconditional canonical evaluator with complete needs evidence');
  const osv = contract.lanes.find(lane => lane.id === 'osv-scanner');
  check(osv?.classification === 'advisory' && osv?.outcomeSchema === 'advisory' && array(summary?.needs).includes('osv-scanner'), 'OSV must remain advisory in the contract and aggregate');
  check(pr['osv-scanner']?.steps?.some(step => step.uses?.startsWith('google/osv-scanner-action/') && step['continue-on-error'] === true), 'OSV must remain advisory');
  check(pr['osv-scanner']?.outputs?.['work-status'] === '${{ steps.osv-scan.outcome }}' &&
    pr['osv-scanner']?.steps?.some(step => step.id === 'osv-scan' && step.uses?.startsWith('google/osv-scanner-action/')),
  'OSV advisory summary must expose the scan outcome rather than its tolerated conclusion');

  const main = graph[contract.orchestrators.main.path]?.jobs ?? {};
  const release = main['release-please'];
  check(release?.['continue-on-error'] === undefined && ['', 'success()'].includes(expression(release?.if)), 'Release coordinator must retain success gating without failure suppression');
  check(isDeepStrictEqual(array(release?.needs), ['main-validation-summary']), 'Release coordinator must depend only on the main validation summary');
  check(graph[contract.orchestrators.pr.path]?.concurrency?.group === '${{ github.workflow }}-pr-${{ github.event.pull_request.number }}' &&
    graph[contract.orchestrators.pr.path]?.concurrency?.['cancel-in-progress'] === true, 'PR concurrency must isolate workflow and PR number and cancel superseded runs');
  check(!graph[contract.orchestrators.main.path]?.concurrency?.['cancel-in-progress'] &&
    Object.values(main).every(job => !job.concurrency?.['cancel-in-progress']), 'Main and release cancellation is forbidden');
  check(changes?.['timeout-minutes'] > 0 && changes['timeout-minutes'] <= 10 && pr['osv-scanner']?.['timeout-minutes'] > 0 && pr['osv-scanner']['timeout-minutes'] <= 10, 'Discovery and advisory jobs require bounded timeouts');
  for (const id of ['markdown-link-check', 'terraform-tests', 'terraform-docs-check', 'terraform-security']) {
    check(contract.lanes.find(lane => lane.id === id)?.classification === 'advisory', `${id}: whole-step tolerated execution must remain advisory`);
  }
  for (const id of ['container-scan', 'codeql-analysis']) {
    check(contract.lanes.find(lane => lane.id === id)?.classification === 'mandatory', `${id}: execution remains mandatory`);
  }

  const contractWorkflow = graph['.github/workflows/workflow-refs-check.yml'];
  const contractJob = contractWorkflow?.jobs?.['workflow-refs-check'];
  check(isDeepStrictEqual(Object.keys(contractWorkflow?.jobs ?? {}), ['workflow-refs-check']) && contractJob?.if === undefined && contractJob?.needs === undefined && contractJob?.['continue-on-error'] === undefined, 'CI contract job must remain present and unconditional');
  for (const command of ['npm run lint:ci', 'npm run test:ci']) {
    const steps = contractJob?.steps?.filter(step => step.run?.trim() === command) ?? [];
    check(steps.length === 1 && steps[0].if === undefined && steps[0]['continue-on-error'] === undefined, `CI contract job must run unconditional canonical command: ${command}`);
  }

  const cpu = graph[contract.cpuSmoke.workflow]?.jobs?.[contract.cpuSmoke.job];
  check(Boolean(cpu) && cpu.if === undefined && cpu.needs === undefined && cpu['continue-on-error'] === undefined, 'CPU smoke must remain unconditional');
  check(isDeepStrictEqual(cpu?.strategy?.matrix?.domain, contract.cpuSmoke.domains), 'CPU smoke domain matrix changed');
  check(isDeepStrictEqual(Object.keys(cpu?.strategy?.matrix ?? {}), ['domain']), 'CPU smoke matrix cannot exclude domains');
  check(cpu?.steps?.some(step => step.run === 'shared/ci/smoke-import.sh ${{ matrix.domain }} --mode cpu' && step.if === undefined && step['continue-on-error'] === undefined), 'CPU smoke command must remain unconditional');
  for (const [input, selectors] of Object.entries(contract.smokeInputs)) {
    const expected = selectors.map(selector => `needs.changes.outputs.${selector} == 'true'`).join(' || ');
    check(expression(pr.smoke?.with?.[input]) === expected, `PR smoke input mismatch: ${input}`);
    check(main.smoke?.with?.[input] === true, `Main must run smoke input: ${input}`);
  }
  const docs = graph[contract.orchestrators.docs.path];
  check(Boolean(docs?.on?.push) && !events(docs ?? {}).includes('workflow_run'), 'Documentation deployment must remain independent');
  validateExecution(graph, contract, check);
  validateEvidenceAction(graph, contract, check);
  return errors;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const errors = validateWorkflows(readWorkflowGraph());
    if (errors.length) throw new Error(errors.join('\n'));
    console.log('CI workflow graph and dependency contract are valid.');
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
