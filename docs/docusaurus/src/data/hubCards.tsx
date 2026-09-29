// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

import React from 'react';
import { RocketIcon, BookIcon, CodeIcon, CloudIcon, CpuIcon, ShieldIcon } from '../components/Icons';
import type { IconCardProps } from '../components/IconCard';
import type { BoxCardProps } from '../components/BoxCard';
import labelData from './labelRegistry.cjs';

const { labelRegistry } = labelData;

export const iconCards: IconCardProps[] = [
  {
    icon: <RocketIcon />,
    supertitle: labelRegistry.quickstart,
    title: labelRegistry.gettingStarted,
    href: '/getting-started/',
    description: 'Set up your environment and deploy the reference architecture end-to-end.',
  },
  {
    icon: <CloudIcon />,
    supertitle: labelRegistry.infrastructure,
    title: labelRegistry.deployInfrastructure,
    href: '/infrastructure/',
    description: 'Provision AKS clusters, networking, storage, and identity with Terraform.',
  },
  {
    icon: <CpuIcon />,
    supertitle: labelRegistry.simulation,
    title: labelRegistry.training,
    href: '/training/',
    description: 'Run reinforcement learning and imitation learning jobs on GPU clusters.',
  },
  {
    icon: <CodeIcon />,
    supertitle: labelRegistry.models,
    title: labelRegistry.evaluation,
    href: '/evaluation/',
    description: 'Validate trained models in simulation and on hardware.',
  },
  {
    icon: <BookIcon />,
    supertitle: labelRegistry.devices,
    title: labelRegistry.fleetDeployment,
    href: '/fleet-deployment/',
    description: 'Deploy trained models to robot fleets via FluxCD GitOps pipelines.',
  },
  {
    icon: <ShieldIcon />,
    supertitle: labelRegistry.platform,
    title: labelRegistry.operations,
    href: '/operations/',
    description: 'Monitor, scale, and manage the robotics platform in production.',
  },
];

export const boxCards: BoxCardProps[] = [
  {
    title: labelRegistry.architectureGuide,
    links: [
      { label: 'System architecture', href: '/contributing/architecture' },
      { label: 'Network topology', href: '/infrastructure/infrastructure-reference' },
      { label: 'Lifecycle domains', href: '/contributing/architecture#lifecycle-domains' },
    ],
    icon: '/img/icons/clipboard-task.svg',
  },
  {
    title: labelRegistry.gpuConfiguration,
    links: [
      { label: 'H100 setup', href: '/reference/gpu-configuration#h100-nodes' },
      { label: 'RTX PRO 6000 setup', href: '/reference/gpu-configuration#rtx-pro-6000-nodes' },
      { label: 'GPU Operator', href: '/reference/gpu-configuration#gpu-driver-management' },
    ],
    icon: '/img/icons/developer-board.svg',
  },
  {
    title: labelRegistry.security,
    links: [
      { label: 'Security review checklist', href: '/contributing/security-review' },
      { label: 'Private cluster access', href: '/infrastructure/vpn' },
      { label: 'Identity and credentials', href: '/infrastructure/infrastructure-deployment#osmo-workload-identity' },
    ],
    icon: '/img/icons/shield-lock.svg',
  },
  {
    title: labelRegistry.contributing,
    links: [
      { label: 'Contribution workflow', href: '/contributing/contribution-workflow' },
      { label: 'Infrastructure style guide', href: '/contributing/infrastructure-style' },
      { label: 'Roadmap', href: '/contributing/ROADMAP' },
    ],
    icon: '/img/icons/rocket-launch.svg',
  },
];
