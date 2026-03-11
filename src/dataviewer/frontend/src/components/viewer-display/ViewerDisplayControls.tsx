/**
 * Viewer display controls — non-destructive brightness/contrast/gamma
 * adjustments applied as CSS filters on the video element.
 *
 * These settings do NOT modify frame data; they only change
 * how the video appears on screen.
 */

import { ChevronDown, Eye, RotateCcw } from 'lucide-react';
import { useState } from 'react';

import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { useViewerDisplay } from '@/stores/viewer-settings-store';

/**
 * Compact display settings bar rendered above the video.
 *
 * Collapsed by default — expands to show brightness, contrast,
 * saturation, and gamma sliders. A reset button returns all values
 * to their defaults.
 */
export function ViewerDisplayControls() {
  const [expanded, setExpanded] = useState(false);
  const { displayAdjustment, isActive, setAdjustment, resetAdjustments } =
    useViewerDisplay();

  return (
    <div className="bg-muted/60 rounded-lg text-sm">
      {/* Toggle bar */}
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-center justify-between px-3 py-1.5 hover:bg-muted/90 rounded-lg transition-colors"
      >
        <span className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
          <Eye className="h-3.5 w-3.5" />
          Display Settings
          {isActive && (
            <span className="ml-1 text-[10px] bg-primary/15 text-primary px-1.5 py-0.5 rounded-full">
              active
            </span>
          )}
        </span>
        <ChevronDown
          className={cn(
            'h-3.5 w-3.5 text-muted-foreground transition-transform',
            expanded && 'rotate-180',
          )}
        />
      </button>

      {/* Expanded panel */}
      {expanded && (
        <div className="px-3 pb-3 pt-1 space-y-2">
          <CompactSlider
            label="Brightness"
            value={displayAdjustment.brightness}
            onChange={(v) => setAdjustment('brightness', v)}
            min={-1}
            max={1}
            step={0.05}
            format={(v) => `${v > 0 ? '+' : ''}${Math.round(v * 100)}%`}
          />
          <CompactSlider
            label="Contrast"
            value={displayAdjustment.contrast}
            onChange={(v) => setAdjustment('contrast', v)}
            min={-1}
            max={1}
            step={0.05}
            format={(v) => `${v > 0 ? '+' : ''}${Math.round(v * 100)}%`}
          />
          <CompactSlider
            label="Saturation"
            value={displayAdjustment.saturation}
            onChange={(v) => setAdjustment('saturation', v)}
            min={-1}
            max={1}
            step={0.05}
            format={(v) => `${v > 0 ? '+' : ''}${Math.round(v * 100)}%`}
          />
          <CompactSlider
            label="Gamma"
            value={displayAdjustment.gamma}
            onChange={(v) => setAdjustment('gamma', v)}
            min={0.1}
            max={3}
            step={0.1}
            format={(v) => v.toFixed(1)}
          />
          <div className="flex justify-end pt-1">
            <Button
              variant="ghost"
              size="sm"
              className="h-6 text-xs px-2"
              onClick={resetAdjustments}
              disabled={!isActive}
            >
              <RotateCcw className="h-3 w-3 mr-1" />
              Reset
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

interface CompactSliderProps {
  label: string;
  value: number;
  onChange: (v: number) => void;
  min: number;
  max: number;
  step: number;
  format: (v: number) => string;
}

function CompactSlider({
  label,
  value,
  onChange,
  min,
  max,
  step,
  format,
}: CompactSliderProps) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-muted-foreground w-16 shrink-0">
        {label}
      </span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        className="flex-1 h-1.5 bg-muted rounded-lg appearance-none cursor-pointer
          [&::-webkit-slider-thumb]:appearance-none
          [&::-webkit-slider-thumb]:w-3
          [&::-webkit-slider-thumb]:h-3
          [&::-webkit-slider-thumb]:rounded-full
          [&::-webkit-slider-thumb]:bg-primary
          [&::-webkit-slider-thumb]:cursor-pointer
          [&::-moz-range-thumb]:w-3
          [&::-moz-range-thumb]:h-3
          [&::-moz-range-thumb]:rounded-full
          [&::-moz-range-thumb]:bg-primary
          [&::-moz-range-thumb]:border-0
          [&::-moz-range-thumb]:cursor-pointer"
      />
      <span className="text-xs font-mono text-muted-foreground w-10 text-right">
        {format(value)}
      </span>
    </div>
  );
}
