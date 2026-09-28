// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { spawnSync } from 'node:child_process'

import {
  collectBuiltRoutes,
  collectSearchRoutes,
  createSourceDocumentInventory,
  loadSourceDiscovery,
  ResolvedCaseBudgetReporter,
  validateRouteSets,
  validateSidebarOwnership,
  validateTierRoutes,
} from '../e2e/route-inventory'

const siteRoot = path.resolve(__dirname, '..')
const checkerPath = path.join(siteRoot, 'scripts', 'check-label-registry.mjs')
const contentDiscoveryCases = JSON.parse(
  fs.readFileSync(path.join(siteRoot, 'scripts', 'content-discovery-cases.json'), 'utf8'),
) as {
  documents: Array<{
    relativePath: string
    source: string
    routeBasePath: string
    expectedId: string
    expectedRoute: string
  }>
}
const fixtureFiles = [
  'docusaurus.config.js',
  'sidebars.js',
  'src/data/hubCards.tsx',
  'src/data/labelRegistry.cjs',
]

function createLabelFixture(): string {
  const fixtureRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'label-registry-'))
  for (const relativePath of fixtureFiles) {
    const destination = path.join(fixtureRoot, relativePath)
    fs.mkdirSync(path.dirname(destination), { recursive: true })
    fs.copyFileSync(path.join(siteRoot, relativePath), destination)
  }
  return fixtureRoot
}

function runLabelChecker(fixtureRoot: string) {
  return spawnSync(process.execPath, [checkerPath], {
    cwd: siteRoot,
    encoding: 'utf8',
    env: { ...process.env, LABEL_REGISTRY_ROOT: fixtureRoot },
  })
}

function updateFixture(
  fixtureRoot: string,
  relativePath: string,
  update: (contents: string) => string,
): void {
  const fixturePath = path.join(fixtureRoot, relativePath)
  fs.writeFileSync(fixturePath, update(fs.readFileSync(fixturePath, 'utf8')), 'utf8')
}

function expectLabelFailure(fixtureRoot: string, message: string): void {
  const result = runLabelChecker(fixtureRoot)
  expect(result.status).toBe(1)
  expect(result.stderr).toContain(message)
}

describe('governed label provenance', () => {
  it('accepts the current governed labels and freeform leaf-link labels', () => {
    const fixtureRoot = createLabelFixture()
    updateFixture(fixtureRoot, 'src/data/hubCards.tsx', (contents) =>
      contents.replace("label: 'System architecture'", "label: 'Architecture overview'"),
    )

    expect(runLabelChecker(fixtureRoot).status).toBe(0)
  })

  it('rejects a literal in a governed field', () => {
    const fixtureRoot = createLabelFixture()
    updateFixture(fixtureRoot, 'sidebars.js', (contents) =>
      contents.replace('label: labelRegistry.documentation', "label: 'Docs'"),
    )

    expectLabelFailure(fixtureRoot, 'governed label must use a direct registry member')
  })

  it('rejects an unproven dynamic label in a governed sidebar field', () => {
    const fixtureRoot = createLabelFixture()
    const baseline = runLabelChecker(fixtureRoot)
    expect(baseline.status).toBe(0)

    const sidebarPath = path.join(fixtureRoot, 'sidebars.js')
    const sidebar = fs.readFileSync(sidebarPath, 'utf8')
    fs.writeFileSync(
      sidebarPath,
      sidebar.replace('label: labelRegistry.documentation', 'label: item.label'),
      'utf8',
    )

    const result = runLabelChecker(fixtureRoot)
    expect(result.status).toBe(1)
    expect(result.stderr).toContain('governed label must use a direct registry member')
  })

  it('rejects a navigation projection that does not bind its governed label', () => {
    const fixtureRoot = createLabelFixture()
    updateFixture(fixtureRoot, 'docusaurus.config.js', (contents) =>
      contents.replace(
        'tierNavigation.map(({ label, route }) => ({ label, to: route }))',
        'tierNavigation.map(({ route }) => ({ label, to: route }))',
      ),
    )

    expectLabelFailure(fixtureRoot, 'governed label must use a direct registry member')
  })

  it('rejects unknown and stale registry keys', () => {
    const unknownFixture = createLabelFixture()
    updateFixture(unknownFixture, 'sidebars.js', (contents) =>
      contents.replace('labelRegistry.documentation', 'labelRegistry.doesNotExist'),
    )
    expectLabelFailure(unknownFixture, 'unknown labelRegistry key doesNotExist')

    const staleFixture = createLabelFixture()
    updateFixture(staleFixture, 'src/data/labelRegistry.cjs', (contents) =>
      contents.replace(
        "  architectureGuide: 'Architecture Guide',",
        "  architectureGuide: 'Architecture Guide',\n  orphan: 'Orphan',",
      ),
    )
    expectLabelFailure(staleFixture, 'labelRegistry.orphan is not consumed')
  })

  it('does not count an ungoverned registry reference as consumption', () => {
    const fixtureRoot = createLabelFixture()
    updateFixture(fixtureRoot, 'src/data/labelRegistry.cjs', (contents) =>
      contents.replace(
        "  architectureGuide: 'Architecture Guide',",
        "  architectureGuide: 'Architecture Guide',\n  orphan: 'Orphan',",
      ),
    )
    updateFixture(fixtureRoot, 'src/data/hubCards.tsx', (contents) =>
      contents.replace(
        "description: 'Set up your environment and deploy the reference architecture end-to-end.',",
        'description: labelRegistry.orphan,',
      ),
    )

    expectLabelFailure(fixtureRoot, 'labelRegistry.orphan is outside a governed label field')
    expectLabelFailure(fixtureRoot, 'labelRegistry.orphan is not consumed')
  })

  it('fails when an expected consumer is missing', () => {
    const fixtureRoot = createLabelFixture()
    fs.rmSync(path.join(fixtureRoot, 'sidebars.js'))

    expectLabelFailure(fixtureRoot, 'sidebars.js: expected consumer file is missing')
  })
})

describe('authored document inventory', () => {
  const sourceFiles = [
    { relativePath: 'README.md', contents: '---\nslug: /documentation\n---\n' },
    { relativePath: 'guide.md', contents: '---\ntitle: Guide\n---\n' },
    { relativePath: 'nested/README.mdx', contents: '---\ntitle: Nested\n---\n' },
  ]

  it('normalizes scalar source metadata and fails closed on unsupported forms', () => {
    expect(createSourceDocumentInventory(sourceFiles).map(({ id, route }) => ({ id, route }))).toEqual([
      { id: 'guide', route: '/guide' },
      { id: 'nested/README', route: '/nested' },
      { id: 'README', route: '/documentation' },
    ])
    expect(() =>
      createSourceDocumentInventory([
        { relativePath: 'unsupported.md', contents: '---\nslug:\n  nested: route\n---\n' },
      ]),
    ).toThrow('unsupported slug metadata form')
  })

  it('uses the shared content discovery corpus', () => {
    for (const entry of contentDiscoveryCases.documents) {
      expect(
        createSourceDocumentInventory(
          [{ relativePath: entry.relativePath, contents: entry.source }],
          entry.routeBasePath,
        ),
      ).toEqual([
        {
          id: entry.expectedId,
          relativePath: entry.relativePath,
          route: entry.expectedRoute,
        },
      ])
    }
  })

  it('requires exactly one top-level sidebar owner for every source ID', () => {
    const documents = createSourceDocumentInventory(sourceFiles)
    const validSidebar = [
      { type: 'doc', id: 'README' },
      { type: 'doc', id: 'guide' },
      {
        type: 'category',
        link: { type: 'doc', id: 'nested/README' },
        items: [{ type: 'autogenerated', dirName: 'nested' }],
      },
    ]
    expect(validateSidebarOwnership(documents, validSidebar)).toEqual([])
    expect(validateSidebarOwnership(documents, validSidebar.slice(1))).toContain(
      'README must have exactly one top-level sidebar owner; found 0',
    )
    expect(validateSidebarOwnership(documents, [...validSidebar, { type: 'doc', id: 'guide' }])).toContain(
      'guide must have exactly one top-level sidebar owner; found 2',
    )
  })

  it('requires source routes in build and exact source-to-search equality', () => {
    const documents = createSourceDocumentInventory(sourceFiles)
    const routes = documents.map(({ route }) => route)
    expect(validateRouteSets(documents, ['/', ...routes], routes)).toEqual([])
    expect(validateRouteSets(documents, routes.slice(1), routes)).toContain(
      'authored route is missing from build: /guide',
    )
    expect(validateRouteSets(documents, routes, routes.slice(1))).toContain(
      'authored route is missing from search: /guide',
    )
    expect(validateRouteSets(documents, routes, [...routes, '/unexpected'])).toContain(
      'search contains unexpected route: /unexpected',
    )
  })

  it('loads current config, preserves navigation regressions, and coheres tier routes', async () => {
    const discovery = await loadSourceDiscovery(siteRoot)
    const searchIndex = JSON.parse(
      fs.readFileSync(path.join(siteRoot, 'build', 'search-index.json'), 'utf8'),
    )
    const labelData = require('../src/data/labelRegistry.cjs')
    const sidebarLabels = discovery.sidebarItems.map((item) => (item as { label?: string }).label)
    expect(sidebarLabels).toEqual([
      labelData.labelRegistry.documentation,
      ...labelData.tierNavigation.map(({ label }: { label: string }) => label),
      labelData.labelRegistry.lifecycle,
      labelData.labelRegistry.recipes,
      labelData.labelRegistry.referenceAndGovernance,
    ])
    expect(discovery.sidebarItems).toHaveLength(10)
    expect(validateSidebarOwnership(discovery.documents, discovery.sidebarItems)).toEqual([])
    expect(validateTierRoutes(discovery.documents, labelData.tierNavigation)).toEqual([])
    expect(discovery.documents).toHaveLength(84)
    const sourceRoutes = new Set(discovery.documents.map(({ route }) => route))
    const builtRoutes = collectBuiltRoutes()
    expect(builtRoutes.filter((route) => !sourceRoutes.has(route))).toEqual([
      '/',
      '/accessibility/screen-reader-calibration',
      '/search',
    ])
    expect(collectSearchRoutes(searchIndex, discovery.baseUrl)).toHaveLength(84)
    expect(
      validateRouteSets(
        discovery.documents,
        builtRoutes,
        collectSearchRoutes(searchIndex, discovery.baseUrl),
      ),
    ).toEqual([])

    const themeConfig = discovery.siteConfig.themeConfig as {
      docs: { sidebar: { autoCollapseCategories: boolean; hideable: boolean } }
      navbar: { items: Array<{ items?: Array<{ label: string; to: string }>; label?: string; type?: string }> }
    }
    expect(themeConfig.docs.sidebar).toMatchObject({
      autoCollapseCategories: true,
      hideable: true,
    })
    const dropdown = themeConfig.navbar.items.find(({ type }) => type === 'dropdown')
    expect(dropdown?.items).toEqual(
      labelData.tierNavigation.map(({ label, route }: { label: string; route: string }) => ({
        label,
        to: route,
      })),
    )
  }, 120_000)
})

describe('browser execution reporting', () => {
  it('reports the resolved case count without blocking required coverage', () => {
    const reporter = new ResolvedCaseBudgetReporter()
    const suite = {
      allTests: () => Array.from({ length: 142 }, () => ({})),
    }
    const log = jest.spyOn(console, 'log').mockImplementation()

    expect(() => reporter.onBegin({} as never, suite as never)).not.toThrow()
    expect(log).toHaveBeenCalledWith('Resolved Playwright cases: 142')
    log.mockRestore()
  })
})
