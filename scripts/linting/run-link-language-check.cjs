#!/usr/bin/env node

const { spawnSync } = require('node:child_process');
const fs = require('node:fs');

function hasPwsh() {
  const command = process.platform === 'win32' ? 'where' : 'which';
  const check = spawnSync(command, ['pwsh'], { stdio: 'ignore' });
  return check.status === 0;
}

function runPwsh() {
  const result = spawnSync(
    'pwsh',
    ['-File', 'scripts/linting/Invoke-LinkLanguageCheck.ps1', ...process.argv.slice(2)],
    { stdio: 'inherit' }
  );

  if (result.error) {
    console.error(`Failed to execute pwsh: ${result.error.message}`);
    return 1;
  }

  return result.status ?? 1;
}

function getGitTrackedTextFiles() {
  const result = spawnSync('git', ['grep', '-I', '--name-only', '-e', ''], {
    encoding: 'utf8'
  });

  if (typeof result.status === 'number' && result.status > 1) {
    throw new Error((result.stderr || result.stdout || 'git grep failed').trim());
  }

  const output = result.stdout || '';
  return output
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
}

function findIssuesInFile(filePath, urlRegex) {
  let content;
  try {
    content = fs.readFileSync(filePath, 'utf8');
  } catch {
    return [];
  }

  const lines = content.split(/\r?\n/);
  const issues = [];

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    const matches = line.matchAll(urlRegex);

    for (const match of matches) {
      issues.push({
        file: filePath,
        line_number: index + 1,
        original_url: match[0]
      });
    }
  }

  return issues;
}

function runNodeFallback() {
  const urlRegex = /https?:\/\/[^\s<>"']+?en-us\/[^\s<>"']+/g;
  const files = getGitTrackedTextFiles();
  const allIssues = [];

  for (const file of files) {
    const stats = fs.existsSync(file) ? fs.statSync(file) : null;
    if (!stats || !stats.isFile()) {
      continue;
    }

    allIssues.push(...findIssuesInFile(file, urlRegex));
  }

  if (allIssues.length === 0) {
    console.log("No URLs with language paths found");
    return 0;
  }

  console.error(`Found ${allIssues.length} URLs with 'en-us' language paths`);
  for (const issue of allIssues) {
    console.error(`${issue.file}:${issue.line_number} ${issue.original_url}`);
  }

  return 1;
}

function main() {
  try {
    if (hasPwsh()) {
      process.exit(runPwsh());
    }

    console.log('pwsh not found, running Node.js fallback for link language-path check');
    process.exit(runNodeFallback());
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.error(`lint:links failed: ${message}`);
    process.exit(1);
  }
}

main();
