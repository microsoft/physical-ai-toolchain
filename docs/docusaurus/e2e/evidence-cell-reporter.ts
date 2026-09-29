import fs from 'node:fs';
import path from 'node:path';

import type { FullConfig, Reporter, TestCase, TestResult } from '@playwright/test/reporter';

type EvidenceMethod =
  | 'AXE'
  | 'PLAYWRIGHT_KEYBOARD'
  | 'PLAYWRIGHT_POINTER'
  | 'PLAYWRIGHT_TREE'
  | 'PLAYWRIGHT_LIVE_REGION'
  | 'PLAYWRIGHT_VISUAL';

interface EvidenceDeclaration {
  journeyId: string;
  method: EvidenceMethod;
  probe: 'project-playwright';
  testId: string;
}

interface RequirementLedger {
  requirements: Array<{
    requirement_id: string;
    journey_ids: string[];
    methods: Array<{ method: string }>;
  }>;
}

interface AssetJourneyLedger {
  journeys: Array<{ journey_id: string; required_methods: string[] }>;
}

interface EvidenceCell extends EvidenceDeclaration {
  artifactIds: string[];
  project: string;
  requirementId: string;
  status: 'PASS' | 'FAIL' | 'CANT_TELL';
}

const ANNOTATION_TYPE = 'a11y-cell';

export function evidence(testId: string, cells: Array<Omit<EvidenceDeclaration, 'testId' | 'probe'>>) {
  return {
    annotation: cells.map((cell) => ({
      type: ANNOTATION_TYPE,
      description: JSON.stringify({ ...cell, testId, probe: 'project-playwright' }),
    })),
  };
}

function resultStatus(status: TestResult['status']): EvidenceCell['status'] {
  if (status === 'passed') return 'PASS';
  if (status === 'skipped' || status === 'interrupted') return 'CANT_TELL';
  return 'FAIL';
}

export default class EvidenceCellReporter implements Reporter {
  private outputDirectory = path.resolve(process.cwd(), process.env.DOCS_E2E_OUTPUT_DIR ?? 'test-results');
  private readonly cells = new Map<string, EvidenceCell>();
  private requirementLedger!: RequirementLedger;
  private requiredMethodsByJourney = new Map<string, Set<string>>();

  onBegin(config: FullConfig): void {
    this.outputDirectory = path.resolve(config.rootDir, '..', process.env.DOCS_E2E_OUTPUT_DIR ?? 'test-results');
    const ledgerPath = path.resolve(
      config.rootDir,
      '..',
      '..',
      '..',
      '.github',
      'accessibility',
      'requirement-evidence.json',
    );
    this.requirementLedger = JSON.parse(fs.readFileSync(ledgerPath, 'utf8')) as RequirementLedger;
    const assetLedgerPath = path.resolve(
      config.rootDir,
      '..',
      '..',
      '..',
      '.github',
      'accessibility',
      'asset-journeys.json',
    );
    const assetLedger = JSON.parse(fs.readFileSync(assetLedgerPath, 'utf8')) as AssetJourneyLedger;
    this.requiredMethodsByJourney = new Map(
      assetLedger.journeys.map((journey) => [journey.journey_id, new Set(journey.required_methods)]),
    );
  }

  onTestEnd(testCase: TestCase, result: TestResult): void {
    const project = testCase.parent.project()?.name ?? 'unknown-project';
    for (const annotation of testCase.annotations) {
      if (annotation.type !== ANNOTATION_TYPE || !annotation.description) continue;
      const declaration = JSON.parse(annotation.description) as EvidenceDeclaration;
      if (!this.requiredMethodsByJourney.get(declaration.journeyId)?.has(declaration.method)) {
        continue;
      }
      for (const requirement of this.requirementLedger.requirements) {
        if (
          !requirement.journey_ids.includes(declaration.journeyId) ||
          !requirement.methods.some((assignment) => assignment.method === declaration.method)
        ) {
          continue;
        }
        const requirementId = `${requirement.requirement_id}:${declaration.journeyId}:${declaration.method}`;
        const key = [project, requirementId, declaration.testId].join(':');
        this.cells.set(key, {
          ...declaration,
          project,
          requirementId,
          status: resultStatus(result.status),
          artifactIds: ['docusaurus-playwright'],
        });
      }
    }
  }

  onEnd(): void {
    fs.mkdirSync(this.outputDirectory, { recursive: true });
    fs.writeFileSync(
      path.join(this.outputDirectory, 'evidence-method-results.json'),
      `${JSON.stringify(
        {
          schemaVersion: '1.0.0',
          runId: process.env.DOCS_E2E_RUN_ID ?? 'local-playwright-run',
          cells: [...this.cells.values()].sort((left, right) =>
            [left.requirementId, left.testId].join(':').localeCompare([right.requirementId, right.testId].join(':')),
          ),
        },
        null,
        2,
      )}\n`,
    );
  }
}