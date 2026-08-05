import type { ReactNode } from 'react'

import { Card, CardContent } from '@/components/ui/card'
import { TabsContent } from '@/components/ui/tabs'

import { CollapsibleSection } from './CollapsibleSection'

interface AnnotationWorkspaceAnalyzerTabProps {
  motionMetricsPanel: ReactNode
  judgePanel: ReactNode
  analysisCard: ReactNode
  labelPanel: ReactNode
  languageInstructionPanel: ReactNode
}

export function AnnotationWorkspaceAnalyzerTab({
  motionMetricsPanel,
  judgePanel,
  analysisCard,
  labelPanel,
  languageInstructionPanel,
}: AnnotationWorkspaceAnalyzerTabProps) {
  return (
    <TabsContent value="analyzer" className="mt-2.5 min-h-0 flex-1">
      <div
        data-testid="analyzer-layout-grid"
        className="grid h-full min-h-0 grid-cols-1 gap-2 lg:grid-cols-2"
      >
        <div
          data-testid="analyzer-run-panel"
          className="bg-card order-1 space-y-3 overflow-y-auto rounded-xl border p-3 shadow-xs"
        >
          <div>
            <h3 className="text-sm font-semibold">Automated Episode Analysis</h3>
            <p className="text-muted-foreground text-xs">
              Score motion quality from the joint trajectory and judge task success with the VLM.
              Results are written to this episode's labels.
            </p>
          </div>
          {motionMetricsPanel}
          {judgePanel}
        </div>
        <Card
          data-testid="analyzer-results-panel"
          className="order-2 min-h-[280px] overflow-hidden lg:min-h-0"
        >
          <CardContent className="h-full overflow-y-auto p-4">
            <div className="space-y-6">
              <CollapsibleSection title="Episode Analysis">{analysisCard}</CollapsibleSection>
              <CollapsibleSection title="Episode Labels" className="border-t pt-6">
                {labelPanel}
              </CollapsibleSection>
              <CollapsibleSection title="Task Instruction" className="border-t pt-6">
                {languageInstructionPanel}
              </CollapsibleSection>
            </div>
          </CardContent>
        </Card>
      </div>
    </TabsContent>
  )
}
