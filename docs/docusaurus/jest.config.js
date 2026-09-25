module.exports = {
  testEnvironment: 'jsdom',
  testMatch: ['<rootDir>/__tests__/**/*.test.ts?(x)'],
  setupFilesAfterEnv: ['<rootDir>/__tests__/setup.ts'],
  collectCoverageFrom: [
    'src/components/**/*.{ts,tsx}',
    'src/data/hubCards.tsx',
    'src/pages/**/*.tsx',
    'src/theme/**/*.{js,ts,tsx}',
  ],
  coverageDirectory: 'coverage',
  coverageReporters: ['text', 'lcov'],
  coverageThreshold: {
    global: {
      branches: 80,
      functions: 80,
      lines: 80,
      statements: 80,
    },
  },
  transform: {
    '^.+\.[jt]sx?$': [
      'ts-jest',
      {
        tsconfig: {
          allowJs: true,
          jsx: 'react-jsx',
          esModuleInterop: true,
          types: ['jest', '@testing-library/jest-dom'],
        },
        diagnostics: false,
      },
    ],
  },
  moduleNameMapper: {
    '\\.module\\.css$': 'identity-obj-proxy',
    '\\.css$': 'identity-obj-proxy',
    '\\.svg$': '<rootDir>/__tests__/__mocks__/fileMock.js',
    '^@docusaurus/Link$': '<rootDir>/__tests__/__mocks__/@docusaurus/Link',
    '^@docusaurus/useBaseUrl$':
      '<rootDir>/__tests__/__mocks__/@docusaurus/useBaseUrl',
    '^@theme-original/MDXComponents$': '<rootDir>/__tests__/__mocks__/@theme/MDXComponents',
    '^@theme/(.*)$': '<rootDir>/__tests__/__mocks__/@theme/$1',
  },
  testPathIgnorePatterns: ['/node_modules/', '/build/', '/__mocks__/'],
  testTimeout: 15000,
};
