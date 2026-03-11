/**
 * Unsaved changes confirmation dialog.
 */

import { AlertTriangle } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

interface UnsavedChangesDialogProps {
  /** Whether the dialog is visible */
  open: boolean;
  /** Callback to confirm (discard changes) */
  onConfirm: () => void;
  /** Callback to cancel */
  onCancel: () => void;
  /** Callback to save and continue */
  onSave?: () => void;
}

/**
 * Modal dialog warning about unsaved changes.
 *
 * @example
 * ```tsx
 * <UnsavedChangesDialog
 *   open={showUnsavedDialog}
 *   onConfirm={confirmNavigation}
 *   onCancel={cancelNavigation}
 *   onSave={async () => { await save(); confirmNavigation(); }}
 * />
 * ```
 */
export function UnsavedChangesDialog({
  open,
  onConfirm,
  onCancel,
  onSave,
}: UnsavedChangesDialogProps) {
  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <Card className="w-full max-w-md">
        <CardHeader className="pb-4">
          <CardTitle className="flex items-center gap-2 text-orange-600">
            <AlertTriangle className="h-5 w-5" />
            Unsaved Changes
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-muted-foreground">
            You have unsaved changes to the current annotation. What would you like to do?
          </p>

          <div className="flex flex-col gap-2">
            {onSave && (
              <Button onClick={onSave} className="w-full">
                Save and Continue
              </Button>
            )}
            <Button
              variant="destructive"
              onClick={onConfirm}
              className="w-full"
            >
              Discard Changes
            </Button>
            <Button
              variant="outline"
              onClick={onCancel}
              className="w-full"
            >
              Go Back
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
