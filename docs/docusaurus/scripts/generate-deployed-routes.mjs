// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const siteDirectory = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const buildDirectory = path.join(siteDirectory, 'build');

function collectHtmlFiles(directory) {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const absolutePath = path.join(directory, entry.name);
    return entry.isDirectory() ? collectHtmlFiles(absolutePath) : [absolutePath];
  });
}

function toRoute(filePath) {
  const relativePath = path.relative(buildDirectory, filePath).replaceAll('\\', '/');
  if (relativePath === '404.html') {
    return null;
  }
  if (relativePath === 'index.html') {
    return '/';
  }
  if (relativePath.endsWith('/index.html')) {
    return `/${relativePath.slice(0, -'/index.html'.length)}`;
  }
  return relativePath.endsWith('.html')
    ? `/${relativePath.slice(0, -'.html'.length)}`
    : null;
}

if (!fs.existsSync(path.join(buildDirectory, 'index.html'))) {
  throw new Error(`Build output not found at ${buildDirectory}`);
}

const { loadSiteConfig } = await import('@docusaurus/core/lib/server/config.js');
const { siteConfig } = await loadSiteConfig({ siteDir: siteDirectory });
const routes = collectHtmlFiles(buildDirectory)
  .map(toRoute)
  .filter((route) => route !== null)
  .sort();

if (new Set(routes).size !== routes.length) {
  throw new Error('Deployed route manifest contains duplicate routes');
}

const manifest = {
  schemaVersion: 1,
  baseUrl: siteConfig.baseUrl,
  routes,
  exclusions: [
    {
      path: '404.html',
      reason: 'Docusaurus 404 output is validated by the representative not-found journey.',
    },
  ],
};

fs.writeFileSync(
  path.join(buildDirectory, 'deployed-routes.json'),
  `${JSON.stringify(manifest, null, 2)}\n`,
  'utf8',
);
console.log(`Deployed route manifest generated (${routes.length} routes).`);