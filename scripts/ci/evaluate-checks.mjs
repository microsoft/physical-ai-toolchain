// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

import { appendFileSync, lstatSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { isDeepStrictEqual } from 'node:util';
import { fileURLToPath } from 'node:url';
import { aggregateOutcomes, inspectReport, outcomeOutputs } from './publish-outcome.mjs';
import { loadContract } from './select-checks.mjs';

export function requiredLanes(contract, owner) {
  return contract.lanes.filter(lane => lane.owners[owner] && ['mandatory', 'discovery'].includes(lane.classification));
}

export function visibleLanes(contract, owner) {
  return contract.lanes.filter(lane => lane.owners[owner] && ['mandatory', 'discovery', 'advisory'].includes(lane.classification));
}

const sha = /^[a-f0-9]{40}(?:[a-f0-9]{24})?$/i;
const count = value => typeof value === 'string' && /^(0|[1-9]\d*)$/.test(value) && Number.isSafeInteger(Number(value));
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const safeText = value => String(value ?? '').replace(/[\u0000-\u001f\u007f]/g, ' ').slice(0, 300);
const markdown = value => safeText(value).replace(/[<>&|`[\]\\]/g, character => `&#${character.codePointAt(0)};`);
const knownTools = new Set(['node', 'git', 'npm', 'uv', 'python', 'pwsh', 'go', 'terraform', 'tflint', 'actionlint', 'shellcheck', 'ruff', 'trivy', 'docker']);
const requireEvidence = (condition, message) => { if (!condition) throw new Error(message); };

function downloadedReceipts(directory) {
  if (!directory) return [];
  try {
    return readdirSync(directory, { withFileTypes: true }).map(entry => {
      const artifact = { name: entry.name, directory: resolve(directory, entry.name) };
      try {
        requireEvidence(entry.isDirectory() && !entry.isSymbolicLink(), 'Invalid artifact directory');
        const path = resolve(artifact.directory, 'receipt.json');
        requireEvidence(lstatSync(path).isFile() && !lstatSync(path).isSymbolicLink(), 'Invalid receipt file');
        artifact.receipt = JSON.parse(readFileSync(path, 'utf8'));
      } catch {
        artifact.invalid = true;
      }
      return artifact;
    });
  } catch {
    return [];
  }
}

function receiptTools(receipt) {
  return {
    workflow: receipt.workflow, job: receipt.job, shard: receipt.shard,
    versions: Object.fromEntries(Object.entries(object(receipt.tools) ? receipt.tools : {})
      .filter(([tool]) => knownTools.has(tool)).map(([tool, version]) => [tool, safeText(version).slice(0, 160)])),
  };
}

function verifyWorkflowReceipt(lane, needs, contract, owner, context, artifacts) {
  const workflow = lane.target.replace(/\.ya?ml$/, '');
  const spec = contract.execution?.[workflow];
  requireEvidence(object(spec?.jobs), 'Missing canonical execution metadata');
  const records = artifacts.filter(artifact => artifact.receipt?.workflow === workflow || artifact.name.startsWith(`ci-outcome-${workflow}-`));
  requireEvidence(records.every(artifact => !artifact.invalid), 'Malformed execution receipt artifact');
  const roots = records.filter(artifact => artifact.receipt?.job === spec['output-job']);
  requireEvidence(roots.length === 1, roots.length ? 'Duplicate output receipts' : 'Missing current-attempt output receipt');
  const root = roots[0].receipt;
  const shard = spec.aggregate?.shard ?? spec.jobs[spec['output-job']]?.shards?.[0] ?? 'default';
  requireEvidence(root.version === 1 && root.mode === (spec.aggregate ? 'aggregate' : 'record'), 'Invalid output receipt schema');
  requireEvidence(root.workflow === workflow && root.job === spec['output-job'] && root.shard === shard && root.target === shard &&
    root['run-id'] === context.runId && root['run-attempt'] === context.runAttempt && root.sha === context.head,
  'Stale or mismatched output receipt identity');
  requireEvidence(root['artifact-name'] === roots[0].name &&
    roots[0].name === `ci-outcome-${workflow}-${root.job}-${shard}-${context.runId}-${context.runAttempt}`,
  'Mismatched output artifact identity');
  requireEvidence(records.every(artifact => artifact.receipt?.['artifact-name'] === artifact.name), 'Mismatched downloaded artifact identity');
  requireEvidence(records.every(artifact => artifact.receipt?.['first-failure'] === ''), 'Receipt failure identity contradicts successful execution');
  requireEvidence(root.selected === true && root['work-status'] === 'success' &&
    Array.isArray(root.errors) && root.errors.length === 0 && root['first-failure'] === '', 'Output receipt did not successfully execute');
  const children = spec.aggregate ? records.filter(artifact => artifact !== roots[0]) : records;
  const expectedShards = {};
  const expectedTargets = {};
  const innerNeeds = {};
  for (const [job, metadata] of Object.entries(spec.jobs)) {
    let selected = true;
    if (metadata.if && !metadata.discovery) {
      const input = metadata.if.match(/^inputs\.(\w+)$/)?.[1];
      requireEvidence(input && contract.smokeInputs[input], 'Unverified child selection');
      selected = owner === 'main' || contract.smokeInputs[input].some(selector => needs.changes?.outputs?.[selector] === 'true');
    }
    if (metadata.discovery) {
      const discovery = metadata.discovery;
      const sources = children.filter(artifact => artifact.receipt?.job === discovery.job);
      requireEvidence(sources.length === 1, 'Missing or duplicate discovery receipt');
      const report = spec.jobs[discovery.job]?.reports?.find(item => item.kind === 'json');
      requireEvidence(report, 'Missing canonical discovery report');
      const path = report.path.replaceAll('{shard}', sources[0].receipt.shard);
      let manifest;
      try {
        inspectReport(resolve(sources[0].directory, 'reports'), { ...report, path });
        manifest = JSON.parse(readFileSync(resolve(sources[0].directory, 'reports', path), 'utf8'));
      }
      catch { throw new Error('Invalid discovery manifest'); }
      requireEvidence(Array.isArray(manifest.results) && manifest.results.length > 0 &&
        manifest.results.every(result => typeof result.shard === 'string' && typeof result.image === 'string'),
      'Invalid discovered target inventory');
      expectedShards[job] = manifest.results.map(result => result.shard);
      expectedTargets[job] = Object.fromEntries(manifest.results.map(result => [result.shard, result.image]));
      innerNeeds[discovery.job] = {
        result: 'success', outputs: {
          [discovery['shards-output']]: JSON.stringify(expectedShards[job]),
          [discovery['targets-output']]: JSON.stringify(expectedTargets[job]),
        },
      };
    } else {
      requireEvidence(Array.isArray(metadata.shards) && metadata.shards.length > 0, 'Missing canonical shard inventory');
      expectedShards[job] = selected ? metadata.shards : [];
    }
    innerNeeds[job] ??= { result: selected ? 'success' : 'skipped' };
  }
  const checked = aggregateOutcomes({
    workflow, job: spec['output-job'], shard, target: shard,
    runId: context.runId, runAttempt: context.runAttempt, sha: context.head, selected: true,
    selectionReason: 'Verify current-attempt gate evidence', expectedShards, expectedTargets, needs: innerNeeds,
    tools: [], artifactRoots: new Map(children.map(artifact => [artifact.receipt, artifact.directory])),
  }, contract, children.map(artifact => artifact.receipt));
  requireEvidence(checked['work-status'] === 'success', 'Invalid or incomplete current-attempt operations, reports, or child receipts');
  requireEvidence(isDeepStrictEqual(root.counts, checked.counts), 'Output receipt counts contradict validated execution');
  if (spec.aggregate) {
    const ordered = values => [...values].sort((a, b) => `${a.job}/${a.shard}`.localeCompare(`${b.job}/${b.shard}`));
    requireEvidence(Array.isArray(root.operations) && root.operations.length === 0 &&
      Array.isArray(root.reports) && root.reports.length === 0 && Array.isArray(root.children) &&
      isDeepStrictEqual(ordered(root.children), ordered(checked.children)), 'Aggregate receipt contradicts validated children');
  }
  const exposed = needs[lane.owners[owner]]?.outputs ?? {};
  for (const [field, value] of Object.entries(outcomeOutputs(checked))) {
    if (field === 'artifact-name') continue;
    requireEvidence((exposed[field] ?? '') === value, 'Exposed outputs contradict current-attempt receipt');
  }
  requireEvidence(artifactUrl(exposed['artifact-url']).includes(`/actions/runs/${context.runId}/artifacts/`),
    'Exposed artifact belongs to another run');
  return records.map(artifact => receiptTools(artifact.receipt));
}

function verifyDownloadedReceipts(needs, contract, owner, context) {
  const errors = [];
  const warnings = [];
  const tools = [];
  const artifacts = downloadedReceipts(context.receiptsDirectory);
  const validContext = [context.runId, context.runAttempt].every(value => typeof value === 'string' && /^[1-9]\d*$/.test(value)) &&
    sha.test(context.head ?? '');
  for (const lane of visibleLanes(contract, owner).filter(lane => lane.target)) {
    const selected = owner === 'main' || lane.selector === 'always' || needs?.changes?.outputs?.[lane.selector] === 'true';
    if (!selected) continue;
    try {
      requireEvidence(validContext, 'Missing current run, attempt, or commit identity');
      tools.push(...verifyWorkflowReceipt(lane, needs, contract, owner, context, artifacts));
    } catch (error) {
      (lane.classification === 'advisory' ? warnings : errors).push(`${lane.owners[owner]}: ${safeText(error.message)}`);
    }
  }
  return { errors, warnings, tools };
}

export function readReceiptTools(directory, context) {
  return readdirSync(directory, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name)).map(entry => {
    if (!entry.isDirectory() || entry.isSymbolicLink()) throw new Error('Invalid summary artifact directory');
    const path = resolve(directory, entry.name, 'receipt.json');
    if (lstatSync(path).isSymbolicLink()) throw new Error('Invalid summary receipt path');
    const receipt = JSON.parse(readFileSync(path, 'utf8'));
    if (receipt['run-id'] !== context.runId || receipt['run-attempt'] !== context.runAttempt || receipt.sha !== context.head) {
      throw new Error('Stale summary receipt identity');
    }
    if (!['workflow', 'job', 'shard'].every(key => /^[a-z0-9][a-z0-9_.-]*$/i.test(receipt[key] ?? ''))) {
      throw new Error('Invalid summary producer identity');
    }
    const versions = Object.fromEntries(Object.entries(object(receipt.tools) ? receipt.tools : {})
      .filter(([tool]) => knownTools.has(tool)).map(([tool, version]) => [tool, safeText(version).slice(0, 160)]));
    return { workflow: receipt.workflow, job: receipt.job, shard: receipt.shard, versions };
  });
}

function selectionReasons(outputs) {
  try {
    const value = JSON.parse(outputs?.selection_reasons);
    return object(value) ? value : {};
  } catch {
    return {};
  }
}

function artifactUrl(value) {
  return typeof value === 'string' && /^https:\/\/github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+\/actions\/runs\/\d+\/artifacts\/\d+$/.test(value)
    ? value : '';
}

function requiresTests(lane, contract) {
  const workflow = lane.target?.replace(/\.ya?ml$/, '');
  return Object.values(contract.execution?.[workflow]?.jobs ?? {}).some(job =>
    job.reports?.some(report => ['junit', 'nunit', 'jest'].includes(report.kind)));
}

export function evaluateChecks(needs, contract = loadContract(), owner = 'pr') {
  const errors = [];
  if (!['pr', 'main'].includes(owner)) return ['Unknown validation owner'];
  if (!object(needs)) return ['Missing needs object'];
  const outputs = needs.changes?.outputs;
  if (owner === 'pr' && (!outputs || !['verified', 'verified-empty'].includes(outputs.selection_status))) {
    errors.push('Missing verified selection evidence');
  } else if (owner === 'pr') {
    if (!sha.test(outputs.base_sha ?? '') || !sha.test(outputs.head_sha ?? '')) errors.push('Missing comparison SHAs');
    if (!count(outputs.file_count) ||
        (outputs.selection_status === 'verified-empty') !== (outputs.file_count === '0')) {
      errors.push('Invalid comparison file count');
    }
    const reasons = selectionReasons(outputs);
    for (const selector of Object.keys(contract.selectors)) {
      if (!['true', 'false'].includes(outputs[selector])) errors.push(`Invalid selection output: ${selector}`);
      if (outputs.selection_status === 'verified-empty' && outputs[selector] !== 'false') {
        errors.push(`Nonempty selection for verified-empty comparison: ${selector}`);
      }
      const allowedReasons = outputs[selector] === 'true' ? ['shared-ci-change', 'path-or-dependency-match'] : ['not-selected'];
      if (!allowedReasons.includes(reasons[selector])) errors.push(`Invalid selection reason: ${selector}`);
    }
  }
  for (const lane of requiredLanes(contract, owner)) {
    const selected = owner === 'main' || lane.selector === 'always' || outputs?.[lane.selector] === 'true';
    const allowed = contract.outcomeSchemas[lane.outcomeSchema][selected ? 'selected' : 'unselected'];
    const job = lane.owners[owner];
    const result = needs[job]?.result;
    if (!allowed.includes(result)) errors.push(`${job}: expected ${allowed.join('/')} but received ${result ?? 'missing'}`);
    if (lane.classification === 'discovery' || (!selected && result === 'skipped')) continue;
    const evidence = needs[job]?.outputs ?? {};
    const expectedStatus = selected ? 'success' : 'planned-skip';
    if (evidence['work-status'] !== expectedStatus) errors.push(`${job}: expected work-status ${expectedStatus} but received ${evidence['work-status'] ?? 'missing'}`);
    for (const field of ['expected-count', 'executed-count', 'test-count', 'skipped-count']) {
      if (!count(evidence[field])) errors.push(`${job}: invalid or missing ${field}`);
    }
    const expected = Number(evidence['expected-count']);
    const executed = Number(evidence['executed-count']);
    if (selected ? !(expected > 0 && executed === expected) : expected !== 0 || executed !== 0) {
      errors.push(`${job}: incomplete execution counts`);
    }
    if (selected && requiresTests(lane, contract) && !(Number(evidence['test-count']) > 0)) errors.push(`${job}: no executed testcases`);
    if (!selected && Number(evidence['test-count']) !== 0) errors.push(`${job}: planned skip has executed testcases`);
    if (!/^[1-9]\d*$/.test(evidence['artifact-id'] ?? '') || !artifactUrl(evidence['artifact-url']) ||
        !evidence['artifact-url']?.endsWith(`/artifacts/${evidence['artifact-id']}`)) {
      errors.push(`${job}: missing or invalid artifact identity`);
    }
    if (evidence['first-failure']) errors.push(`${job}: first-failure contradicts successful evidence`);
  }
  return errors;
}

export function buildSummary(needs, contract = loadContract(), owner = 'pr', context = {}) {
  const errors = evaluateChecks(needs, contract, owner);
  const evidence = verifyDownloadedReceipts(needs ?? {}, contract, owner, context);
  errors.push(...evidence.errors);
  if (context.cancelled === true) errors.push('Workflow run was cancelled or superseded');
  else if (context.cancelled !== undefined && typeof context.cancelled !== 'boolean') errors.push('Invalid workflow cancellation context');
  const selection = needs?.changes?.outputs;
  const reasons = selectionReasons(selection);
  const rows = visibleLanes(contract, owner).map(lane => {
    const job = needs?.[lane.owners[owner]];
    const outputs = job?.outputs ?? {};
    const selected = owner === 'main' || lane.selector === 'always' || selection?.[lane.selector] === 'true';
    const jobErrors = errors.filter(error => error.startsWith(`${lane.owners[owner]}:`));
    const plannedSkip = !selected && job?.result === 'skipped' &&
      ['verified', 'verified-empty'].includes(selection?.selection_status) && selection?.[lane.selector] === 'false';
    const cancelled = job?.result === 'cancelled' || outputs['work-status'] === 'cancelled' ||
      context.cancelled === true && !plannedSkip && !['success', 'failure'].includes(job?.result);
    const status = cancelled ? 'cancelled'
      : jobErrors.length ? 'failure'
      : plannedSkip ? 'planned-skip'
      : outputs['work-status'] ?? job?.result ?? 'missing';
    return {
      job: lane.owners[owner], classification: lane.classification, selected,
      reason: owner === 'main' ? 'full-run' : lane.selector === 'always' ? 'unconditional' : safeText(reasons[lane.selector] ?? 'missing'),
      result: safeText(job?.result ?? 'missing'), status: safeText(status),
      expected: count(outputs['expected-count']) ? Number(outputs['expected-count']) : null,
      executed: count(outputs['executed-count']) ? Number(outputs['executed-count']) : null,
      tests: count(outputs['test-count']) ? Number(outputs['test-count']) : null,
      skipped: count(outputs['skipped-count']) ? Number(outputs['skipped-count']) : null,
      firstFailure: safeText(outputs['first-failure'] || jobErrors[0] || ''),
      artifact: artifactUrl(outputs['artifact-url']),
    };
  });
  const report = {
    owner, status: errors.length ? context.cancelled === true || rows.some(row => row.classification !== 'advisory' && row.status === 'cancelled') ? 'cancelled' : 'failure' : 'success',
    comparison: {
      base: sha.test(selection?.base_sha ?? context.base ?? '') ? selection?.base_sha ?? context.base : '',
      head: sha.test(selection?.head_sha ?? context.head ?? '') ? selection?.head_sha ?? context.head : '',
      status: owner === 'main' ? 'full' : safeText(selection?.selection_status ?? 'missing'),
      files: count(selection?.file_count) ? Number(selection.file_count) : null,
    },
    tools: { node: process.version, producers: evidence.tools }, warnings: evidence.warnings, errors, rows,
  };
  const lines = [`## ${owner === 'main' ? 'Main' : 'PR'} validation: ${report.status}`, '',
    `Comparison: \`${report.comparison.base || 'unavailable'}\` → \`${report.comparison.head || 'unavailable'}\` (${report.comparison.status})`,
    `Summary tool: Node ${process.version}. Raw reports are retained in each evidence artifact.`, ''];
  for (const [title, advisory] of [['Required checks', false], ['Advisory checks', true]]) {
    lines.push(`### ${title}`, '', '| Check | Status | Selection reason | Operations | Tests / skipped | First failure | Evidence |',
      '| --- | --- | --- | --- | --- | --- | --- |');
    for (const row of rows.filter(row => (row.classification === 'advisory') === advisory)) {
      lines.push(`| ${markdown(row.job)} | ${markdown(row.status)} | ${markdown(row.reason)} | ${row.executed ?? '—'} / ${row.expected ?? '—'} | ${row.tests ?? '—'} / ${row.skipped ?? '—'} | ${markdown(row.firstFailure)} | ${row.artifact ? `[artifact](${row.artifact})` : '—'} |`);
    }
    if (report.tools.producers.length) {
      lines.push('### Producer tool versions', '', '| Workflow / job / shard | Tools |', '| --- | --- |');
      for (const producer of report.tools.producers) {
        lines.push(`| ${markdown(`${producer.workflow} / ${producer.job} / ${producer.shard}`)} | ${markdown(Object.entries(producer.versions).map(([tool, version]) => `${tool}: ${version}`).join('; '))} |`);
      }
      lines.push('');
    }
    lines.push('');
  }
  if (errors.length) lines.push('### Gate errors', '', ...errors.map(error => `- ${markdown(error)}`), '');
  if (report.warnings.length) lines.push('### Reporting warnings', '', ...report.warnings.map(warning => `- ${markdown(warning)}`), '');
  return { report, markdown: lines.join('\n') };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const context = {
      base: process.env.BASE_SHA, head: process.env.HEAD_SHA,
      runId: process.env.GITHUB_RUN_ID, runAttempt: process.env.GITHUB_RUN_ATTEMPT,
      receiptsDirectory: process.env.CI_RECEIPTS_DIRECTORY,
      cancelled: ['true', 'false'].includes(process.env.CI_CANCELLED) ? process.env.CI_CANCELLED === 'true' : process.env.CI_CANCELLED,
    };
    const { report, markdown } = buildSummary(JSON.parse(process.env.NEEDS_JSON ?? 'null'), loadContract(), process.env.CI_OWNER ?? 'pr', {
      ...context,
    });
    const output = resolve(process.env.CI_SUMMARY_PATH ?? 'logs/ci-validation-summary.json');
    mkdirSync(dirname(output), { recursive: true });
    writeFileSync(output, `${JSON.stringify(report, null, 2)}\n`);
    writeFileSync(output.replace(/\.json$/, '') + '.md', markdown);
    if (process.env.GITHUB_STEP_SUMMARY) appendFileSync(process.env.GITHUB_STEP_SUMMARY, markdown);
    if (report.errors.length) throw new Error(report.errors.join('\n'));
    console.log('All mandatory checks match verified selection and execution evidence.');
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
