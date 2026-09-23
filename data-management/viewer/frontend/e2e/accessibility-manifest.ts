import fs from 'node:fs'
import { pathToFileURL } from 'node:url'

export interface ViewerAccessibilityCase {
  testId: string
  testTitle: string
  controls: string[]
  states: string[]
  criteria: string[]
  methods: string[]
}

const entry = (
  testId: string,
  testTitle: string,
  controls: string[],
  states: string[],
  criteria: string[],
  methods: string[],
): ViewerAccessibilityCase => ({ testId, testTitle, controls, states, criteria, methods })

export const viewerAccessibilityManifest: ViewerAccessibilityCase[] = [
  entry(
    'viewer-adaptive-content',
    'V19 preserves loaded and transient content under adaptive rendering',
    ['workspace'],
    ['adaptive'],
    ['WCAG22-1.4.10'],
    ['PLAYWRIGHT_VISUAL'],
  ),
  entry(
    'viewer-visible-focus',
    'V02 V03 V05 and V19 primary focus stops stay visible and unobscured',
    ['primary-focus-stops'],
    ['keyboard-focus'],
    ['WCAG22-2.4.11'],
    ['PLAYWRIGHT_KEYBOARD', 'PLAYWRIGHT_VISUAL'],
  ),
  entry(
    'viewer-targets',
    'V08 V09 and V19 targets satisfy size or spacing and pointer cancellation',
    ['interactive-targets'],
    ['pointer'],
    ['WCAG22-2.5.7', 'WCAG22-2.5.8'],
    ['PLAYWRIGHT_POINTER', 'PLAYWRIGHT_VISUAL'],
  ),
  entry(
    'viewer-empty-tab-order',
    'V02 V03 and V04 empty shell follows authored bidirectional tab order',
    ['empty-shell'],
    ['keyboard'],
    ['WCAG22-2.1.1', 'WCAG22-2.4.3'],
    ['PLAYWRIGHT_KEYBOARD'],
  ),
  entry(
    'viewer-loaded-tab-order',
    'V02 V03 and V05 loaded primary controls follow authored bidirectional tab order',
    ['workspace'],
    ['keyboard'],
    ['WCAG22-2.1.1', 'WCAG22-2.4.3'],
    ['PLAYWRIGHT_KEYBOARD'],
  ),
  entry(
    'viewer-composite-keyboard',
    'V05 V06 V08 and V09 composite controls support keyboard operation and exit',
    ['composite-controls'],
    ['keyboard'],
    ['WCAG22-2.1.1'],
    ['PLAYWRIGHT_KEYBOARD'],
  ),
  entry(
    'viewer-task-keyboard',
    'V07 V10 V12 and V16 task controls are keyboard operable',
    ['task-controls'],
    ['keyboard'],
    ['WCAG22-2.1.1'],
    ['PLAYWRIGHT_KEYBOARD'],
  ),
  entry(
    'viewer-error-alert',
    'V03 and V04 episode list failure is exposed as an alert',
    ['episode-list'],
    ['error'],
    ['WCAG22-4.1.3'],
    ['PLAYWRIGHT_LIVE_REGION'],
  ),
  entry(
    'viewer-save-status',
    'V05 save status preserves focus and reports completion',
    ['save'],
    ['success'],
    ['WCAG22-4.1.3'],
    ['PLAYWRIGHT_LIVE_REGION', 'PLAYWRIGHT_KEYBOARD'],
  ),
  entry(
    'viewer-export-status',
    'V18 export completion uses status semantics and failure uses alert semantics',
    ['export'],
    ['success', 'error'],
    ['WCAG22-4.1.3'],
    ['PLAYWRIGHT_LIVE_REGION'],
  ),
  entry(
    'viewer-local-process',
    'J04 completes a representative local Viewer process',
    ['workflow'],
    ['complete'],
    ['WCAG22-2.1.1', 'WCAG22-4.1.2'],
    ['PLAYWRIGHT_KEYBOARD', 'PLAYWRIGHT_TREE'],
  ),
  entry(
    'viewer-loaded-axe',
    'V02 through V18 loaded workspace has no axe violations',
    ['workspace'],
    ['loaded'],
    ['WCAG22-4.1.2'],
    ['AXE'],
  ),
  entry(
    'viewer-empty-axe',
    'V02 V03 and V04 empty shell has no axe violations',
    ['empty-shell'],
    ['empty'],
    ['WCAG22-4.1.2'],
    ['AXE'],
  ),
  entry(
    'viewer-analyzer-axe',
    'V05 and V15 analyzer state has no axe violations',
    ['analyzer'],
    ['loaded'],
    ['WCAG22-4.1.2'],
    ['AXE'],
  ),
  entry(
    'viewer-dataset-axe',
    'V02 dataset chooser has no axe violations',
    ['dataset-chooser'],
    ['loaded'],
    ['WCAG22-4.1.2'],
    ['AXE'],
  ),
  entry(
    'viewer-export-axe',
    'V18 export dialog has no axe violations',
    ['export-dialog'],
    ['open'],
    ['WCAG22-4.1.2'],
    ['AXE'],
  ),
  entry(
    'viewer-exact-semantics',
    'V02 V05 and V18 expose exact page and component semantics',
    ['page', 'workspace', 'export'],
    ['loaded'],
    ['WCAG22-1.3.1', 'WCAG22-4.1.2'],
    ['PLAYWRIGHT_TREE'],
  ),
  entry(
    'viewer-risk-inventory',
    'V02 through V19 accessibility-risk source classes have explicit ownership',
    ['source-inventory'],
    ['static'],
    ['WCAG22-4.1.2'],
    ['SOURCE_INSPECTION'],
  ),
  entry(
    'viewer-selection',
    'V02 and V03 support keyboard dataset and episode selection',
    ['dataset-chooser', 'episode-list'],
    ['keyboard'],
    ['WCAG22-2.1.1'],
    ['PLAYWRIGHT_KEYBOARD'],
  ),
  entry(
    'viewer-workspace-semantics',
    'V05 V06 V08 V13 V14 and V18 expose exact workspace semantics',
    ['workspace'],
    ['loaded'],
    ['WCAG22-1.3.1', 'WCAG22-4.1.2'],
    ['PLAYWRIGHT_TREE'],
  ),
  entry(
    'viewer-focus-boundaries',
    'V02 and V18 preserve forward and reverse focus boundaries',
    ['workspace', 'export-dialog'],
    ['keyboard'],
    ['WCAG22-2.4.3'],
    ['PLAYWRIGHT_KEYBOARD'],
  ),
  entry(
    'viewer-adaptive-geometry',
    'V19 preserves adaptive layout focus and target geometry',
    ['workspace'],
    ['adaptive'],
    ['WCAG22-1.4.10', 'WCAG22-2.4.11', 'WCAG22-2.5.8'],
    ['PLAYWRIGHT_VISUAL'],
  ),
  ...[
    ['F01', 'Viewer Swagger UI'],
    ['F02', 'Viewer ReDoc'],
    ['F03', 'VLM Judge Swagger UI'],
    ['F04', 'VLM Judge ReDoc'],
    ['F05', 'OpenAI Shim Swagger UI'],
    ['F06', 'OpenAI Shim ReDoc'],
  ].map(([testId, name]) =>
    entry(
      `viewer-docs-${testId.toLowerCase()}`,
      `${testId} renders ${name} with safe API states`,
      ['generated-api-documentation'],
      ['loaded', 'reflow'],
      ['WCAG22-1.3.1', 'WCAG22-2.1.1', 'WCAG22-4.1.2'],
      ['PLAYWRIGHT_KEYBOARD', 'PLAYWRIGHT_TREE', 'PLAYWRIGHT_VISUAL'],
    ),
  ),
]

export function emitViewerAccessibilityManifest(outputPath: string): void {
  const testIds = new Set(viewerAccessibilityManifest.map((item) => item.testId))
  const testTitles = new Set(viewerAccessibilityManifest.map((item) => item.testTitle))
  if (
    testIds.size !== viewerAccessibilityManifest.length ||
    testTitles.size !== viewerAccessibilityManifest.length
  ) {
    throw new Error('Viewer accessibility manifest identities must be unique')
  }
  if (
    viewerAccessibilityManifest.some(
      (item) =>
        !item.controls.length ||
        !item.states.length ||
        !item.criteria.length ||
        !item.methods.length,
    )
  ) {
    throw new Error(
      'Viewer accessibility manifest entries must declare controls, states, criteria, and methods',
    )
  }
  fs.mkdirSync(new URL('.', pathToFileURL(outputPath)), { recursive: true })
  fs.writeFileSync(
    outputPath,
    `${JSON.stringify({ schemaVersion: '1.0.0', cases: viewerAccessibilityManifest }, null, 2)}\n`,
  )
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const outputPath = process.argv[2]
  if (!outputPath) throw new Error('Output path is required')
  emitViewerAccessibilityManifest(outputPath)
}
