/**
 * LabelFilter - filter episodes by label in the sidebar.
 */

import { X } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { useLabelStore } from '@/stores/label-store';

export function LabelFilter({ compact = false }: { compact?: boolean }) {
    const availableLabels = useLabelStore((state) => state.availableLabels);
    const filterLabels = useLabelStore((state) => state.filterLabels);
    const toggleFilterLabel = useLabelStore((state) => state.toggleFilterLabel);
    const isLoaded = useLabelStore((state) => state.isLoaded);

    if (!isLoaded || availableLabels.length === 0) return null;

    return (
        <div className={compact ? 'space-y-1' : 'space-y-1 border-b px-2 py-2'}>
            <div className="text-xs font-medium text-muted-foreground">Filter by label</div>
            <div className="flex flex-wrap gap-1">
                {availableLabels.map((label) => {
                    const isActive = filterLabels.includes(label);
                    return (
                        <button
                            key={label}
                            onClick={() => toggleFilterLabel(label)}
                            className="focus:outline-none"
                        >
                            <Badge
                                variant={isActive ? 'default' : 'outline'}
                                className={`cursor-pointer text-[10px] px-1.5 py-0 h-5 select-none ${isActive ? 'shadow-sm' : 'opacity-60 hover:opacity-100'
                                    }`}
                            >
                                {label}
                                {isActive && <X className="h-2.5 w-2.5 ml-0.5" />}
                            </Badge>
                        </button>
                    );
                })}
            </div>
        </div>
    );
}
