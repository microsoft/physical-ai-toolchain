// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

import { spawnSync } from 'node:child_process';
import { appendFileSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

export const contractPath = fileURLToPath(new URL('./ci-contract.json', import.meta.url));

export function loadContract(path = contractPath) {
  const contract = JSON.parse(readFileSync(path, 'utf8'));
  if (contract.version !== 1 || !contract.selectors || !Array.isArray(contract.lanes)) {
    throw new Error('Unsupported or malformed CI contract');
  }
  return contract;
}

export function parseChangedPaths(output) {
  if (output === '') return [];
  const fields = output.split('\0');
  if (fields.pop() !== '') throw new Error('Truncated Git change stream');
  const paths = new Set();
  for (let i = 0; i < fields.length;) {
    const status = fields[i++];
    const paired = /^[RC]\d{1,3}$/.test(status);
    if ((!['A', 'D', 'M', 'T', 'U', 'X', 'B'].includes(status) && !paired) || (paired && Number(status.slice(1)) > 100)) {
      throw new Error(`Unsupported Git status: ${status}`);
    }
    const count = paired ? 2 : 1;
    for (let side = 0; side < count; side++) {
      const path = fields[i++];
      if (!path) throw new Error('Missing path in Git change stream');
      paths.add(path);
    }
  }
  return [...paths];
}

export function comparePaths(base, head, cwd = process.cwd()) {
  if (![base, head].every(ref => typeof ref === 'string' && /^[a-f0-9]{40}(?:[a-f0-9]{24})?$/i.test(ref))) {
    throw new Error('Comparison requires exact base and tested commit SHAs');
  }
  const result = spawnSync('git', ['diff', '--name-status', '-z', '--find-renames', base, head, '--'], {
    cwd, encoding: 'utf8', maxBuffer: 64 * 1024 * 1024,
  });
  if (result.error || result.status !== 0) {
    throw new Error(`Git comparison failed: ${result.error?.message ?? result.stderr.trim()}`);
  }
  return parseChangedPaths(result.stdout);
}

export function selectChecks(paths, contract = loadContract(), full = false) {
  const shared = contract.sharedPatterns.map(pattern => new RegExp(pattern));
  const sharedChange = paths.some(path => shared.some(pattern => pattern.test(path)));
  return Object.fromEntries(Object.entries(contract.selectors).map(([name, selector]) => {
    const patterns = [...selector.patterns, ...selector.groups.flatMap(group => {
      if (!contract.dependencyGroups[group]) throw new Error(`Unknown dependency group: ${group}`);
      return contract.dependencyGroups[group];
    })].map(pattern => new RegExp(pattern));
    return [name, full || sharedChange || paths.some(path => patterns.some(pattern => pattern.test(path)))];
  }));
}

export function selectionOutputs({ base, head, full = false, cwd, contract = loadContract() }) {
  const paths = full ? [] : comparePaths(base, head, cwd);
  return {
    ...Object.fromEntries(Object.entries(selectChecks(paths, contract, full)).map(([key, value]) => [key, String(value)])),
    selection_status: full ? 'full' : paths.length ? 'verified' : 'verified-empty',
    base_sha: full ? '' : base,
    head_sha: full ? '' : head,
    file_count: String(paths.length),
  };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const outputs = selectionOutputs({ base: process.env.BASE_SHA, head: process.env.HEAD_SHA, full: process.argv.includes('--full') });
    if (process.env.GITHUB_OUTPUT) {
      appendFileSync(process.env.GITHUB_OUTPUT, Object.entries(outputs).map(([key, value]) => `${key}=${value}\n`).join(''));
    }
    console.log(JSON.stringify(outputs, null, 2));
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
