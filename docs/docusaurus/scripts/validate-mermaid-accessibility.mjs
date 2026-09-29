// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import contentDiscovery from './content-discovery.cjs';

const { sourceDocumentIdentity } = contentDiscovery;

const scriptPath = fileURLToPath(import.meta.url);
const siteDirectory = path.resolve(path.dirname(scriptPath), '..');

function stripContainerPrefix(line) {
  return line.replace(/^\s*(?:>\s*)+/, '');
}

export function extractMermaidDiagrams(source, sourcePath = '<source>') {
  const lines = source.replaceAll('\r\n', '\n').split('\n');
  const diagrams = [];
  for (let index = 0; index < lines.length; index += 1) {
    const line = stripContainerPrefix(lines[index]);
    const opening = line.match(/^\s*(`{3,}|~{3,})mermaid(?:\s+.*)?\s*$/i);
    if (!opening) {
      continue;
    }
    const marker = opening[1][0];
    const minimumLength = opening[1].length;
    const body = [];
    let closed = false;
    for (index += 1; index < lines.length; index += 1) {
      const candidate = stripContainerPrefix(lines[index]);
      const closing = candidate.match(/^\s*(`{3,}|~{3,})\s*$/);
      if (closing && closing[1][0] === marker && closing[1].length >= minimumLength) {
        closed = true;
        break;
      }
      body.push(candidate);
    }
    if (!closed) {
      throw new Error(`${sourcePath}: Mermaid fence is not terminated`);
    }
    diagrams.push(body.join('\n'));
  }
  return diagrams;
}

export function parseMermaidAccessibilityMetadata(diagramSource, sourcePath = '<source>', ordinal = 1) {
  const activeLines = diagramSource
    .split('\n')
    .filter((line) => !line.trimStart().startsWith('%%'));
  const titleMatches = activeLines
    .map((line) => line.match(/^\s*accTitle\s*:\s*(.*)$/i))
    .filter(Boolean);
  const descriptionMatches = activeLines
    .map((line) => line.match(/^\s*accDescr(?:iption)?\s*:\s*(.*)$/i))
    .filter(Boolean);
  const prefix = `${sourcePath}: Mermaid diagram ${ordinal}`;
  const failures = [];

  if (titleMatches.length !== 1) {
    failures.push(`${prefix} requires exactly one active accTitle; found ${titleMatches.length}`);
  } else if (!titleMatches[0][1].trim()) {
    failures.push(`${prefix} has an empty accTitle`);
  }
  if (descriptionMatches.length !== 1) {
    failures.push(`${prefix} requires exactly one active accDescr; found ${descriptionMatches.length}`);
  } else if (!descriptionMatches[0][1].trim()) {
    failures.push(`${prefix} has an empty accDescr`);
  }

  return {
    description: descriptionMatches.length === 1 ? descriptionMatches[0][1].trim() : '',
    failures,
    title: titleMatches.length === 1 ? titleMatches[0][1].trim() : '',
  };
}

export function sourcePathToRoute(sourcePath, source, routeBasePath = '/') {
  return sourceDocumentIdentity(sourcePath, source, routeBasePath).route;
}

export function collectMermaidInventory({ docsDirectory, exclude = [], routeBasePath = '/' }) {
  const records = [];
  const failures = [];

  function visit(directory) {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const absolutePath = path.join(directory, entry.name);
      const relativePath = path.relative(docsDirectory, absolutePath).replaceAll('\\', '/');
      const candidatePath = entry.isDirectory() ? `${relativePath}/inventory.md` : relativePath;
      const excluded = exclude.some((pattern) => path.matchesGlob(candidatePath, pattern));
      if (excluded || entry.name.startsWith('.')) {
        continue;
      }
      if (entry.isDirectory()) {
        visit(absolutePath);
        continue;
      }
      if (!/\.mdx?$/i.test(entry.name)) {
        continue;
      }

      const source = fs.readFileSync(absolutePath, 'utf8');
      let diagrams;
      try {
        diagrams = extractMermaidDiagrams(source, relativePath);
      } catch (error) {
        failures.push(error instanceof Error ? error.message : String(error));
        continue;
      }
      diagrams.forEach((diagram, index) => {
        const metadata = parseMermaidAccessibilityMetadata(diagram, relativePath, index + 1);
        failures.push(...metadata.failures);
        records.push({
          description: metadata.description,
          ordinal: index + 1,
          route: sourcePathToRoute(relativePath, source, routeBasePath),
          sourcePath: relativePath,
          title: metadata.title,
        });
      });
    }
  }

  visit(docsDirectory);
  records.sort((left, right) =>
    left.sourcePath.localeCompare(right.sourcePath) || left.ordinal - right.ordinal);
  if (records.length === 0) {
    failures.push('No deployed Mermaid diagrams were found');
  }
  return { failures, records };
}

async function loadDocsConfiguration() {
  const { loadSiteConfig } = await import('@docusaurus/core/lib/server/config.js');
  const { siteConfig: config } = await loadSiteConfig({ siteDir: siteDirectory });
  const classic = config.presets.find((preset) => Array.isArray(preset) && preset[0] === 'classic');
  const docs = classic?.[1]?.docs;
  if (!docs || typeof docs.path !== 'string') {
    throw new Error('Docusaurus classic docs configuration is required');
  }
  return {
    baseUrl: config.baseUrl,
    docsDirectory: path.resolve(siteDirectory, docs.path),
    exclude: Array.isArray(docs.exclude) ? docs.exclude : [],
    routeBasePath: docs.routeBasePath ?? '/docs/',
  };
}

async function main() {
  const configuration = await loadDocsConfiguration();
  const inventory = collectMermaidInventory(configuration);
  if (inventory.failures.length > 0) {
    console.error('Mermaid accessibility validation failed:');
    inventory.failures.forEach((failure) => console.error(`  - ${failure}`));
    process.exitCode = 1;
    return;
  }

  if (process.argv.includes('--write-manifest')) {
    const buildDirectory = path.join(siteDirectory, 'build');
    if (!fs.existsSync(buildDirectory)) {
      throw new Error(`Build directory does not exist: ${buildDirectory}`);
    }
    fs.writeFileSync(
      path.join(buildDirectory, 'mermaid-routes.json'),
      `${JSON.stringify({
        schemaVersion: 1,
        baseUrl: configuration.baseUrl,
        diagrams: inventory.records,
      }, null, 2)}\n`,
      'utf8',
    );
  }

  console.log(`Mermaid accessibility validation passed (${inventory.records.length} diagrams).`);
}

if (process.argv[1] && path.resolve(process.argv[1]) === scriptPath) {
  await main();
}