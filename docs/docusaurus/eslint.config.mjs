// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

import tsParser from '@typescript-eslint/parser';
import jsxA11yX from 'eslint-plugin-jsx-a11y-x';

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
      'jsx-a11y-x': jsxA11yX,
    },
    rules: {
      ...jsxA11yX.configs.recommended.rules,
      'jsx-a11y-x/no-noninteractive-tabindex': ['error', { roles: ['group'] }],
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