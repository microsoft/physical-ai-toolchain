// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { isDeepStrictEqual } from 'node:util';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseDocument } from 'yaml';
import { loadContract } from './select-checks.mjs';
import { requiredLanes } from './evaluate-checks.mjs';

const array = value => value === undefined ? [] : Array.isArray(value) ? value : [value];
const expression = value => String(value ?? '').replace(/^\s*\$\{\{\s*|\s*\}\}\s*$/g, '').trim();
const events = workflow => typeof workflow.on === 'string' ? [workflow.on] : Array.isArray(workflow.on) ? workflow.on : Object.keys(workflow.on ?? {});
const executableEvents = workflow => events(workflow).filter(event => event !== 'workflow_call');
const isObject = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const hasOwn = (value, key) => Object.hasOwn(value ?? {}, key);

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
        const innerFilter = job.with?.['changed-files-only'] ?? graph[`.github/workflows/${lane.target}`]?.on?.workflow_call?.inputs?.['changed-files-only']?.default;
        check(innerFilter === undefined || innerFilter === false, `${key}: secondary changed-file filter is forbidden`);
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
      const expected = requiredLanes(contract, owner).map(lane => lane.owners[owner]).sort();
      check(isDeepStrictEqual([...array(aggregate?.needs)].sort(), expected), `${config.path}: aggregate must include exactly all mandatory jobs`);
    }
  }

  const pr = graph[contract.orchestrators.pr.path]?.jobs ?? {};
  const changes = pr.changes;
  const filter = changes?.steps?.find(step => step.id === 'filter');
  check(filter?.if === undefined && filter?.['continue-on-error'] === undefined && changes?.if === undefined && changes?.['continue-on-error'] === undefined, 'Discovery must execute without failure suppression');
  check(filter?.env?.BASE_SHA === '${{ github.event.pull_request.base.sha }}' && filter?.env?.HEAD_SHA === '${{ github.sha }}', 'PR selection must compare event base SHA to tested merge SHA');
  check(filter?.run?.trim() === 'node scripts/ci/select-checks.mjs', 'Discovery must use the canonical selector');
  check(changes?.steps?.some(step => step.uses?.startsWith('actions/checkout@') && step.with?.['fetch-depth'] === 0 && !step.with?.ref), 'Selection checkout must fetch the tested merge tree and history');
  for (const output of [...Object.keys(contract.selectors), 'selection_status', 'base_sha', 'head_sha', 'file_count']) {
    check(changes?.outputs?.[output] === `\${{ steps.filter.outputs.${output} }}`, `Missing selector output: ${output}`);
  }
  const summary = pr['pr-validation-summary'];
  check(expression(summary?.if) === 'always()', 'Required summary must always execute');
  check(summary?.['continue-on-error'] === undefined, 'Required summary cannot suppress failure');
  check(!summary?.name || summary.name === 'pr-validation-summary', 'Required summary check context must remain stable');
  const evaluators = summary?.steps?.filter(step => step.run?.trim() === 'node scripts/ci/evaluate-checks.mjs') ?? [];
  check(evaluators.length === 1 && evaluators[0].env?.NEEDS_JSON === '${{ toJSON(needs) }}' && evaluators[0].if === undefined && evaluators[0]['continue-on-error'] === undefined, 'Summary must use one unconditional canonical evaluator with complete needs evidence');
  const osv = contract.lanes.find(lane => lane.id === 'osv-scanner');
  check(osv?.classification === 'advisory' && osv?.outcomeSchema === 'advisory' && !array(summary?.needs).includes('osv-scanner'), 'OSV must remain advisory in the contract and aggregate');
  check(pr['osv-scanner']?.steps?.some(step => step.uses?.startsWith('google/osv-scanner-action/') && step['continue-on-error'] === true), 'OSV must remain advisory');

  const main = graph[contract.orchestrators.main.path]?.jobs ?? {};
  const release = main[contract.orchestrators.main.aggregate];
  check(release?.['continue-on-error'] === undefined && ['', 'success()'].includes(expression(release?.if)), 'Release coordinator must retain success gating without failure suppression');

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
