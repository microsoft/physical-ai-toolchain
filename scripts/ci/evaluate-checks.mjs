// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { loadContract } from './select-checks.mjs';

export function requiredLanes(contract, owner) {
  return contract.lanes.filter(lane => lane.owners[owner] && ['mandatory', 'discovery'].includes(lane.classification));
}

export function evaluateChecks(needs, contract = loadContract()) {
  const errors = [];
  if (!needs || typeof needs !== 'object' || Array.isArray(needs)) return ['Missing needs object'];
  const outputs = needs.changes?.outputs;
  if (!outputs || !['verified', 'verified-empty'].includes(outputs.selection_status)) {
    errors.push('Missing verified selection evidence');
  } else {
    const sha = /^[a-f0-9]{40}(?:[a-f0-9]{24})?$/i;
    if (!sha.test(outputs.base_sha ?? '') || !sha.test(outputs.head_sha ?? '')) errors.push('Missing comparison SHAs');
    if (!/^(0|[1-9]\d*)$/.test(outputs.file_count ?? '') ||
        (outputs.selection_status === 'verified-empty') !== (outputs.file_count === '0')) {
      errors.push('Invalid comparison file count');
    }
    for (const selector of Object.keys(contract.selectors)) {
      if (!['true', 'false'].includes(outputs[selector])) errors.push(`Invalid selection output: ${selector}`);
      if (outputs.selection_status === 'verified-empty' && outputs[selector] !== 'false') {
        errors.push(`Nonempty selection for verified-empty comparison: ${selector}`);
      }
    }
  }
  for (const lane of requiredLanes(contract, 'pr')) {
    const selected = lane.selector === 'always' || outputs?.[lane.selector] === 'true';
    const allowed = contract.outcomeSchemas[lane.outcomeSchema][selected ? 'selected' : 'unselected'];
    const job = lane.owners.pr;
    if (!allowed.includes(needs[job]?.result)) errors.push(`${job}: expected ${allowed.join('/')} but received ${needs[job]?.result ?? 'missing'}`);
  }
  return errors;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const errors = evaluateChecks(JSON.parse(process.env.NEEDS_JSON ?? 'null'));
    if (errors.length) throw new Error(errors.join('\n'));
    console.log('All mandatory checks match the verified CI selection.');
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
