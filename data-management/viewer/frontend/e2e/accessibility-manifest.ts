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
    'viewer-release-publication',
    'curated dataset release workflow \u203a submits a release, runs the worker, and shows the verified result without duplicates',
    ['release-dialog', 'release-jobs', 'verified-result'],
    ['previewed', 'accepted', 'reloaded', 'committed'],
    ['WCAG22-3.3.4', 'WCAG22-4.1.2'],
    ['HTTP_API', 'PLAYWRIGHT_TREE'],
  ),
  entry(
    'viewer-release-dialog-keyboard',
    'curated dataset release workflow \u203a keyboard-only dialog use returns focus to Create release after Escape',
    ['create-release', 'release-dialog', 'release-name'],
    ['open', 'keyboard', 'closed', 'focus-restored'],
    ['WCAG22-2.1.1', 'WCAG22-2.1.2', 'WCAG22-2.4.3'],
    ['PLAYWRIGHT_KEYBOARD', 'PLAYWRIGHT_TREE'],
  ),
  entry(
    'viewer-release-workspace-reflow',
    'curated dataset release workflow \u203a narrow viewports reflow without horizontal page scrolling',
    ['release-workspace', 'create-release'],
    ['reflow-320'],
    ['WCAG22-1.4.10'],
    ['PLAYWRIGHT_VISUAL'],
  ),
  entry(
    'viewer-release-dialog-reflow',
    'mobile release workflow \u203a opens the release dialog within a phone-width viewport',
    ['release-dialog'],
    ['open', 'reflow-390'],
    ['WCAG22-1.4.10'],
    ['PLAYWRIGHT_TREE', 'PLAYWRIGHT_VISUAL'],
  ),
  entry(
    'viewer-release-capacity-recovery',
    'release capacity and catalog boundaries through the real API \u203a a shortage of admissible capacity is refused without output and recovers after the holding job is cancelled',
    ['release-dialog', 'release-alert', 'submit-release'],
    ['capacity-refused', 'no-output', 'retried', 'committed'],
    ['WCAG22-3.3.1', 'WCAG22-3.3.4', 'WCAG22-4.1.3'],
    ['HTTP_API', 'PLAYWRIGHT_TREE'],
  ),
  entry(
    'viewer-release-source-catalog',
    'release capacity and catalog boundaries through the real API \u203a a committed release is absent from the ordinary dataset catalog and listed under its source',
    ['dataset-catalog', 'source-releases'],
    ['committed', 'source-associated'],
    ['WCAG22-4.1.2'],
    ['HTTP_API', 'PLAYWRIGHT_TREE'],
  ),
  entry(
    'viewer-release-read-only-source',
    'release API contract against the real backend \u203a a committed source release is browsable read-only with episodes and camera media',
    ['release-details', 'release-episodes', 'release-cameras', 'source-tabs'],
    ['committed', 'read-only', 'media-loaded', 'source-restored'],
    ['WCAG22-2.1.1', 'WCAG22-3.3.4', 'WCAG22-4.1.2'],
    ['HTTP_API', 'PLAYWRIGHT_KEYBOARD', 'PLAYWRIGHT_TREE'],
  ),
  entry(
    'viewer-release-advisory-api',
    'release API contract against the real backend \u203a a label saved through the real API leaves the advisory preview valid',
    ['episode-labels-api', 'release-preview-api', 'release-submit-api'],
    ['saved', 'advisory-preview', 'accepted'],
    ['WCAG22-3.3.4'],
    ['HTTP_API'],
  ),
  entry(
    'viewer-release-cancel-cleanup',
    'release API contract against the real backend \u203a an accepted job cancelled before publication leaves no output after the worker runs',
    ['release-cancel-api'],
    ['accepted', 'cancelled', 'purged', 'no-output'],
    ['WCAG22-3.3.4'],
    ['HTTP_API'],
  ),
  entry(
    'viewer-release-inaccessible-pointer',
    'release dialog and job panel through the real API \u203a an inaccessible saved job clears its pointer without resubmitting',
    ['release-job-panel', 'create-release', 'release-dialog'],
    ['accepted', 'inaccessible', 'pointer-cleared', 'no-resubmission', 'focus-restored'],
    ['WCAG22-2.1.1', 'WCAG22-2.4.3', 'WCAG22-3.3.4'],
    ['HTTP_API', 'PLAYWRIGHT_KEYBOARD', 'PLAYWRIGHT_TREE'],
  ),
  entry(
    'viewer-release-mobile-publication',
    'release dialog and job panel through the real API \u203a a phone-width release completes from selection to verified result without overflow',
    ['release-dialog', 'release-jobs', 'verified-result'],
    ['reflow-390', 'previewed', 'accepted', 'committed'],
    ['WCAG22-1.4.10', 'WCAG22-3.3.4', 'WCAG22-4.1.2'],
    ['HTTP_API', 'PLAYWRIGHT_TREE', 'PLAYWRIGHT_VISUAL'],
  ),
  entry(
    'viewer-release-advisory-dialog',
    'release dialog and job panel through the real API \u203a a saved label leaves the advisory preview usable for submission',
    ['episode-labels-api', 'release-dialog', 'submit-release', 'release-job-panel'],
    ['previewed', 'saved', 'accepted'],
    ['WCAG22-3.3.4', 'WCAG22-4.1.2'],
    ['HTTP_API', 'PLAYWRIGHT_TREE'],
  ),
  entry(
    'viewer-release-reload-recovery',
    'release dialog and job panel through the real API \u203a reloading while a job is accepted restores the job without a second submission',
    ['release-jobs', 'release-job-panel', 'verified-result'],
    ['accepted', 'reloaded', 'no-resubmission', 'committed'],
    ['WCAG22-3.3.4', 'WCAG22-4.1.2'],
    ['HTTP_API', 'PLAYWRIGHT_TREE'],
  ),
  entry(
    'viewer-release-cancel-panel',
    'release dialog and job panel through the real API \u203a cancelling from the active job panel stops the job and leaves no output',
    ['release-job-panel', 'cancel-job'],
    ['accepted', 'cancelled', 'no-output'],
    ['WCAG22-3.3.4', 'WCAG22-4.1.2'],
    ['HTTP_API', 'PLAYWRIGHT_TREE'],
  ),
  entry(
    'viewer-release-control-admission',
    'release read and control admission through the real HTTP service \u203a concurrent source reads are admitted or refused with bounded status and the control lane stays responsive',
    ['source-releases-api', 'release-job-status-api'],
    ['concurrent-reads', 'bounded-refusal', 'responsive-control'],
    ['WCAG22-3.3.4'],
    ['HTTP_API'],
  ),
  entry(
    'viewer-release-selection-keyboard',
    'V21 release selection preserves keyboard focus and announces filter states',
    [
      'release-label-filters',
      'select-shown',
      'selected-only',
      'release-selection-status',
      'release-dialog',
    ],
    ['open', 'filtered', 'mixed', 'hidden-selection', 'empty', 'deselected', 'closed'],
    ['WCAG22-2.1.1', 'WCAG22-2.1.2', 'WCAG22-2.4.3', 'WCAG22-4.1.2', 'WCAG22-4.1.3'],
    ['PLAYWRIGHT_KEYBOARD', 'PLAYWRIGHT_TREE', 'PLAYWRIGHT_LIVE_REGION'],
  ),
  entry(
    'viewer-release-mobile-filters',
    'V21 mobile release filters expose disclosure state and keyboard controls',
    ['release-filter-disclosure', 'release-label-filters', 'selected-only'],
    ['reflow-320', 'collapsed', 'expanded', 'filtered'],
    ['WCAG22-2.1.1', 'WCAG22-2.4.3', 'WCAG22-4.1.2'],
    ['PLAYWRIGHT_KEYBOARD', 'PLAYWRIGHT_TREE'],
  ),
  entry(
    'viewer-release-adaptive',
    'V21 release dialog preserves reflow text spacing and unclipped focus',
    ['release-dialog', 'release-name', 'release-actions'],
    ['open', 'reflow-320', 'resize-200', 'text-spacing', 'focus'],
    ['WCAG22-1.4.4', 'WCAG22-1.4.10', 'WCAG22-1.4.12', 'WCAG22-2.4.7', 'WCAG22-2.4.11'],
    ['PLAYWRIGHT_KEYBOARD', 'PLAYWRIGHT_VISUAL'],
  ),
  entry(
    'viewer-release-axe',
    'V20 and V21 release workspace and selection states have no axe violations',
    [
      'source-tabs',
      'release-workspace',
      'release-dialog',
      'release-label-filters',
      'selected-only',
    ],
    ['open', 'filtered', 'empty', 'closed'],
    ['WCAG22-1.3.1', 'WCAG22-1.4.3', 'WCAG22-4.1.2'],
    ['AXE'],
  ),
  entry(
    'viewer-job-control-recovery',
    'P06 job controls retain progress through refresh failure and keyboard cancel retry',
    ['durable-jobs', 'cancel-job', 'retry-job', 'refresh-jobs'],
    ['running', 'cancelled', 'failed', 'refresh-error', 'succeeded', 'reflow'],
    ['WCAG22-2.1.1', 'WCAG22-2.4.3', 'WCAG22-4.1.3', 'WCAG22-1.4.10'],
    ['PLAYWRIGHT_KEYBOARD', 'PLAYWRIGHT_TREE', 'PLAYWRIGHT_LIVE_REGION', 'PLAYWRIGHT_VISUAL'],
  ),
  entry(
    'viewer-label-persistence',
    'P06 real backend persists labels and rejects stale writes',
    ['episode-labels-api'],
    ['saved', 'conflict', 'reverted'],
    ['WCAG22-3.3.4'],
    ['HTTP_API'],
  ),
  entry(
    'viewer-label-recovery',
    'P06 fresh browser recovers a saved label change without browser drafts',
    ['trajectory-labels', 'save', 'workspace-save-status'],
    ['saved', 'fresh-browser'],
    ['WCAG22-3.3.4', 'WCAG22-4.1.2'],
    ['HTTP_API', 'PLAYWRIGHT_TREE'],
  ),
  entry(
    'viewer-edit-recovery',
    'P06 fresh browser recovers complete saved edits and explicit clearing',
    ['episode-edits-api', 'trajectory-subtasks', 'workspace-save-status'],
    ['saved', 'fresh-browser', 'cleared'],
    ['WCAG22-3.3.4'],
    ['HTTP_API', 'PLAYWRIGHT_TREE'],
  ),
  entry(
    'viewer-label-read-recovery',
    'P06 real backend recovers an unsaved label after an episode read failure',
    ['trajectory-labels', 'retry-episode-load', 'save', 'episode-navigation'],
    ['read-error', 'dirty', 'cancelled-navigation', 'recovered', 'saved'],
    ['WCAG22-2.1.1', 'WCAG22-3.3.4', 'WCAG22-4.1.3'],
    ['HTTP_API', 'PLAYWRIGHT_KEYBOARD', 'PLAYWRIGHT_TREE', 'PLAYWRIGHT_LIVE_REGION'],
  ),
  entry(
    'viewer-judge-application-withdrawal',
    'P06 real echo judge displays evidence without writes and withdraws explicit application',
    ['judge-assessment', 'apply-label', 'withdrawal-preview', 'confirm-label-removal'],
    ['current', 'applied', 'withdrawn', 'stale', 'reflow', 'enlarged-text'],
    ['WCAG22-2.1.1', 'WCAG22-3.3.4', 'WCAG22-1.4.10'],
    ['HTTP_API', 'PLAYWRIGHT_KEYBOARD', 'PLAYWRIGHT_TREE', 'PLAYWRIGHT_VISUAL'],
  ),
  entry(
    'viewer-grounding-draft-readiness',
    'P06 real echo grounding blocks unmounted drafts before approval and separate targets',
    [
      'grounding-episodes',
      'target-episodes',
      'evaluate-grounding',
      'approve-configuration',
      'judge-targets',
    ],
    ['dirty', 'unmounted', 'reloaded', 'saved', 'approved', 'judged', 'reflow'],
    ['WCAG22-2.1.1', 'WCAG22-3.3.4', 'WCAG22-1.4.10'],
    ['HTTP_API', 'PLAYWRIGHT_KEYBOARD', 'PLAYWRIGHT_TREE', 'PLAYWRIGHT_VISUAL'],
  ),
  entry(
    'viewer-grounding-save-conflict',
    'P06 pending save and real label conflict block grounding until acknowledged save',
    ['save', 'saved-data-conflict', 'evaluate-grounding', 'refresh-jobs'],
    ['saving', 'conflict', 'reconciled', 'saved', 'snapshot-error', 'retried'],
    ['WCAG22-2.1.1', 'WCAG22-3.3.4', 'WCAG22-4.1.2'],
    ['HTTP_API', 'PLAYWRIGHT_KEYBOARD', 'PLAYWRIGHT_TREE'],
  ),
  ...[401, 403, 404].map((status) =>
    entry(
      `viewer-cached-episode-denied-${status}`,
      `P06 cached episode ${status} hides the editor and preserves its draft for retry`,
      ['trajectory-labels', 'save', 'retry-episode-load'],
      ['dirty', 'denied', 'editor-hidden', 'retried'],
      ['WCAG22-2.1.1', 'WCAG22-3.3.4', 'WCAG22-4.1.3'],
      ['PLAYWRIGHT_KEYBOARD', 'PLAYWRIGHT_TREE', 'PLAYWRIGHT_LIVE_REGION'],
    ),
  ),
  entry(
    'viewer-principal-draft-isolation',
    'P06 principal changes isolate persisted label drafts',
    ['trajectory-labels', 'workspace-save-status'],
    ['dirty', 'principal-changed', 'principal-restored', 'recovered'],
    ['WCAG22-3.3.4', 'WCAG22-4.1.2'],
    ['PLAYWRIGHT_TREE'],
  ),
  entry(
    'viewer-source-draft-reconciliation',
    'P06 changed source requires explicit reconciliation of a recovered draft',
    ['trajectory-labels', 'saved-data-conflict', 'prefer-server', 'prefer-local'],
    ['dirty', 'source-changed', 'conflict'],
    ['WCAG22-3.3.4', 'WCAG22-4.1.2'],
    ['PLAYWRIGHT_TREE'],
  ),
  ...['annotation', 'episode-edit'].map((resource) =>
    entry(
      `viewer-unmounted-${resource}-readiness`,
      `P06 unmounted ${resource} draft blocks grounding across reload`,
      [
        'episode-editor',
        'playback-frame',
        'evaluate-grounding',
        'judge-targets',
        'discard-changes',
      ],
      ['dirty', 'refresh-error', 'retried', 'unmounted', 'reloaded', 'discarded', 'judged'],
      ['WCAG22-2.1.1', 'WCAG22-3.3.4', 'WCAG22-4.1.3'],
      ['HTTP_API', 'PLAYWRIGHT_KEYBOARD', 'PLAYWRIGHT_TREE', 'PLAYWRIGHT_LIVE_REGION'],
    ),
  ),
  entry(
    'viewer-workspace-disclosure',
    'dataset workspace disclosure excludes hidden editors and returns keyboard focus without autoplay',
    ['dataset-workspace', 'return-to-episode', 'playback'],
    ['expanded', 'collapsed', 'unavailable'],
    ['WCAG22-2.1.1', 'WCAG22-2.4.3', 'WCAG22-4.1.2'],
    ['PLAYWRIGHT_KEYBOARD', 'PLAYWRIGHT_TREE'],
  ),
  entry(
    'viewer-joint-defaults-conflict',
    'V07 joint defaults conflict retains the draft and keyboard focus with an alert',
    ['joint-defaults-dialog'],
    ['dirty', 'conflict'],
    ['WCAG22-2.1.1', 'WCAG22-2.4.3', 'WCAG22-4.1.3'],
    ['PLAYWRIGHT_KEYBOARD', 'PLAYWRIGHT_LIVE_REGION', 'PLAYWRIGHT_TREE'],
  ),
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
    'viewer-trajectory-adjustment-keyboard',
    'V13 trajectory adjustments preserve keyboard focus and announce changes',
    ['trajectory-adjustments'],
    ['delta', 'set-value', 'reset', 'applied', 'reset-frame', 'cleared', 'adaptive', 'inserted'],
    ['WCAG22-2.1.1', 'WCAG22-2.4.3', 'WCAG22-4.1.2', 'WCAG22-4.1.3', 'WCAG22-1.4.10'],
    ['PLAYWRIGHT_KEYBOARD', 'PLAYWRIGHT_TREE', 'PLAYWRIGHT_LIVE_REGION', 'PLAYWRIGHT_VISUAL'],
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
