import react from '@vitejs/plugin-react'
import path from 'path'
import { defineConfig } from 'vitest/config'

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
        'src/hooks/**': {
          perFile: true,
          lines: 50,
          functions: 45,
          branches: 25,
          statements: 45,
        },
        'src/stores/**': {
          perFile: true,
          lines: 85,
          functions: 75,
          branches: 60,
          statements: 85,
        },
        'src/components/**': {
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
      },
    },
  },
})
