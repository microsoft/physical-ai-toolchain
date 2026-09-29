import type { Page, TestInfo } from '@playwright/test'
import { expect } from '@playwright/test'

export const riskComponentRegistry = [
  {
    id: 'viewer-risk-dialogs',
    category: 'dialog',
    sourcePaths: [
      'annotation-panel/LabelPanel.tsx',
      'annotation-workspace/DraftConflictDialog.tsx',
      'episode-viewer/JointConfigDefaultsEditor.tsx',
      'export/ExportDialog.tsx',
    ],
    mappedEvidence: 'e2e/accessibility-keyboard.spec.ts',
  },
  {
    id: 'viewer-risk-popovers',
    category: 'popover',
    sourcePaths: [
      'app-shell/DataviewerShellHeader.tsx',
      'playback/SpeedControl.tsx',
      'subtask-timeline/SubtaskToolbar.tsx',
    ],
    mappedEvidence: 'e2e/accessibility-keyboard.spec.ts',
  },
  {
    id: 'viewer-risk-selects',
    category: 'select',
    sourcePaths: [
      'annotation-panel/AddAnomalyDialog.tsx',
      'annotation-panel/AddIssueDialog.tsx',
      'annotation-panel/LanguageInstructionWidget.tsx',
      'annotation-panel/ObjectDetectionWidget.tsx',
      'annotation-panel/TaskCompletenessWidget.tsx',
      'curriculum/ExportPanel.tsx',
      'curriculum/FilterBuilder.tsx',
      'vlm-judge/JudgePanel.tsx',
    ],
    mappedEvidence: 'e2e/accessibility-keyboard.spec.ts',
  },
  {
    id: 'viewer-risk-context-menus',
    category: 'context-menu',
    sourcePaths: ['episode-viewer/JointSelector.tsx'],
    mappedEvidence: 'e2e/accessibility-keyboard.spec.ts',
  },
  {
    id: 'viewer-risk-tabs',
    category: 'tabs',
    sourcePaths: [
      'annotation-workspace/AnnotationWorkspaceContent.tsx',
      'curriculum/CurriculumGenerator.tsx',
    ],
    mappedEvidence: 'e2e/accessibility-keyboard.spec.ts',
  },
  {
    id: 'viewer-risk-tooltips',
    category: 'tooltip',
    sourcePaths: ['ai-suggestions/AISuggestionBadge.tsx', 'frame-editor/TrajectoryEditor.tsx'],
    mappedEvidence: 'e2e/accessibility-adaptive.spec.ts',
  },
  {
    id: 'viewer-risk-sliders',
    category: 'slider',
    sourcePaths: [
      'annotation-panel/TaskCompletenessWidget.tsx',
      'annotation-workspace/AnnotationWorkspacePlaybackCard.tsx',
      'frame-editor/ColorAdjustmentControls.tsx',
      'frame-editor/TrajectoryEditor.tsx',
      'subtask-timeline/SubtaskSegmentSlider.tsx',
      'viewer-display/ViewerDisplayControls.tsx',
    ],
    mappedEvidence: 'e2e/accessibility-keyboard.spec.ts',
  },
  {
    id: 'viewer-risk-live-regions',
    category: 'live-region',
    sourcePaths: [
      'annotation-panel/ObjectDetectionWidget.tsx',
      'annotation-workspace/AnnotationWorkspacePlaybackCard.tsx',
      'annotation-workspace/AnnotationWorkspaceTopBar.tsx',
      'app-shell/DataviewerEpisodeViewer.tsx',
      'export/ExportProgress.tsx',
      'vlm-judge/JudgePanel.tsx',
    ],
    mappedEvidence: 'e2e/accessibility-states.spec.ts',
  },
  {
    id: 'viewer-risk-keyboard-shortcuts',
    category: 'keyboard-shortcut',
    sourcePaths: [
      'annotation-panel/TaskCompletenessWidget.tsx',
      'annotation-panel/TrajectoryQualityWidget.tsx',
      'annotation-workspace/useAnnotationWorkspaceShell.ts',
      'episode-viewer/VideoPlayer.tsx',
      'HelpOverlay.tsx',
    ],
    mappedEvidence: 'e2e/accessibility-keyboard.spec.ts',
  },
  {
    id: 'viewer-risk-pointer-drag',
    category: 'pointer-drag',
    sourcePaths: [
      'episode-viewer/JointSelector.tsx',
      'episode-viewer/TrajectoryPlotChart.tsx',
      'episode-viewer/TrajectoryPlotSelectionOverlay.tsx',
    ],
    mappedEvidence: 'e2e/accessibility-adaptive.spec.ts',
  },
].map((entry) => ({ ...entry, owner: 'Viewer team' }))

const datasets = [
  {
    id: 'a11y-synthetic',
    name: 'Accessibility synthetic dataset',
    total_episodes: 2,
    fps: 12,
    features: {},
    tasks: [{ task_index: 0, description: 'Move the geometric block' }],
  },
  {
    id: 'a11y-secondary',
    name: 'Secondary keyboard target',
    total_episodes: 2,
    fps: 12,
    features: {},
    tasks: [{ task_index: 0, description: 'Inspect the geometric block' }],
  },
]

const episodes = [
  { index: 0, id: 'episode-0', length: 12, task_index: 0, has_annotations: false },
  { index: 1, id: 'episode-1', length: 18, task_index: 0, has_annotations: true },
]

const trajectory = Array.from({ length: 12 }, (_, frame) => ({
  timestamp: frame / 12,
  frame,
  joint_positions: [frame / 12, frame / 24],
  joint_velocities: [1, 0.5],
  end_effector_pose: [0, 0, 0, 0, 0, 0],
  gripper_state: frame > 5 ? 1 : 0,
  variables: { 'observation.state[0]': frame / 12, 'action[0]': frame / 10 },
}))

function episodePayload(index: number, options: ApiFixtureOptions) {
  return {
    meta: { ...episodes[index], task: 'Move the geometric block' },
    video_urls: {},
    video_time_windows: {},
    cameras: [],
    trajectory_variables:
      options.trajectoryVariables === 'joints'
        ? []
        : [
            {
              key: 'observation.state[0]',
              label: 'shoulder_joint',
              source: 'observation.state',
              index: 0,
              kind: 'state',
            },
            {
              key: 'action[0]',
              label: 'target_joint',
              source: 'action',
              index: 0,
              kind: 'action',
            },
          ],
    trajectory_data: trajectory.map((point) => ({
      ...point,
      frame: Math.min(point.frame, episodes[index].length - 1),
    })),
  }
}

interface ApiFixtureOptions {
  catalog?: 'empty' | 'populated'
  episodeList?: 'error' | 'success'
  export?: 'failure' | 'success'
  trajectoryVariables?: 'joints' | 'named'
}

export async function installApiFixture(page: Page, options: ApiFixtureOptions = {}) {
  const activeDatasets = options.catalog === 'empty' ? [] : datasets
  const activeEpisodes = options.catalog === 'empty' ? [] : episodes
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    const method = request.method()
    const json = (body: unknown, status = 200) =>
      route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })

    if (path === '/api/csrf-token') return json({ csrf_token: 'synthetic-csrf' })
    if (path === '/api/auth/context') {
      return json({ scope_id: 'a11y-local-scope', auth_mode: 'local' })
    }
    if (path === '/api/datasets') return json(activeDatasets)
    if (/^\/api\/datasets\/[^/]+$/.test(path)) {
      const datasetId = path.split('/').at(-1)
      return json(activeDatasets.find((dataset) => dataset.id === datasetId) ?? activeDatasets[0])
    }
    if (path.endsWith('/capabilities')) {
      return json({
        hdf5_support: true,
        has_hdf5_files: true,
        lerobot_support: false,
        is_lerobot_dataset: false,
        episode_count: 2,
      })
    }
    if (/\/episodes$/.test(path)) {
      if (options.episodeList === 'error') {
        return json({ code: 'SYNTHETIC_FAILURE', message: 'Synthetic episode list failure' }, 500)
      }
      return json(activeEpisodes)
    }
    if (path.endsWith('/labels')) {
      if (method === 'PUT') return json({ episode_index: 0, labels: ['REVIEWED'] })
      return json({
        dataset_id: path.split('/')[3],
        available_labels: ['REVIEWED', 'NEEDS_ATTENTION'],
        episodes: { '0': [], '1': ['REVIEWED'] },
        analysis: {},
      })
    }
    if (path.endsWith('/joint-config') || path === '/api/joint-config/defaults') {
      return json({
        dataset_id: path.split('/')[3] ?? 'a11y-synthetic',
        labels: { '0': 'shoulder_joint', '1': 'target_joint' },
        groups: [{ id: 'arm', label: 'Arm', indices: [0, 1] }],
      })
    }
    if (path.endsWith('/annotations')) {
      return json({
        schema_version: '1.0.0',
        episode_index: Number(path.split('/')[5]),
        dataset_id: path.split('/')[3],
        annotations: [],
      })
    }
    if (path.endsWith('/judge')) {
      return json({
        enabled: true,
        cached: false,
        judge_model: 'echo',
        prompt_version: 'a11y-v1',
        cache_key: null,
        backend: 'echo',
        process_method: 'gvl',
        process_methods: ['gvl', 'chronological'],
        n_frames: 8,
        result: null,
      })
    }
    if (path === '/api/ai/trajectory-analysis') {
      return json({
        smoothness: 0.9,
        normalized_smoothness: 0.9,
        efficiency: 0.85,
        jitter: 0.05,
        hesitation_count: 0,
        correction_count: 0,
        overall_score: 0.88,
        flags: [],
      })
    }
    if (path.endsWith('/export/stream')) {
      if (options.export === 'failure') {
        return route.fulfill({ status: 500, body: 'Synthetic export failure' })
      }
      return route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: [
          'event: progress',
          'data: {"percentage":50,"status":"Exporting episode","currentEpisode":1,"totalEpisodes":1,"currentFrame":6,"totalFrames":12}',
          '',
          'event: complete',
          'data: {"success":true,"outputFiles":["synthetic.hdf5"],"stats":{"totalEpisodes":1}}',
          '',
        ].join('\n'),
      })
    }
    const episodeMatch = path.match(/\/episodes\/(\d+)$/)
    if (episodeMatch) return json(episodePayload(Number(episodeMatch[1]), options))
    if (path.endsWith('/cache/warm')) return json({ status: 'ready' })
    if (path.endsWith('/cache/stats')) {
      return json({
        capacity: 2,
        size: 2,
        hits: 1,
        misses: 0,
        hit_rate: 1,
        total_bytes: 0,
        max_memory_bytes: 0,
      })
    }
    return json({ code: 'NOT_FOUND', message: `No synthetic fixture for ${path}` }, 404)
  })
}

export async function attachJson(testInfo: TestInfo, name: string, value: unknown) {
  await testInfo.attach(name, {
    body: Buffer.from(JSON.stringify(value, null, 2)),
    contentType: 'application/json',
  })
}

export async function openViewer(page: Page, testInfo: TestInfo, options: ApiFixtureOptions = {}) {
  const consoleErrors: string[] = []
  const pageErrors: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error') {
      const source = message.location().url
      consoleErrors.push(source ? `${message.text()} (${source})` : message.text())
    }
  })
  page.on('pageerror', (error) => pageErrors.push(error.message))
  await installApiFixture(page, options)
  await page.goto('/')
  await expect(page.getByRole('banner')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Episode 0' })).toBeVisible()
  await attachJson(testInfo, 'state-proof', {
    journeyIds: ['V02', 'V03', 'V05'],
    route: '/',
    fixture: 'a11y-synthetic',
    expected: 'Viewer shell and episode 0 workspace are rendered',
    observed: 'Banner and Episode 0 heading are visible',
  })
  return { consoleErrors, pageErrors }
}

export async function openEmptyViewer(page: Page, testInfo: TestInfo) {
  const consoleErrors: string[] = []
  const pageErrors: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error') {
      const source = message.location().url
      consoleErrors.push(source ? `${message.text()} (${source})` : message.text())
    }
  })
  page.on('pageerror', (error) => pageErrors.push(error.message))
  await installApiFixture(page, { catalog: 'empty' })
  await page.goto('/')
  await expect(page.getByRole('banner')).toBeVisible()
  await expect(page.getByText('No episode data', { exact: true })).toBeVisible()
  await attachJson(testInfo, 'state-proof', {
    journeyIds: ['V02', 'V03', 'V04'],
    route: '/',
    fixture: 'a11y-empty',
    expected: 'Viewer shell renders an empty catalog without an episode workspace',
    observed: 'Banner and No episode data status are visible',
  })
  return { consoleErrors, pageErrors }
}
