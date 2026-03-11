module.exports = {
  testEnvironment: 'jsdom',
  transform: {
    '^.+\\.tsx?$': [
      'ts-jest',
      {
        tsconfig: {
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
    '^@theme/(.*)$': '<rootDir>/__tests__/__mocks__/@theme/$1',
  },
  testPathIgnorePatterns: ['/node_modules/', '/build/', '/__mocks__/'],
};
