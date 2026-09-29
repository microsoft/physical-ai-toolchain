// @ts-check
import { themes as prismThemes } from 'prism-react-renderer';
import remarkGithubAlert from 'remark-github-blockquote-alert';

import rehypeTableScope from './plugins/rehype-table-scope.mjs';
import remarkTableCaption from './plugins/remark-table-caption.mjs';
import labelData from './src/data/labelRegistry.cjs';

const { labelRegistry, tierNavigation } = labelData;

/** @type {import('@docusaurus/types').Config} */
const config = {
  title: 'Physical AI Toolchain',
  tagline: 'Production-ready framework for training, deploying, and operating physical AI solutions on Azure with NVIDIA Isaac',
  favicon: 'img/microsoft-logo.svg',

  future: {
    v4: true,
  },

  url: 'https://microsoft.github.io',
  baseUrl: '/physical-ai-toolchain/',

  organizationName: 'microsoft',
  projectName: 'physical-ai-toolchain',

  onBrokenLinks: 'throw',
  onDuplicateRoutes: 'throw',

  markdown: {
    format: 'detect',
    mermaid: true,
    hooks: {
      onBrokenMarkdownLinks: 'throw',
    },
  },

  i18n: {
    defaultLocale: 'en',
    locales: ['en'],
  },

  presets: [
    [
      'classic',
      /** @type {import('@docusaurus/preset-classic').Options} */
      ({
        docs: {
          path: '../',
          routeBasePath: '/',
          sidebarPath: './sidebars.js',
          editUrl: ({ docPath }) =>
            `https://github.com/microsoft/physical-ai-toolchain/edit/main/docs/${docPath}`,
          exclude: [
            'docusaurus/**',
            'images/**',
            'announcements/**',
            '**/_*.{js,jsx,ts,tsx,md,mdx}',
            '**/_*/**',
            '**/*.test.{md,mdx}',
            '**/*.spec.{md,mdx}',
            '**/__tests__/**',
          ],
          showLastUpdateTime: true,
          showLastUpdateAuthor: true,
          remarkPlugins: [remarkGithubAlert, remarkTableCaption],
          rehypePlugins: [rehypeTableScope],
        },
        blog: false,
        theme: {
          customCss: './src/css/custom.css',
        },
      }),
    ],
  ],

  themes: [
    '@docusaurus/theme-mermaid',
    [
      '@easyops-cn/docusaurus-search-local',
      /** @type {import('@easyops-cn/docusaurus-search-local').PluginOptions} */
      ({
        hashed: true,
        docsDir: '../',
        docsRouteBasePath: '/',
        indexBlog: false,
        language: ['en'],
        highlightSearchTermsOnTargetPage: false,
        explicitSearchResultPath: true,
      }),
    ],
  ],

  plugins: [
    'docusaurus-plugin-image-zoom',
    [
      '@docusaurus/plugin-client-redirects',
      {
        redirects: [],
      },
    ],
  ],

  themeConfig:
    /** @type {import('@docusaurus/preset-classic').ThemeConfig} */
    ({
      navbar: {
        title: 'Physical AI Toolchain',
        logo: {
          alt: 'Microsoft',
          src: 'img/microsoft-logo.svg',
        },
        items: [
          {
            type: 'docSidebar',
            sidebarId: 'docsSidebar',
            position: 'left',
            label: labelRegistry.documentation,
          },
          {
            type: 'dropdown',
            label: labelRegistry.tiers,
            position: 'left',
            items: tierNavigation.map(({ label, route }) => ({ label, to: route })),
          },
          {
            href: 'https://github.com/microsoft/physical-ai-toolchain',
            label: labelRegistry.github,
            position: 'right',
          },
        ],
      },
      footer: {
        style: 'dark',
        links: [
          {
            title: 'Docs',
            items: [
              {
                label: labelRegistry.gettingStarted,
                to: '/getting-started/',
              },
              {
                label: labelRegistry.infrastructure,
                to: '/infrastructure/',
              },
              {
                label: labelRegistry.training,
                to: '/training/',
              },
            ],
          },
          {
            title: 'Community',
            items: [
              {
                label: labelRegistry.contributing,
                to: '/contributing/',
              },
              {
                label: labelRegistry.githubIssues,
                href: 'https://github.com/microsoft/physical-ai-toolchain/issues',
              },
            ],
          },
          {
            title: 'More',
            items: [
              {
                label: labelRegistry.github,
                href: 'https://github.com/microsoft/physical-ai-toolchain',
              },
              {
                label: 'Microsoft',
                href: 'https://github.com/microsoft',
              },
            ],
          },
        ],
        copyright: `Copyright © ${new Date().getFullYear()} Microsoft Corporation.`,
      },
      prism: {
        theme: prismThemes.github,
        darkTheme: prismThemes.dracula,
        additionalLanguages: ['bash', 'json', 'yaml', 'hcl', 'python', 'powershell'],
      },
      colorMode: {
        defaultMode: 'light',
        respectPrefersColorScheme: true,
      },
      docs: {
        sidebar: {
          hideable: true,
          autoCollapseCategories: true,
        },
      },
      mermaid: {
        theme: { light: 'neutral', dark: 'dark' },
      },
    }),
};

export default config;
