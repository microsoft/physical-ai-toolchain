// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

import { spawnSync } from 'node:child_process';
import { appendFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const exactObjectId = /^[a-f0-9]{40}(?:[a-f0-9]{24})?$/;

export const fullValidation = Object.freeze({ mode: 'full', base_sha: '', head_sha: '' });

function defaultGit(args, cwd) {
  const result = spawnSync('git', args, { cwd, encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 });
  if (result.error) throw result.error;
  if (result.status === null) throw new Error(`Git terminated without an exit status: ${result.signal ?? 'unknown signal'}`);
  return result;
}

function gitOutput(git, args, cwd) {
  const result = git(args, cwd);
  return result.status === 0 ? result.stdout.trim() : null;
}

function resolveCommit(candidate, cwd, git) {
  if (typeof candidate !== 'string' || !exactObjectId.test(candidate)) return null;
  const commit = gitOutput(git, ['rev-parse', '--verify', '--end-of-options', `${candidate}^{commit}`], cwd);
  return commit && exactObjectId.test(commit) ? commit : null;
}

function resolvePullRequestBase(head, pullRequestHead, cwd, git) {
  const resolvedPullRequestHead = resolveCommit(pullRequestHead, cwd, git);
  if (!resolvedPullRequestHead) return null;
  const parentLine = gitOutput(git, ['rev-list', '--parents', '-n', '1', '--end-of-options', head], cwd);
  if (!parentLine) return null;
  const [, ...parents] = parentLine.split(/\s+/);
  return parents.length === 2 && parents[1] === resolvedPullRequestHead ? parents[0] : null;
}

function isAncestor(base, head, cwd, git) {
  return git(['merge-base', '--is-ancestor', base, head], cwd).status === 0;
}

function isDiffable(base, head, cwd, git) {
  return git(['diff', '--name-only', `${base}..${head}`, '--'], cwd).status === 0;
}

export function resolveWorkflowChangeRange({
  eventName,
  baseSha = '',
  headSha,
  pullRequestHeadSha = '',
  cwd = process.cwd(),
  git = defaultGit,
}) {
  const resolvedHead = resolveCommit(headSha, cwd, git);
  const checkedOutHead = resolveCommit(gitOutput(git, ['rev-parse', 'HEAD'], cwd) ?? '', cwd, git);
  if (!resolvedHead || !checkedOutHead || checkedOutHead !== resolvedHead) return { ...fullValidation };

  let resolvedBase = null;
  if (eventName === 'pull_request') {
    resolvedBase = resolvePullRequestBase(resolvedHead, pullRequestHeadSha, cwd, git);
  } else if (eventName === 'merge_group') {
    resolvedBase = resolveCommit(baseSha, cwd, git);
  }

  if (!resolvedBase || resolvedBase === resolvedHead
      || !isAncestor(resolvedBase, resolvedHead, cwd, git)
      || !isDiffable(resolvedBase, resolvedHead, cwd, git)) {
    return { ...fullValidation };
  }
  return { mode: 'range', base_sha: resolvedBase, head_sha: resolvedHead };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const result = resolveWorkflowChangeRange({
      eventName: process.env.EVENT_NAME,
      baseSha: process.env.BASE_SHA,
      headSha: process.env.HEAD_SHA,
      pullRequestHeadSha: process.env.PULL_REQUEST_HEAD_SHA,
    });
    if (process.env.GITHUB_OUTPUT) {
      appendFileSync(process.env.GITHUB_OUTPUT, Object.entries(result).map(([key, value]) => `${key}=${value}\n`).join(''));
    }
    console.log(JSON.stringify(result, null, 2));
  } catch (error) {
    console.error(`Workflow change-range resolution failed: ${error.message}`);
    process.exitCode = 1;
  }
}
