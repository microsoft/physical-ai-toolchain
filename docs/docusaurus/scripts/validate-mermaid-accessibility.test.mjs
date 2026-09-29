// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import {
  collectMermaidInventory,
  extractMermaidDiagrams,
  parseMermaidAccessibilityMetadata,
  sourcePathToRoute,
} from './validate-mermaid-accessibility.mjs';
import contentDiscovery from './content-discovery.cjs';

const { readFrontMatterScalar, sourceDocumentIdentity } = contentDiscovery;

const contentDiscoveryCases = JSON.parse(
  fs.readFileSync(new URL('./content-discovery-cases.json', import.meta.url), 'utf8'),
);

test('shared content discovery corpus covers scalar, route, README, and malformed metadata', () => {
  for (const entry of contentDiscoveryCases.documents) {
    assert.deepEqual(
      sourceDocumentIdentity(entry.relativePath, entry.source, entry.routeBasePath),
      { id: entry.expectedId, relativePath: entry.relativePath, route: entry.expectedRoute },
    );
  }
  for (const entry of contentDiscoveryCases.invalid) {
    assert.throws(
      () => readFrontMatterScalar(entry.source, 'slug', entry.relativePath),
      new RegExp(entry.error),
    );
  }
});

test('extracts backtick, tilde, blockquote, and longer nested Mermaid fences', () => {
  const source = [
    '```mermaid',
    'accTitle: First',
    'accDescr: First description',
    '```',
    '> ~~~mermaid',
    '> accTitle: Second',
    '> accDescription: Second description',
    '> ~~~',
    '````mermaid',
    'accTitle: Third',
    'accDescr: Includes ``` inside',
    '```',
    '````',
  ].join('\n');

  assert.equal(extractMermaidDiagrams(source).length, 3);
});

test('requires exactly one active non-empty title and description', () => {
  const valid = parseMermaidAccessibilityMetadata(
    '%% accTitle: ignored\naccTitle: Diagram\naccDescr: Description',
    'valid.md',
  );
  assert.deepEqual(valid.failures, []);
  assert.equal(valid.title, 'Diagram');
  assert.equal(valid.description, 'Description');

  const invalid = parseMermaidAccessibilityMetadata(
    'accTitle:\naccDescr: First\naccDescription: Second',
    'invalid.md',
  );
  assert.deepEqual(invalid.failures, [
    'invalid.md: Mermaid diagram 1 has an empty accTitle',
    'invalid.md: Mermaid diagram 1 requires exactly one active accDescr; found 2',
  ]);
});

test('fails precisely for unterminated fences and unsupported route metadata', () => {
  assert.throws(
    () => extractMermaidDiagrams('```mermaid\naccTitle: Diagram', 'unterminated.md'),
    /unterminated\.md: Mermaid fence is not terminated/,
  );
  assert.throws(
    () => sourcePathToRoute('guide.md', '---\nslug:\n  nested: route\n---\n'),
    /guide\.md: unsupported slug metadata form/,
  );
});

test('honors exclusions and maps included sources to Docusaurus routes', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'mermaid-a11y-'));
  try {
    fs.mkdirSync(path.join(root, 'excluded'), { recursive: true });
    fs.mkdirSync(path.join(root, 'guides'), { recursive: true });
    fs.writeFileSync(
      path.join(root, 'guides', 'README.md'),
      '```mermaid\naccTitle: Included\naccDescr: Included diagram\ngraph LR\n```\n',
    );
    fs.writeFileSync(
      path.join(root, 'excluded', 'ignored.md'),
      '```mermaid\naccTitle:\naccDescr:\ngraph LR\n```\n',
    );

    const inventory = collectMermaidInventory({
      docsDirectory: root,
      exclude: ['excluded/**'],
      routeBasePath: '/',
    });
    assert.deepEqual(inventory.failures, []);
    assert.deepEqual(inventory.records, [{
      description: 'Included diagram',
      ordinal: 1,
      route: '/guides',
      sourcePath: 'guides/README.md',
      title: 'Included',
    }]);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('reports an empty deployed Mermaid boundary', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'mermaid-empty-'));
  try {
    assert.deepEqual(
      collectMermaidInventory({ docsDirectory: root }).failures,
      ['No deployed Mermaid diagrams were found'],
    );
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});