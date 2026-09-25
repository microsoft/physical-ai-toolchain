// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

import tsParser from '@typescript-eslint/parser';
import jsxA11y from 'eslint-plugin-jsx-a11y';

export default [
  {
    ignores: ['.docusaurus/**', 'build/**', 'coverage/**', 'node_modules/**', 'test-results/**'],
  },
  {
    files: [
      'src/**/*.{js,jsx,ts,tsx}',
      '__tests__/**/*.{js,jsx,ts,tsx}',
    ],
    languageOptions: {
      parser: tsParser,
      parserOptions: {
        ecmaFeatures: { jsx: true },
        ecmaVersion: 'latest',
        sourceType: 'module',
      },
    },
    plugins: {
      'jsx-a11y': jsxA11y,
    },
    rules: {
      ...jsxA11y.flatConfigs.recommended.rules,
      'jsx-a11y/no-noninteractive-tabindex': ['error', { roles: ['group'] }],
    },
  },
  {
    files: [
      '*.{js,mjs,ts}',
      'e2e/**/*.{js,mjs,ts}',
      'plugins/**/*.{js,mjs,ts}',
      'scripts/**/*.{js,mjs,ts}',
    ],
    languageOptions: {
      parser: tsParser,
      parserOptions: {
        ecmaVersion: 'latest',
        sourceType: 'module',
      },
    },
  },
];