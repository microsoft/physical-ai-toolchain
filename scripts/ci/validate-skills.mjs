// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

import { existsSync, lstatSync, readdirSync, readFileSync } from 'node:fs';
import { dirname, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const allowedFrontmatterFields = new Set([
  'name',
  'description',
  'license',
  'compatibility',
  'metadata',
  'allowed-tools',
]);
const namePattern = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

function scalarValue(value) {
  const trimmed = value.trim();
  if (trimmed.startsWith('"') && trimmed.endsWith('"')) {
    try {
      return JSON.parse(trimmed);
    } catch {
      return trimmed.slice(1, -1);
    }
  }
  if (trimmed.startsWith("'") && trimmed.endsWith("'")) {
    return trimmed.slice(1, -1).replaceAll("''", "'");
  }
  return trimmed;
}

function frontmatter(markdown, skillPath, errors) {
  const match = markdown.match(/^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/);
  if (!match) {
    errors.push(`${skillPath}: missing YAML frontmatter`);
    return null;
  }
  const data = {};
  for (const [index, line] of match[1].split(/\r?\n/).entries()) {
    if (!line || /^\s/.test(line) || line.trimStart().startsWith('#')) {
      continue;
    }
    const property = line.match(/^([A-Za-z][A-Za-z0-9_-]*):(.*)$/);
    if (!property) {
      errors.push(`${skillPath}:${index + 2}: invalid top-level frontmatter entry`);
      continue;
    }
    const [, key, rawValue] = property;
    if (Object.hasOwn(data, key)) {
      errors.push(`${skillPath}:${index + 2}: duplicate frontmatter field '${key}'`);
      continue;
    }
    data[key] = scalarValue(rawValue);
  }
  return data;
}

function validateLocalLinks(markdown, skillFile, packageRoot, errors) {
  for (const match of markdown.matchAll(/\[[^\]]*]\(([^)]+)\)/g)) {
    const rawTarget = match[1].trim().replace(/^<|>$/g, '');
    const target = rawTarget.split('#', 1)[0];
    if (!target || /^[a-z][a-z0-9+.-]*:/i.test(target) || target.startsWith('#') ||
        target.includes('<') || target.includes('$')) {
      continue;
    }
    const resolved = resolve(dirname(skillFile), decodeURIComponent(target));
    const relativeTarget = relative(packageRoot, resolved);
    if (relativeTarget.startsWith(`..${sep}`) || relativeTarget === '..') {
      continue;
    } else if (!existsSync(resolved)) {
      errors.push(`${skillFile}: local link does not exist: ${rawTarget}`);
    }
  }
}

function validateNoSymlinks(path, packageRoot, errors) {
  for (const entry of readdirSync(path, { withFileTypes: true })) {
    const entryPath = join(path, entry.name);
    if (lstatSync(entryPath).isSymbolicLink()) {
      errors.push(`${relative(packageRoot, entryPath)}: symlinks are not permitted in skill packages`);
    } else if (entry.isDirectory()) {
      validateNoSymlinks(entryPath, packageRoot, errors);
    }
  }
}

export function validateSkillPackages(repositoryRoot) {
  const skillsRoot = join(repositoryRoot, '.github', 'skills');
  const errors = [];
  if (!existsSync(skillsRoot)) {
    return [`${skillsRoot}: skills directory does not exist`];
  }

  for (const entry of readdirSync(skillsRoot, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
    const packageRoot = join(skillsRoot, entry.name);
    if (!entry.isDirectory()) {
      errors.push(`${packageRoot}: skill packages must be directories`);
      continue;
    }
    const skillFile = join(packageRoot, 'SKILL.md');
    if (!existsSync(skillFile)) {
      errors.push(`${packageRoot}: missing SKILL.md`);
      continue;
    }
    validateNoSymlinks(packageRoot, packageRoot, errors);
    const markdown = readFileSync(skillFile, 'utf8');
    const data = frontmatter(markdown, skillFile, errors);
    if (!data) {
      continue;
    }
    for (const key of Object.keys(data)) {
      if (!allowedFrontmatterFields.has(key)) {
        errors.push(`${skillFile}: unsupported frontmatter field '${key}'`);
      }
    }
    if (data.name !== entry.name) {
      errors.push(`${skillFile}: name '${data.name ?? ''}' must match directory '${entry.name}'`);
    }
    if (!namePattern.test(entry.name)) {
      errors.push(`${skillFile}: directory name must use lowercase kebab-case`);
    }
    if (typeof data.description !== 'string' || data.description.trim().length === 0) {
      errors.push(`${skillFile}: description must be a non-empty string`);
    }
    if ('compatibility' in data &&
        (typeof data.compatibility !== 'string' || data.compatibility.trim().length === 0)) {
      errors.push(`${skillFile}: compatibility must be a non-empty string`);
    }
    validateLocalLinks(markdown, skillFile, packageRoot, errors);
  }
  return errors;
}

const isMain = process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMain) {
  const repositoryRoot = resolve(process.argv[2] ?? fileURLToPath(new URL('../../', import.meta.url)));
  const errors = validateSkillPackages(repositoryRoot);
  if (errors.length > 0) {
    console.error(errors.join('\n'));
    process.exitCode = 1;
  } else {
    console.log('Skill package validation passed.');
  }
}
