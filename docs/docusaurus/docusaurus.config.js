// @ts-check
const { themes: prismThemes } = require('prism-react-renderer');

/** @type {import('@docusaurus/types').Config} */
const config = {
  title: 'Physical AI Toolchain',
  tagline: 'Production-ready framework for training, deploying, and operating physical AI solutions on Azure with NVIDIA Isaac',
  favicon: 'img/microsoft-logo.svg',

  url: 'https://microsoft.github.io',
  baseUrl: '/physical-ai-toolchain/',

  organizationName: 'microsoft',
  projectName: 'physical-ai-toolchain',

  onBrokenLinks: 'warn',

  markdown: {
    format: 'detect',
    hooks: {
      onBrokenMarkdownLinks: 'warn',
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
          editUrl:
            'https://github.com/microsoft/physical-ai-toolchain/tree/main/docs/docusaurus/',
          exclude: ['docusaurus/**', 'images/**'],
          showLastUpdateTime: true,
        },
        blog: false,
        theme: {
          customCss: './src/css/custom.css',
        },
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
            label: 'Documentation',
          },
          {
            href: '/physical-ai-toolchain/book/',
            label: '📖 Book',
            position: 'left',
          },
          {
            href: 'https://github.com/microsoft/physical-ai-toolchain',
            label: 'GitHub',
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
                label: 'Getting Started',
                to: '/getting-started/',
              },
              {
                label: 'Deploy',
                to: '/deploy/',
              },
              {
                label: 'Training',
                to: '/training/',
              },
            ],
          },
          {
            title: 'Community',
            items: [
              {
                label: 'Contributing',
                to: '/contributing/',
              },
              {
                label: 'GitHub Issues',
                href: 'https://github.com/microsoft/physical-ai-toolchain/issues',
              },
            ],
          },
          {
            title: 'More',
            items: [
              {
                label: '📖 Book',
                href: '/physical-ai-toolchain/book/',
              },
              {
                label: 'GitHub',
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
    }),
};

module.exports = config;
