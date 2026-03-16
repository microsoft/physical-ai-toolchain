// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

import React from 'react';
import { RocketIcon, BookIcon, CodeIcon, CloudIcon, CpuIcon, ShieldIcon } from '../components/Icons';
import type { IconCardProps } from '../components/IconCard';
import type { BoxCardProps } from '../components/BoxCard';

export const iconCards: IconCardProps[] = [
  {
    icon: <RocketIcon />,
    supertitle: 'Quickstart',
    title: 'Getting Started',
    href: '/getting-started/',
    description: 'Set up your environment and deploy the reference architecture end-to-end.',
  },
  {
    icon: <CloudIcon />,
    supertitle: 'Infrastructure',
    title: 'Deploy Infrastructure',
    href: '/deploy/',
    description: 'Provision AKS clusters, networking, storage, and identity with Terraform.',
  },
  {
    icon: <CpuIcon />,
    supertitle: 'Simulation',
    title: 'Training',
    href: '/training/',
    description: 'Run reinforcement learning and imitation learning jobs on GPU clusters.',
  },
  {
    icon: <CodeIcon />,
    supertitle: 'Models',
    title: 'Inference',
    href: '/inference/',
    description: 'Deploy trained models for real-time robot control and evaluation.',
  },
  {
    icon: <BookIcon />,
    supertitle: 'Devices',
    title: 'Edge Deployment',
    href: '/edge/',
    description: 'Push models to edge devices for on-premises robot operation.',
  },
  {
    icon: <ShieldIcon />,
    supertitle: 'Platform',
    title: 'Operations',
    href: '/operations/',
    description: 'Monitor, scale, and manage the robotics platform in production.',
  },
];

export const boxCards: BoxCardProps[] = [
  {
    title: 'Architecture Guide',
    links: [
      { label: 'System architecture', href: '/contributing/architecture' },
      { label: 'Network topology', href: '/deploy/infrastructure-reference' },
      { label: 'Lifecycle domains', href: '/contributing/architecture#domain-overview' },
    ],
    icon: '/img/icons/clipboard-task.svg',
  },
  {
    title: 'GPU Configuration',
    links: [
      { label: 'H100 setup', href: '/reference/gpu-configuration#h100-nodes' },
      { label: 'RTX PRO 6000 setup', href: '/reference/gpu-configuration#rtx-pro-6000-nodes' },
      { label: 'GPU Operator', href: '/reference/gpu-configuration#gpu-driver-management' },
    ],
    icon: '/img/icons/developer-board.svg',
  },
  {
    title: 'Security',
    links: [
      { label: 'Security review checklist', href: '/contributing/security-review' },
      { label: 'Private cluster access', href: '/deploy/vpn' },
      { label: 'Identity and credentials', href: '/deploy/infrastructure#osmo-workload-identity' },
    ],
    icon: '/img/icons/shield-lock.svg',
  },
  {
    title: 'Contributing',
    links: [
      { label: 'Contribution workflow', href: '/contributing/contribution-workflow' },
      { label: 'Infrastructure style guide', href: '/contributing/infrastructure-style' },
      { label: 'Roadmap', href: '/contributing/ROADMAP' },
    ],
    icon: '/img/icons/rocket-launch.svg',
  },
];
