/**
 * Dialog for adding a new data quality issue.
 */

import { X } from 'lucide-react';
import { useState } from 'react';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import type { DataQualityIssue, DataQualityIssueType, IssueSeverity } from '@/types';

import { FormSection } from './FormSection';

interface AddIssueDialogProps {
  /** Whether the dialog is open */
  open: boolean;
  /** Callback to close the dialog */
  onClose: () => void;
  /** Callback when issue is added */
  onAdd: (issue: DataQualityIssue) => void;
  /** Current frame for default frame range */
  currentFrame: number;
}

/**
 * Modal dialog for adding a new data quality issue.
 *
 * @example
 * ```tsx
 * <AddIssueDialog
 *   open={dialogOpen}
 *   onClose={() => setDialogOpen(false)}
 *   onAdd={handleAddIssue}
 *   currentFrame={42}
 * />
 * ```
 */
export function AddIssueDialog({
  open,
  onClose,
  onAdd,
  currentFrame,
}: AddIssueDialogProps) {
  const [type, setType] = useState<DataQualityIssueType>('frame-drop');
  const [severity, setSeverity] = useState<IssueSeverity>('minor');
  const [notes, setNotes] = useState('');
  const [frameStart, setFrameStart] = useState(currentFrame);
  const [frameEnd, setFrameEnd] = useState(currentFrame + 10);

  const issueTypes: DataQualityIssueType[] = [
    'frame-drop',
    'sync-issue',
    'occlusion',
    'lighting-issue',
    'sensor-noise',
    'calibration-drift',
    'encoding-artifact',
    'missing-data',
  ];

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onAdd({
      type,
      severity,
      notes: notes || undefined,
      affectedFrames: [frameStart, frameEnd],
    });
    // Reset form
    setType('frame-drop');
    setSeverity('minor');
    setNotes('');
    onClose();
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <Card className="w-full max-w-md">
        <CardHeader className="flex flex-row items-center justify-between pb-2">
          <CardTitle className="text-base">Add Data Quality Issue</CardTitle>
          <Button variant="ghost" size="icon" onClick={onClose}>
            <X className="h-4 w-4" />
          </Button>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            {/* Issue type */}
            <FormSection label="Issue Type" htmlFor="issue-type">
              <Select
                value={type}
                onValueChange={(value) => setType(value as DataQualityIssueType)}
              >
                <SelectTrigger id="issue-type">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {issueTypes.map((issueType) => (
                    <SelectItem key={issueType} value={issueType}>
                      {issueType.replace(/-/g, ' ').replace(/\b\w/g, (character) => character.toUpperCase())}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </FormSection>

            {/* Severity */}
            <FormSection label="Severity" labelId="issue-severity-label">
              <div className="flex gap-2" role="group" aria-labelledby="issue-severity-label">
                {(['minor', 'major', 'critical'] as const).map((s) => (
                  <Button
                    key={s}
                    type="button"
                    variant={severity === s ? 'default' : 'outline'}
                    size="sm"
                    onClick={() => setSeverity(s)}
                    className="flex-1 capitalize"
                  >
                    {s}
                  </Button>
                ))}
              </div>
            </FormSection>

            {/* Frame range */}
            <FormSection label="Affected Frames" labelId="issue-frames-label">
              <div className="flex gap-2 items-center" role="group" aria-labelledby="issue-frames-label">
                <div className="flex-1 space-y-1">
                  <span className="text-xs text-muted-foreground">Start frame</span>
                  <Input
                    id="issue-frame-start"
                    type="number"
                    aria-label="Start frame"
                    value={frameStart}
                    onChange={(e) => setFrameStart(parseInt(e.target.value, 10) || 0)}
                    min={0}
                  />
                </div>
                <span className="text-muted-foreground">to</span>
                <div className="flex-1 space-y-1">
                  <span className="text-xs text-muted-foreground">End frame</span>
                  <Input
                    id="issue-frame-end"
                    type="number"
                    aria-label="End frame"
                    value={frameEnd}
                    onChange={(e) => setFrameEnd(parseInt(e.target.value, 10) || 0)}
                    min={0}
                  />
                </div>
              </div>
            </FormSection>

            {/* Description */}
            <FormSection label="Notes (optional)" htmlFor="issue-notes">
              <Textarea
                id="issue-notes"
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                placeholder="Describe the issue..."
                className="min-h-[60px] resize-none"
              />
            </FormSection>

            {/* Actions */}
            <div className="flex gap-2 pt-2">
              <Button type="button" variant="outline" onClick={onClose} className="flex-1">
                Cancel
              </Button>
              <Button type="submit" className="flex-1">
                Add Issue
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
