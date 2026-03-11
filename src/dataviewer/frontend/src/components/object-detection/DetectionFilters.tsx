/**
 * Detection filter controls.
 */

import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Label } from '@/components/ui/label';
import type { DetectionFilters as FilterState } from '@/types/detection';

interface DetectionFiltersProps {
  filters: FilterState;
  availableClasses: string[];
  onFiltersChange: (filters: FilterState) => void;
}

export function DetectionFilters({
  filters,
  availableClasses,
  onFiltersChange,
}: DetectionFiltersProps) {
  const handleClassToggle = (className: string, checked: boolean) => {
    const newClasses = checked
      ? [...filters.classes, className]
      : filters.classes.filter((c) => c !== className);
    onFiltersChange({ ...filters, classes: newClasses });
  };

  const handleConfidenceChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    onFiltersChange({ ...filters, minConfidence: parseFloat(e.target.value) });
  };

  const handleReset = () => {
    onFiltersChange({ classes: [], minConfidence: 0.25 });
  };

  const handleSelectAll = () => {
    onFiltersChange({ ...filters, classes: [] });
  };

  const handleSelectNone = () => {
    onFiltersChange({ ...filters, classes: availableClasses });
  };

  return (
    <div className="space-y-4">
      {/* Confidence threshold */}
      <div className="space-y-2">
        <div className="flex justify-between">
          <Label>Min Confidence</Label>
          <span className="text-sm text-muted-foreground">
            {(filters.minConfidence * 100).toFixed(0)}%
          </span>
        </div>
        <input
          type="range"
          min="0"
          max="1"
          step="0.05"
          value={filters.minConfidence}
          onChange={handleConfidenceChange}
          className="w-full h-2 bg-muted rounded-lg appearance-none cursor-pointer"
        />
        <div className="flex justify-between text-xs text-muted-foreground">
          <span>0%</span>
          <span>50%</span>
          <span>100%</span>
        </div>
      </div>

      {/* Class filter */}
      <div className="space-y-2">
        <div className="flex justify-between items-center">
          <Label>Classes</Label>
          <div className="flex gap-2">
            <Button variant="ghost" size="sm" className="h-6 px-2 text-xs" onClick={handleSelectAll}>
              All
            </Button>
            <Button variant="ghost" size="sm" className="h-6 px-2 text-xs" onClick={handleSelectNone}>
              None
            </Button>
          </div>
        </div>
        <div className="grid grid-cols-2 gap-2 max-h-40 overflow-y-auto p-2 border rounded-md">
          {availableClasses.length === 0 ? (
            <p className="col-span-2 text-sm text-muted-foreground text-center py-2">
              No classes detected yet
            </p>
          ) : (
            availableClasses.map((className) => (
              <div key={className} className="flex items-center space-x-2">
                <Checkbox
                  id={`class-${className}`}
                  checked={
                    filters.classes.length === 0 || filters.classes.includes(className)
                  }
                  onCheckedChange={(checked) =>
                    handleClassToggle(className, checked as boolean)
                  }
                />
                <label
                  htmlFor={`class-${className}`}
                  className="text-sm cursor-pointer"
                >
                  {className}
                </label>
              </div>
            ))
          )}
        </div>
      </div>

      {/* Reset button */}
      <Button variant="outline" size="sm" onClick={handleReset} className="w-full">
        Reset Filters
      </Button>
    </div>
  );
}
