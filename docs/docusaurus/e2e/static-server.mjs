// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import express from 'express';
import { rateLimit } from 'express-rate-limit';

const currentDirectory = path.dirname(fileURLToPath(import.meta.url));
const defaultBuildDirectory = path.resolve(currentDirectory, '..', 'build');
const scriptPath = fileURLToPath(import.meta.url);

function positiveInteger(value, name) {
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < 1) {
    throw new Error(`${name} must be a positive integer; received ${value}`);
  }
  return parsed;
}

export function resolveServerConfig(environment = process.env) {
  const rawPort = environment.DOCS_E2E_PORT ?? environment.PORT ?? '3001';
  const port = Number(rawPort);
  if (!Number.isInteger(port) || port < 1 || port > 65_535) {
    throw new Error(`Server port must be an integer from 1 through 65535; received ${rawPort}`);
  }
  const host = environment.DOCS_E2E_HOST ?? environment.HOST ?? '127.0.0.1';
  if (!host.trim()) {
    throw new Error('Server host must not be empty');
  }
  return {
    basePath: '/physical-ai-toolchain/',
    buildDirectory: path.resolve(environment.DOCS_BUILD_DIRECTORY ?? defaultBuildDirectory),
    host,
    port,
    rateLimitMaxRequests: positiveInteger(
      environment.DOCS_E2E_RATE_LIMIT_MAX_REQUESTS ?? '10000',
      'DOCS_E2E_RATE_LIMIT_MAX_REQUESTS',
    ),
    rateLimitWindowMs: positiveInteger(
      environment.DOCS_E2E_RATE_LIMIT_WINDOW_MS ?? '60000',
      'DOCS_E2E_RATE_LIMIT_WINDOW_MS',
    ),
  };
}

export function createStaticApp({
  basePath,
  buildDirectory,
  rateLimitMaxRequests,
  rateLimitWindowMs,
}) {
  for (const requiredFile of ['index.html', '404.html']) {
    if (!fs.existsSync(path.join(buildDirectory, requiredFile))) {
      throw new Error(`Build output is missing ${requiredFile} at ${buildDirectory}`);
    }
  }

  const app = express();
  app.use(rateLimit({
    windowMs: rateLimitWindowMs,
    limit: rateLimitMaxRequests,
    standardHeaders: 'draft-8',
    legacyHeaders: false,
  }));
  app.use(basePath, express.static(buildDirectory, { extensions: ['html'], index: 'index.html' }));
  app.get('/', (_request, response) => response.redirect(basePath));
  app.use((_request, response) => response.status(404).sendFile(path.join(buildDirectory, '404.html')));
  return app;
}

export function startStaticServer(config = resolveServerConfig()) {
  const app = createStaticApp(config);
  const server = app.listen(config.port, config.host, () => {
    console.log(
      `Serving ${config.buildDirectory} at http://${config.host}:${config.port}${config.basePath}`,
    );
  });
  server.keepAliveTimeout = 60_000;
  server.headersTimeout = 65_000;
  return server;
}

if (process.argv[1] && path.resolve(process.argv[1]) === scriptPath) {
  startStaticServer();
}