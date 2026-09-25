// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

const path = require('node:path');

function normalizeRoute(route, baseUrl = '/') {
  let pathname = new URL(route, 'https://content-discovery.invalid').pathname;
  const normalizedBase = `/${baseUrl.split('/').filter(Boolean).join('/')}`;
  if (normalizedBase !== '/' && (pathname === normalizedBase || pathname.startsWith(`${normalizedBase}/`))) {
    pathname = pathname.slice(normalizedBase.length) || '/';
  }
  pathname = `/${pathname.split('/').filter(Boolean).join('/')}`;
  return pathname === '/' ? pathname : pathname.replace(/\/$/, '');
}

function readFrontMatterScalar(source, field, sourcePath) {
  const normalized = source.replaceAll('\r\n', '\n');
  if (!normalized.startsWith('---\n')) return null;
  const end = normalized.indexOf('\n---\n', 4);
  if (end < 0) throw new Error(`${sourcePath}: front matter is not terminated`);

  const matches = normalized
    .slice(4, end)
    .split('\n')
    .filter((line) => line.startsWith(`${field}:`));
  if (matches.length === 0) return null;
  if (matches.length !== 1) throw new Error(`${sourcePath}: front matter ${field} must appear once`);

  const value = matches[0].slice(field.length + 1).trim();
  if (!value || ['[', '{', '|', '>'].includes(value[0])) {
    throw new Error(`${sourcePath}: unsupported ${field} metadata form`);
  }
  if (value.startsWith('"')) {
    try {
      const parsed = JSON.parse(value);
      if (typeof parsed === 'string') return parsed;
    } catch {
      throw new Error(`${sourcePath}: unsupported ${field} metadata form`);
    }
  }
  if (value.startsWith("'")) {
    if (!value.endsWith("'") || value.length < 2) {
      throw new Error(`${sourcePath}: unsupported ${field} metadata form`);
    }
    return value.slice(1, -1).replaceAll("''", "'");
  }
  if (/[[\]{}]/.test(value)) throw new Error(`${sourcePath}: unsupported ${field} metadata form`);
  return value;
}

function sourceDocumentIdentity(relativePath, source, routeBasePath = '/') {
  const normalizedPath = relativePath.replaceAll('\\', '/');
  const pathWithoutExtension = normalizedPath.replace(/\.(md|mdx)$/i, '');
  const directory = path.posix.dirname(pathWithoutExtension) === '.'
    ? ''
    : path.posix.dirname(pathWithoutExtension);
  const defaultName = path.posix.basename(pathWithoutExtension);
  const id = readFrontMatterScalar(source, 'id', normalizedPath) ?? pathWithoutExtension;
  const slug = readFrontMatterScalar(source, 'slug', normalizedPath);
  const routePath = slug
    ? slug.startsWith('/') ? slug : path.posix.join(directory, slug)
    : defaultName === 'README' ? directory : path.posix.join(directory, defaultName);
  const route = normalizeRoute(`/${[routeBasePath, routePath].flatMap((part) => part.split('/')).filter(Boolean).join('/')}`);
  return { id, relativePath: normalizedPath, route };
}

module.exports = { normalizeRoute, readFrontMatterScalar, sourceDocumentIdentity };