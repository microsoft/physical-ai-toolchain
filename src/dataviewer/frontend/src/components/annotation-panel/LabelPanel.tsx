/**
 * LabelPanel - multi-select label tagging for episodes.
 *
 * Displays available labels as toggleable chips and allows adding custom labels.
 */

import { Check, Plus, X } from 'lucide-react';
import { useCallback, useState } from 'react';

import { Button } from '@/components/ui/button';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import {
    useAddLabelOption,
    useCurrentEpisodeLabels,
    useRemoveLabelOption,
} from '@/hooks/use-labels';
import { cn } from '@/lib/utils';
import { DEFAULT_LABELS, useLabelStore } from '@/stores/label-store';

interface LabelPanelProps {
    episodeIndex: number;
}

export function LabelPanel({ episodeIndex }: LabelPanelProps) {
    const [newLabel, setNewLabel] = useState('');
    const [errorMessage, setErrorMessage] = useState<string | null>(null);
    const [pendingDeleteLabel, setPendingDeleteLabel] = useState<string | null>(null);
    const availableLabels = useLabelStore((state) => state.availableLabels);
    const { currentLabels, toggle } = useCurrentEpisodeLabels(episodeIndex);
    const addOption = useAddLabelOption();
    const removeOption = useRemoveLabelOption();

    const handleAddLabel = useCallback(async () => {
        const normalized = newLabel.trim().toUpperCase();
        if (!normalized) return;
        try {
            await addOption.mutateAsync(normalized);
            setNewLabel('');
            setErrorMessage(null);
        } catch (error) {
            const detail = error instanceof Error ? error.message : 'Unknown error';
            setErrorMessage(`Failed to add label: ${detail}`);
        }
    }, [newLabel, addOption]);

    const handleKeyDown = useCallback(
        (e: React.KeyboardEvent) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                handleAddLabel();
            }
        },
        [handleAddLabel],
    );

    const handleDeleteLabel = useCallback(
        (label: string) => {
            setPendingDeleteLabel(label);
        },
        [],
    );

    const handleToggleLabel = useCallback(
        async (label: string) => {
            try {
                await toggle(label);
                setErrorMessage(null);
            } catch (error) {
                const detail = error instanceof Error ? error.message : 'Unknown error';
                setErrorMessage(`Failed to update labels: ${detail}`);
            }
        },
        [toggle],
    );

    const confirmDeleteLabel = useCallback(async () => {
        if (!pendingDeleteLabel) return;

        try {
            await removeOption.mutateAsync(pendingDeleteLabel);
            setErrorMessage(null);
            setPendingDeleteLabel(null);
        } catch (error) {
            const detail = error instanceof Error ? error.message : 'Unknown error';
            setErrorMessage(`Failed to delete label: ${detail}`);
        }
    }, [pendingDeleteLabel, removeOption]);

    return (
        <div className="space-y-3">
            <div className="flex items-center justify-between gap-3">
                <h3 className="text-sm font-medium">Episode Labels</h3>
            </div>

            {errorMessage && <p className="text-xs text-destructive">{errorMessage}</p>}

            {/* Label toggles */}
            <div className="flex flex-wrap gap-2">
                {availableLabels.map((label) => {
                    const isSelected = currentLabels.includes(label);
                    const isProtected = DEFAULT_LABELS.includes(label);
                    return (
                        <div
                            key={label}
                            className={cn(
                                'inline-flex items-center rounded-full border transition-all',
                                isSelected
                                    ? 'border-transparent bg-primary text-primary-foreground shadow-sm'
                                    : 'text-foreground hover:bg-accent',
                            )}
                        >
                            <button
                                type="button"
                                onClick={() => void handleToggleLabel(label)}
                                className="inline-flex items-center gap-1 rounded-l-full px-2.5 py-0.5 text-xs font-semibold focus:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                            >
                                {isSelected && <Check className="h-3 w-3 mr-1" />}
                                {label}
                            </button>
                            {!isProtected && (
                                <button
                                    type="button"
                                    onClick={() => handleDeleteLabel(label)}
                                    aria-label={`Delete label ${label}`}
                                    title={`Delete label ${label}`}
                                    disabled={removeOption.isPending}
                                    className={cn(
                                        'mr-1 inline-flex h-5 w-5 items-center justify-center rounded-full focus:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2',
                                        isSelected
                                            ? 'hover:bg-primary-foreground/15'
                                            : 'hover:bg-accent-foreground/10',
                                    )}
                                >
                                    <X className="h-3 w-3" />
                                </button>
                            )}
                        </div>
                    );
                })}
            </div>

            <p className="text-xs text-muted-foreground">Built-in labels stay available. Custom labels require confirmation before deletion.</p>

            {/* Add custom label */}
            <div className="flex gap-2">
                <Input
                    value={newLabel}
                    onChange={(e) => setNewLabel(e.target.value)}
                    onKeyDown={handleKeyDown}
                    placeholder="Add custom label..."
                    className="h-8 text-sm"
                />
                <Button
                    size="sm"
                    variant="outline"
                    onClick={() => void handleAddLabel()}
                    disabled={!newLabel.trim() || addOption.isPending}
                    className="h-8 px-3 gap-1"
                >
                    <Plus className="h-3 w-3" />
                    Add
                </Button>
            </div>

            {/* Current labels summary */}
            {currentLabels.length > 0 && (
                <div className="text-xs text-muted-foreground">
                    Applied: {currentLabels.join(', ')}
                </div>
            )}

            <Dialog open={pendingDeleteLabel !== null} onOpenChange={(open) => !open && setPendingDeleteLabel(null)}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Delete Label</DialogTitle>
                        <DialogDescription>
                            {pendingDeleteLabel
                                ? `Delete ${pendingDeleteLabel} from the dataset label list and remove it from every episode assignment?`
                                : 'Delete this label from the dataset.'}
                        </DialogDescription>
                    </DialogHeader>
                    <DialogFooter>
                        <Button type="button" variant="outline" onClick={() => setPendingDeleteLabel(null)}>
                            Cancel
                        </Button>
                        <Button type="button" variant="destructive" onClick={() => void confirmDeleteLabel()}>
                            Delete Label
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}
