// @ts-check
const { themes: prismThemes } = require('prism-react-renderer');

/** @type {import('@docusaurus/types').Config} */
const config = {
  title: 'Azure NVIDIA Robotics Reference Architecture',
  tagline: 'End-to-end robotics simulation, training, and deployment on Azure',
  favicon: 'img/microsoft-logo.svg',

  url: 'https://azure-samples.github.io',
  baseUrl: '/azure-nvidia-robotics-reference-architecture/',

  organizationName: 'Azure-Samples',
  projectName: 'azure-nvidia-robotics-reference-architecture',
  trailingSlash: false,

  onBrokenLinks: 'warn',
  onBrokenMarkdownLinks: 'warn',

  markdown: {
    format: 'detect',
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
            'https://github.com/Azure-Samples/azure-nvidia-robotics-reference-architecture/tree/main/docs/docusaurus/',
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
        title: 'Azure NVIDIA Robotics',
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
            href: 'https://github.com/Azure-Samples/azure-nvidia-robotics-reference-architecture',
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
                href: 'https://github.com/Azure-Samples/azure-nvidia-robotics-reference-architecture/issues',
              },
            ],
          },
          {
            title: 'More',
            items: [
              {
                label: 'GitHub',
                href: 'https://github.com/Azure-Samples/azure-nvidia-robotics-reference-architecture',
              },
              {
                label: 'Azure Samples',
                href: 'https://github.com/Azure-Samples',
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
