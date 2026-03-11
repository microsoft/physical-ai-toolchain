import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

export function AnnotationWorkspaceEmptyState() {
  return (
    <div className="flex h-full items-center justify-center">
      <Card className="max-w-md">
        <CardHeader>
          <CardTitle>No Episode Selected</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-muted-foreground">
            Select a dataset and episode from the sidebar to begin annotation.
          </p>
        </CardContent>
      </Card>
    </div>
  )
}
