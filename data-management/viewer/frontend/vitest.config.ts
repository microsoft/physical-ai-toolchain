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
      },
    },
  },
})
