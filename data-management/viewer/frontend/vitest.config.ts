import react from '@vitejs/plugin-react'
import path from 'path'
import { defineConfig } from 'vitest/config'

// Coverage floors for files that predate enforced per-file thresholds; remove once raised.
const coverageBaselines = {
  'src/components/__tests__/support/annotationWorkspaceTestSupport.tsx': {
    statements: 85,
    branches: 62,
    functions: 80,
    lines: 85,
  },
  'src/components/annotation-panel/DataQualityWidget.tsx': {
    statements: 75,
    branches: 62,
    functions: 63,
    lines: 72,
  },
  'src/components/annotation-panel/LabelPanel.tsx': {
    statements: 75,
    branches: 62,
    functions: 73,
    lines: 77,
  },
  'src/components/annotation-panel/ObjectDetectionWidget.tsx': {
    statements: 66,
    branches: 70,
    functions: 74,
    lines: 66,
  },
  'src/components/annotation-panel/StarRating.tsx': {
    statements: 69,
    branches: 66,
    functions: 71,
    lines: 69,
  },
  'src/components/annotation-panel/TaskCompletenessWidget.tsx': {
    statements: 78,
    branches: 70,
    functions: 76,
    lines: 75,
  },
  'src/components/annotation-panel/TrajectoryQualityWidget.tsx': {
    statements: 85,
    branches: 64,
    functions: 80,
    lines: 85,
  },
  'src/components/annotation-workspace/AnnotationWorkspaceContent.tsx': {
    statements: 80,
    branches: 70,
    functions: 57,
    lines: 80,
  },
  'src/components/annotation-workspace/AnnotationWorkspaceDiagnosticsPanel.tsx': {
    statements: 85,
    branches: 66,
    functions: 80,
    lines: 85,
  },
  'src/components/annotation-workspace/AnnotationWorkspaceEmptyState.tsx': {
    statements: 0,
    branches: 70,
    functions: 0,
    lines: 0,
  },
  'src/components/annotation-workspace/AnnotationWorkspacePlaybackCard.tsx': {
    statements: 73,
    branches: 70,
    functions: 57,
    lines: 72,
  },
  'src/components/annotation-workspace/AnnotationWorkspaceSubtaskListCard.tsx': {
    statements: 85,
    branches: 60,
    functions: 80,
    lines: 85,
  },
  'src/components/annotation-workspace/AnnotationWorkspaceTrajectoryTab.tsx': {
    statements: 50,
    branches: 70,
    functions: 50,
    lines: 50,
  },
  'src/components/annotation-workspace/CollapsibleSection.tsx': {
    statements: 75,
    branches: 70,
    functions: 80,
    lines: 75,
  },
  'src/components/annotation-workspace/DraftConflictDialog.tsx': {
    statements: 76,
    branches: 50,
    functions: 80,
    lines: 72,
  },
  'src/components/annotation-workspace/useAnnotationWorkspaceDiagnostics.ts': {
    statements: 75,
    branches: 70,
    functions: 80,
    lines: 75,
  },
  'src/components/annotation-workspace/useAnnotationWorkspaceMediaController.ts': {
    statements: 85,
    branches: 50,
    functions: 80,
    lines: 85,
  },
  'src/components/annotation-workspace/useAnnotationWorkspaceMediaSources.ts': {
    statements: 51,
    branches: 56,
    functions: 73,
    lines: 50,
  },
  'src/components/annotation-workspace/useAnnotationWorkspaceShell.ts': {
    statements: 85,
    branches: 58,
    functions: 80,
    lines: 85,
  },
  'src/components/annotation-workspace/useAnnotationWorkspaceVideoSync.ts': {
    statements: 78,
    branches: 66,
    functions: 80,
    lines: 78,
  },
  'src/components/curriculum/ExportPanel.tsx': {
    statements: 82,
    branches: 70,
    functions: 75,
    lines: 80,
  },
  'src/components/episode-viewer/JointConfigDefaultsEditor.tsx': {
    statements: 83,
    branches: 70,
    functions: 76,
    lines: 83,
  },
  'src/components/episode-viewer/JointSelector.tsx': {
    statements: 68,
    branches: 67,
    functions: 78,
    lines: 69,
  },
  'src/components/episode-viewer/Timeline.tsx': {
    statements: 67,
    branches: 56,
    functions: 64,
    lines: 68,
  },
  'src/components/episode-viewer/TimelineMarker.tsx': {
    statements: 77,
    branches: 28,
    functions: 33,
    lines: 85,
  },
  'src/components/episode-viewer/TrajectoryPlot.tsx': {
    statements: 85,
    branches: 70,
    functions: 75,
    lines: 85,
  },
  'src/components/episode-viewer/TrajectoryPlotChart.tsx': {
    statements: 83,
    branches: 70,
    functions: 80,
    lines: 85,
  },
  'src/components/episode-viewer/TrajectoryPlotControls.tsx': {
    statements: 66,
    branches: 70,
    functions: 66,
    lines: 66,
  },
  'src/components/episode-viewer/VideoPlayer.tsx': {
    statements: 85,
    branches: 70,
    functions: 80,
    lines: 84,
  },
  'src/components/export/ExportProgress.tsx': {
    statements: 85,
    branches: 61,
    functions: 80,
    lines: 85,
  },
  'src/components/frame-editor/TransformControls.tsx': {
    statements: 85,
    branches: 70,
    functions: 76,
    lines: 85,
  },
  'src/hooks/use-vlm-judge.ts': { statements: 45, branches: 18, functions: 45, lines: 50 },
  'src/stores/annotation-store.ts': { statements: 77, branches: 54, functions: 75, lines: 85 },
}

// Per-file glob for a directory that skips files with coverage baselines, matched by file name.
const outsideBaselines = (dir: string) =>
  `${dir}/**/!(${Object.keys(coverageBaselines)
    .filter((file) => file.startsWith(`${dir}/`))
    .map((file) => path.basename(file))
    .join('|')})`

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  test: {
    // Run with `--no-file-parallelism` when invoking coverage locally to
    // avoid happy-dom timer/global races between concurrent test files.
    environment: 'happy-dom',
    globals: false,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    reporters: ['default', 'junit'],
    outputFile: {
      junit: '../../../logs/vitest-results.xml',
    },
    coverage: {
      provider: 'v8',
      reporter: ['text', 'lcov', 'cobertura', 'json-summary'],
      reportsDirectory: './coverage',
      include: ['src/**/*.{ts,tsx}'],
      exclude: [
        'src/**/*.test.{ts,tsx}',
        'src/**/*.spec.{ts,tsx}',
        'src/**/*.d.ts',
        'src/test/**',
        'src/vite-env.d.ts',
        'src/main.tsx',
        'src/components/**/index.ts',
        'src/components/ui/**',
      ],
      thresholds: {
        lines: 55,
        functions: 55,
        branches: 40,
        statements: 55,
        // Per-file enforcement on hand-tested directories to catch regressions
        // in individual files. Component-level coverage tracked separately.
        [outsideBaselines('src/hooks')]: {
          perFile: true,
          lines: 50,
          functions: 45,
          branches: 25,
          statements: 45,
        },
        [outsideBaselines('src/stores')]: {
          perFile: true,
          lines: 85,
          functions: 75,
          branches: 60,
          statements: 85,
        },
        [outsideBaselines('src/components')]: {
          perFile: true,
          lines: 85,
          functions: 80,
          branches: 70,
          statements: 85,
        },
        'src/components/export/ExportDialog.tsx': {
          perFile: true,
          statements: 70,
          branches: 55,
          functions: 65,
          lines: 70,
        },
        'src/components/app-shell/DataviewerEpisodeList.tsx': {
          perFile: true,
          statements: 70,
          branches: 55,
          functions: 65,
          lines: 70,
        },
        'src/components/app-shell/DataviewerEpisodeViewer.tsx': {
          perFile: true,
          statements: 80,
          branches: 70,
          functions: 75,
          lines: 80,
        },
        'src/components/annotation-workspace/AnnotationWorkspace.tsx': {
          perFile: true,
          statements: 80,
          branches: 70,
          functions: 75,
          lines: 80,
        },
        'src/api/ai-analysis.ts': {
          statements: 80,
          branches: 80,
          functions: 80,
          lines: 80,
        },
        'src/api/detection.ts': {
          statements: 80,
          branches: 80,
          functions: 80,
          lines: 80,
        },
        'src/api/export.ts': {
          statements: 80,
          branches: 80,
          functions: 80,
          lines: 80,
        },
        'src/lib/auth-config.ts': {
          statements: 80,
          branches: 80,
          functions: 80,
          lines: 80,
        },
        'src/lib/edit-draft-storage.ts': {
          statements: 80,
          branches: 80,
          functions: 80,
          lines: 80,
        },
        'src/lib/joint-significance.ts': {
          statements: 80,
          branches: 75,
          functions: 80,
          lines: 80,
        },
        'src/lib/offline-storage.ts': {
          statements: 80,
          branches: 75,
          functions: 80,
          lines: 80,
        },
        'src/lib/query-client.ts': {
          statements: 80,
          branches: 80,
          functions: 80,
          lines: 80,
        },
        'src/lib/sync-queue.ts': {
          statements: 80,
          branches: 80,
          functions: 80,
          lines: 80,
        },
        ...coverageBaselines,
      },
    },
  },
})
