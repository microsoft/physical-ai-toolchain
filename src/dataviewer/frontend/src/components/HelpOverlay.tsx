/**
 * Help overlay component showing keyboard shortcuts.
 */

import { Keyboard,X } from 'lucide-react';
import { useEffect } from 'react';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { formatShortcut, type KeyboardShortcut } from '@/hooks/use-keyboard-shortcuts';

interface HelpOverlayProps {
  /** Whether the overlay is visible */
  open: boolean;
  /** Callback to close the overlay */
  onClose: () => void;
  /** Shortcuts to display */
  shortcuts?: KeyboardShortcut[];
}

/**
 * Full-screen overlay showing available keyboard shortcuts.
 *
 * @example
 * ```tsx
 * <HelpOverlay
 *   open={showHelp}
 *   onClose={() => setShowHelp(false)}
 *   shortcuts={shortcuts}
 * />
 * ```
 */
export function HelpOverlay({ open, onClose, shortcuts = [] }: HelpOverlayProps) {
  // Close on Escape
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      }
    };

    if (open) {
      window.addEventListener('keydown', handleKeyDown);
      return () => window.removeEventListener('keydown', handleKeyDown);
    }
  }, [open, onClose]);

  if (!open) return null;

  // Group shortcuts by category
  const categories = {
    annotation: shortcuts.filter((s) => s.category === 'annotation'),
    playback: shortcuts.filter((s) => s.category === 'playback'),
    navigation: shortcuts.filter((s) => s.category === 'navigation'),
    workflow: shortcuts.filter((s) => s.category === 'workflow'),
  };

  // Default shortcuts if none provided
  const defaultShortcuts = {
    annotation: [
      { key: 'S', description: 'Mark as Success' },
      { key: 'P', description: 'Mark as Partial' },
      { key: 'F', description: 'Mark as Failure' },
      { key: '1-5', description: 'Set Quality Rating' },
      { key: 'J', description: 'Toggle Jittery Flag' },
    ],
    playback: [
      { key: 'Space', description: 'Play/Pause' },
      { key: '←', description: 'Previous Frame' },
      { key: '→', description: 'Next Frame' },
      { key: '↑', description: 'Back 10 Frames' },
      { key: '↓', description: 'Forward 10 Frames' },
    ],
    navigation: [
      { key: 'Shift+←', description: 'Previous Episode' },
      { key: 'Shift+→', description: 'Next Episode' },
    ],
    workflow: [
      { key: '↵ Enter', description: 'Save & Next Episode' },
      { key: 'Ctrl+S', description: 'Save Current' },
      { key: '?', description: 'Show This Help' },
      { key: 'Esc', description: 'Close Dialog' },
    ],
  };

  const renderCategory = (
    title: string,
    items: { key: string; description: string }[] | KeyboardShortcut[]
  ) => {
    if (items.length === 0) return null;

    return (
      <div className="space-y-2">
        <h3 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">
          {title}
        </h3>
        <div className="grid gap-1">
          {items.map((item) => (
            <div
              key={`${item.key}-${item.description}`}
              className="flex items-center justify-between py-1.5 px-2 rounded hover:bg-muted/50"
            >
              <span className="text-sm">{'description' in item ? item.description : ''}</span>
              <kbd className="px-2 py-1 text-xs font-mono bg-muted rounded border">
                {'key' in item && typeof item.key === 'string'
                  ? 'action' in item
                    ? formatShortcut(item as KeyboardShortcut)
                    : item.key
                  : ''}
              </kbd>
            </div>
          ))}
        </div>
      </div>
    );
  };

  const hasCustomShortcuts = shortcuts.length > 0;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      role="button"
      tabIndex={0}
      onClick={onClose}
      onKeyDown={(e) => { if (e.key === 'Escape' || e.key === 'Enter') onClose(); }}
    >
      <Card
        className="w-full max-w-2xl max-h-[80vh] overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <CardHeader className="flex flex-row items-center justify-between pb-4 border-b">
          <CardTitle className="flex items-center gap-2">
            <Keyboard className="h-5 w-5" />
            Keyboard Shortcuts
          </CardTitle>
          <Button variant="ghost" size="icon" onClick={onClose}>
            <X className="h-4 w-4" />
          </Button>
        </CardHeader>
        <CardContent className="p-6 overflow-y-auto max-h-[60vh]">
          <div className="grid md:grid-cols-2 gap-6">
            <div className="space-y-6">
              {renderCategory(
                'Annotation',
                hasCustomShortcuts ? categories.annotation : defaultShortcuts.annotation
              )}
              {renderCategory(
                'Playback',
                hasCustomShortcuts ? categories.playback : defaultShortcuts.playback
              )}
            </div>
            <div className="space-y-6">
              {renderCategory(
                'Navigation',
                hasCustomShortcuts ? categories.navigation : defaultShortcuts.navigation
              )}
              {renderCategory(
                'Workflow',
                hasCustomShortcuts ? categories.workflow : defaultShortcuts.workflow
              )}
            </div>
          </div>

          <div className="mt-6 pt-4 border-t text-center text-sm text-muted-foreground">
            Press <kbd className="px-1.5 py-0.5 bg-muted rounded border text-xs">?</kbd> anytime
            to show this help
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
