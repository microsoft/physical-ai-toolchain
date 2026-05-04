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
    environment: 'happy-dom',
    globals: true,
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
      ],
      thresholds: {
        lines: 45,
        functions: 35,
        branches: 35,
        statements: 45,
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
