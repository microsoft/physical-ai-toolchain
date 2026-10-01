// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

import fs from 'node:fs'
import { createRequire } from 'node:module'
import path from 'node:path'

const siteRoot = path.resolve(__dirname, '..')
const manifest = JSON.parse(fs.readFileSync(path.join(siteRoot, 'package.json'), 'utf8'))
const lockfile = JSON.parse(
  fs.readFileSync(path.join(siteRoot, 'package-lock.json'), 'utf8'),
) as { packages: Record<string, { version?: string }> }

describe('docs lodash-es override', () => {
  it('keeps every locked copy at the patched override version', () => {
    expect(manifest.overrides['lodash-es']).toBe('4.18.1')
    const copies = Object.entries(lockfile.packages).filter(([packagePath]) =>
      packagePath.endsWith('node_modules/lodash-es'),
    )

    expect(copies.length).toBeGreaterThan(0)
    for (const [packagePath, metadata] of copies) {
      expect({ packagePath, version: metadata.version }).toEqual({
        packagePath,
        version: manifest.overrides['lodash-es'],
      })
    }
  })

  it.each([
    'chevrotain',
    '@chevrotain/cst-dts-gen',
    '@chevrotain/gast',
  ])('resolves patched lodash-es from %s after installation', (dependency) => {
    const packagePath = path.join(siteRoot, 'node_modules', dependency, 'package.json')
    expect(fs.existsSync(packagePath)).toBe(true)
    const requireFromDependency = createRequire(packagePath)
    const installed = JSON.parse(
      fs.readFileSync(requireFromDependency.resolve('lodash-es/package.json'), 'utf8'),
    )

    expect(installed.version).toBe(manifest.overrides['lodash-es'])
  })
})
