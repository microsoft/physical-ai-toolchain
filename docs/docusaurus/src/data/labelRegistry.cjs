// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

const labelRegistry = Object.freeze({
  architectureGuide: 'Architecture Guide',
  contributing: 'Contributing',
  dataPipeline: 'Data Pipeline',
  deployInfrastructure: 'Deploy Infrastructure',
  design: 'Design',
  devices: 'Devices',
  documentation: 'Documentation',
  edge: 'Edge Deployment',
  evaluation: 'Evaluation',
  fleetDeployment: 'Fleet Deployment',
  fleetIntelligence: 'Fleet Intelligence',
  gettingStarted: 'Getting Started',
  github: 'GitHub',
  githubIssues: 'GitHub Issues',
  gpuConfiguration: 'GPU Configuration',
  infrastructure: 'Infrastructure',
  lifecycle: 'Lifecycle',
  models: 'Models',
  operations: 'Operations',
  platform: 'Platform',
  quickstart: 'Quickstart',
  recipes: 'Recipes',
  reference: 'Reference',
  referenceAndGovernance: 'Reference and Governance',
  security: 'Security',
  simulation: 'Simulation',
  syntheticData: 'Synthetic Data',
  tier0: 'T0 - Dev (default)',
  tier1: 'T1 - Lab',
  tier2: 'T2 - Pilot (recommended)',
  tier3: 'T3 - Production (advanced)',
  tier4: 'T4 - Scale (advanced)',
  tier5: 'T5 - Operate (roadmap)',
  tiers: 'Tiers',
  training: 'Training',
});

const tierNavigation = Object.freeze([
  {
    label: labelRegistry.tier0,
    docId: 'recipes/tier-0-dev/README',
    dirName: 'recipes/tier-0-dev',
    route: '/recipes/tier-0-dev/',
  },
  {
    label: labelRegistry.tier1,
    docId: 'recipes/tier-1-lab/README',
    dirName: 'recipes/tier-1-lab',
    route: '/recipes/tier-1-lab/',
  },
  {
    label: labelRegistry.tier2,
    docId: 'recipes/tier-2-pilot/README',
    dirName: 'recipes/tier-2-pilot',
    route: '/recipes/tier-2-pilot/',
  },
  {
    label: labelRegistry.tier3,
    docId: 'recipes/tier-3-production/README',
    dirName: 'recipes/tier-3-production',
    route: '/recipes/tier-3-production/',
  },
  {
    label: labelRegistry.tier4,
    docId: 'recipes/tier-4-scale/README',
    dirName: 'recipes/tier-4-scale',
    route: '/recipes/tier-4-scale/',
  },
  {
    label: labelRegistry.tier5,
    docId: 'recipes/tier-5-operate/README',
    dirName: 'recipes/tier-5-operate',
    route: '/recipes/tier-5-operate/',
  },
]);

const sharedNavigation = Object.freeze([
  {
    label: labelRegistry.lifecycle,
    items: [
      { label: labelRegistry.gettingStarted, docId: 'getting-started/README', dirName: 'getting-started' },
      { label: labelRegistry.dataPipeline, docId: 'data-pipeline/README', dirName: 'data-pipeline' },
      { label: labelRegistry.syntheticData, docId: 'synthetic-data/README' },
      { label: labelRegistry.training, docId: 'training/README', dirName: 'training' },
      { label: labelRegistry.evaluation, docId: 'evaluation/README', dirName: 'evaluation' },
      { label: labelRegistry.infrastructure, docId: 'infrastructure/README', dirName: 'infrastructure' },
      { label: labelRegistry.edge, docId: 'edge/README' },
      { label: labelRegistry.fleetDeployment, docId: 'fleet-deployment/README' },
      { label: labelRegistry.fleetIntelligence, docId: 'fleet-intelligence/README' },
      { label: labelRegistry.operations, docId: 'operations/README', dirName: 'operations' },
    ],
  },
  {
    label: labelRegistry.recipes,
    docId: 'recipes/README',
    items: [
      {
        label: labelRegistry.dataPipeline,
        docId: 'recipes/data-collection/README',
        dirName: 'recipes/data-collection',
      },
      { label: labelRegistry.training, docId: 'recipes/training/README', dirName: 'recipes/training' },
    ],
  },
  {
    label: labelRegistry.referenceAndGovernance,
    items: [
      { label: labelRegistry.design, dirName: 'design' },
      { label: labelRegistry.reference, docId: 'reference/README', dirName: 'reference' },
      { label: labelRegistry.security, docId: 'security/README', dirName: 'security' },
      { label: labelRegistry.contributing, docId: 'contributing/README', dirName: 'contributing' },
      { type: 'doc', id: 'cloud/blob-storage-structure' },
      { type: 'doc', id: 'osmo-proxy' },
      { type: 'doc', id: 'deprecation-policy' },
    ],
  },
]);

module.exports = { labelRegistry, sharedNavigation, tierNavigation };