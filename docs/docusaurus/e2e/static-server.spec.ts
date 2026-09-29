// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

import { spawn, spawnSync, type ChildProcess } from 'node:child_process';
import fs from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';

import { expect, test } from '@playwright/test';

async function reservePort(): Promise<number> {
  const server = net.createServer();
  await new Promise<void>((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  const address = server.address();
  if (!address || typeof address === 'string') {
    throw new Error('Unable to reserve a loopback port');
  }
  await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
  return address.port;
}

async function startFixtureServer(
  buildDirectory: string,
  port: number,
  environment: NodeJS.ProcessEnv = {},
): Promise<ChildProcess> {
  const child = spawn(process.execPath, ['e2e/static-server.mjs'], {
    cwd: process.cwd(),
    env: {
      ...process.env,
      DOCS_BUILD_DIRECTORY: buildDirectory,
      DOCS_E2E_PORT: String(port),
      ...environment,
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let output = '';
  await new Promise<void>((resolve, reject) => {
    const consume = (chunk: Buffer) => {
      output += chunk.toString();
      if (output.includes('Serving ')) {
        resolve();
      }
    };
    child.stdout?.on('data', consume);
    child.stderr?.on('data', consume);
    child.once('exit', (code) => reject(new Error(`Fixture server exited ${code}: ${output}`)));
  });
  return child;
}

async function stopFixtureServer(child: ChildProcess): Promise<void> {
  if (child.exitCode !== null) {
    return;
  }
  await new Promise<void>((resolve) => {
    child.once('exit', () => resolve());
    child.kill();
  });
}

test.describe.configure({ mode: 'serial' });

test('static server rejects invalid ports and missing build output', async () => {
  const invalidPort = spawnSync(process.execPath, ['e2e/static-server.mjs'], {
    cwd: process.cwd(),
    encoding: 'utf8',
    env: { ...process.env, DOCS_E2E_PORT: 'invalid' },
  });
  expect(invalidPort.status).not.toBe(0);
  expect(invalidPort.stderr).toContain('Server port must be an integer');

  const emptyBuild = fs.mkdtempSync(path.join(os.tmpdir(), 'docs-server-empty-'));
  try {
    const missingBuild = spawnSync(process.execPath, ['e2e/static-server.mjs'], {
      cwd: process.cwd(),
      encoding: 'utf8',
      env: {
        ...process.env,
        DOCS_BUILD_DIRECTORY: emptyBuild,
        DOCS_E2E_PORT: String(await reservePort()),
      },
    });
    expect(missingBuild.status).not.toBe(0);
    expect(missingBuild.stderr).toContain('Build output is missing index.html');
  } finally {
    fs.rmSync(emptyBuild, { recursive: true, force: true });
  }
});

test('static server rate limits repeated file-system requests', async () => {
  const buildDirectory = fs.mkdtempSync(path.join(os.tmpdir(), 'docs-server-rate-limit-'));
  fs.writeFileSync(path.join(buildDirectory, 'index.html'), '<h1>Fixture home</h1>');
  fs.writeFileSync(path.join(buildDirectory, '404.html'), '<h1>Missing</h1>');

  const port = await reservePort();
  const child = await startFixtureServer(buildDirectory, port, {
    DOCS_E2E_RATE_LIMIT_MAX_REQUESTS: '2',
    DOCS_E2E_RATE_LIMIT_WINDOW_MS: '60000',
  });
  const origin = `http://127.0.0.1:${port}`;
  try {
    expect((await fetch(origin, { redirect: 'manual' })).status).toBe(302);
    expect((await fetch(origin, { redirect: 'manual' })).status).toBe(302);
    expect((await fetch(origin, { redirect: 'manual' })).status).toBe(429);
  } finally {
    await stopFixtureServer(child);
    fs.rmSync(buildDirectory, { recursive: true, force: true });
  }
});

test('static server serves base assets and current 404 output after rebuilds', async () => {
  const buildDirectory = fs.mkdtempSync(path.join(os.tmpdir(), 'docs-server-build-'));
  const assetsDirectory = path.join(buildDirectory, 'assets');
  fs.mkdirSync(assetsDirectory);
  fs.writeFileSync(path.join(buildDirectory, 'index.html'), '<h1>Fixture home</h1>');
  fs.writeFileSync(path.join(buildDirectory, '404.html'), '<h1>Missing version one</h1>');
  fs.writeFileSync(path.join(assetsDirectory, 'site.css'), 'body { color: black; }');

  const port = await reservePort();
  const child = await startFixtureServer(buildDirectory, port);
  const origin = `http://127.0.0.1:${port}`;
  try {
    const root = await fetch(origin, { redirect: 'manual' });
    expect(root.status).toBe(302);
    expect(root.headers.get('location')).toBe('/physical-ai-toolchain/');

    const home = await fetch(`${origin}/physical-ai-toolchain/`);
    expect(home.status).toBe(200);
    expect(await home.text()).toContain('Fixture home');

    const asset = await fetch(`${origin}/physical-ai-toolchain/assets/site.css`);
    expect(asset.status).toBe(200);
    expect(await asset.text()).toContain('color: black');

    const firstMissing = await fetch(`${origin}/missing-one`);
    expect(firstMissing.status).toBe(404);
    expect(await firstMissing.text()).toContain('Missing version one');

    fs.writeFileSync(path.join(buildDirectory, '404.html'), '<h1>Missing version two</h1>');
    const rebuiltMissing = await fetch(`${origin}/missing-two`);
    expect(rebuiltMissing.status).toBe(404);
    expect(await rebuiltMissing.text()).toContain('Missing version two');
  } finally {
    await stopFixtureServer(child);
    fs.rmSync(buildDirectory, { recursive: true, force: true });
  }
});