// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

// cspell:words dagre

import fs from 'node:fs'
import { createRequire } from 'node:module'
import path from 'node:path'

const siteRoot = path.resolve(__dirname, '..')
const lockfile = JSON.parse(
  fs.readFileSync(path.join(siteRoot, 'package-lock.json'), 'utf8'),
) as {
  packages: Record<string, {
    version?: string
    dependencies?: Record<string, string>
    optionalDependencies?: Record<string, string>
    peerDependencies?: Record<string, string>
  }>
}

function isAtLeast(version: string, minimum: [number, number, number]): boolean {
  const parts = version.match(/^(\d+)\.(\d+)\.(\d+)$/)
  if (!parts) return false
  for (let index = 0; index < minimum.length; index++) {
    const actual = Number(parts[index + 1])
    if (actual !== minimum[index]) return actual > minimum[index]
  }
  return true
}

describe('numeric patched-version comparison', () => {
  it.each([
    ['4.17.23', false],
    ['4.9.99', false],
    ['4.18.0', true],
    ['4.18.1', true],
    ['4.19.0', true],
    ['5.0.0', true],
    ['4.18.0-beta.1', false],
    ['', false],
  ])('checks %s against 4.18.0', (version, patched) => {
    expect(isAtLeast(version, [4, 18, 0])).toBe(patched)
  })
})

describe('docs Mermaid dependency graph', () => {
  it('keeps every locked lodash-es copy at 4.18.0 or later', () => {
    const copies = Object.entries(lockfile.packages).filter(([packagePath]) =>
      packagePath.endsWith('node_modules/lodash-es'),
    )

    expect(copies.length).toBeGreaterThan(0)
    for (const [packagePath, metadata] of copies) {
      expect({ packagePath, patched: isAtLeast(metadata.version ?? '', [4, 18, 0]) }).toEqual({
        packagePath,
        patched: true,
      })
    }
  })

  it('keeps all Chevrotain packages independent of lodash-es', () => {
    const packages = Object.entries(lockfile.packages).filter(([packagePath]) =>
      /(?:^|\/)node_modules\/(?:chevrotain|@chevrotain\/[^/]+)$/.test(packagePath),
    )

    expect(packages.length).toBeGreaterThan(0)
    for (const [packagePath, metadata] of packages) {
      for (const field of ['dependencies', 'optionalDependencies', 'peerDependencies'] as const) {
        expect({ packagePath, field, dependencies: metadata[field] ?? {} })
          .not.toHaveProperty('dependencies.lodash-es')
      }
    }
  })

  it('locks Chevrotain major version 13 or later', () => {
    const chevrotain = lockfile.packages['node_modules/chevrotain']
    expect(chevrotain).toBeDefined()
    expect(isAtLeast(chevrotain.version ?? '', [13, 0, 0])).toBe(true)
  })

  it('resolves patched lodash-es from installed dagre-d3-es', () => {
    const packagePath = path.join(siteRoot, 'node_modules', 'dagre-d3-es', 'package.json')
    expect(fs.existsSync(packagePath)).toBe(true)
    const requireFromDependency = createRequire(packagePath)
    const installed = JSON.parse(
      fs.readFileSync(requireFromDependency.resolve('lodash-es/package.json'), 'utf8'),
    )

    expect(isAtLeast(installed.version ?? '', [4, 18, 0])).toBe(true)
  })
})
