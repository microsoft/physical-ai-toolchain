/**
 * Conflict resolution dialog for offline sync conflicts.
 */

import { AlertTriangle, Check, Clock, Cloud, Laptop } from 'lucide-react'
import { useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Label } from '@/components/ui/label'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import { ScrollArea } from '@/components/ui/scroll-area'
import { cn } from '@/lib/utils'

export interface ConflictVersion {
  source: 'local' | 'server'
  data: unknown
  updatedAt: string
  updatedBy?: string
}

export interface ConflictResolutionProps {
  /** Whether the dialog is open */
  open: boolean
  /** Handler for dialog close */
  onOpenChange: (open: boolean) => void
  /** Episode ID with conflict */
  episodeId: string
  /** Local version */
  localVersion: ConflictVersion
  /** Server version */
  serverVersion: ConflictVersion
  /** Resolution handler */
  onResolve: (choice: 'local' | 'server' | 'merge') => Promise<void>
  /** Additional class names */
  className?: string
}

/**
 * Displays conflict resolution options.
 */
export function ConflictResolution({
  open,
  onOpenChange,
  episodeId,
  localVersion,
  serverVersion,
  onResolve,
  className,
}: ConflictResolutionProps) {
  const [choice, setChoice] = useState<'local' | 'server'>('server')
  const [isResolving, setIsResolving] = useState(false)

  const handleResolve = async () => {
    setIsResolving(true)
    try {
      await onResolve(choice)
      onOpenChange(false)
    } finally {
      setIsResolving(false)
    }
  }

  const formatDate = (dateString: string) => {
    try {
      return new Date(dateString).toLocaleString()
    } catch {
      return dateString
    }
  }

  const formatData = (data: unknown) => {
    try {
      return JSON.stringify(data, null, 2)
    } catch {
      return String(data)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className={cn('max-w-2xl', className)}>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <AlertTriangle className="h-5 w-5 text-orange-500" />
            Sync Conflict
          </DialogTitle>
          <DialogDescription>
            The annotation for episode <strong>{episodeId}</strong> was modified both locally and on
            the server. Choose which version to keep.
          </DialogDescription>
        </DialogHeader>

        <RadioGroup
          value={choice}
          onValueChange={(v: string) => setChoice(v as 'local' | 'server')}
        >
          <div className="grid grid-cols-2 gap-4">
            {/* Local version */}
            <div
              className={cn(
                'cursor-pointer rounded-lg border p-4 transition-colors',
                choice === 'local' && 'border-primary bg-primary/5',
              )}
              role="button"
              tabIndex={0}
              onClick={() => setChoice('local')}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') setChoice('local')
              }}
            >
              <div className="mb-3 flex items-center gap-2">
                <RadioGroupItem value="local" id="local" />
                <Label htmlFor="local" className="flex cursor-pointer items-center gap-2">
                  <Laptop className="h-4 w-4" />
                  <span className="font-medium">Local Version</span>
                </Label>
                <Badge variant="outline" className="ml-auto">
                  Your changes
                </Badge>
              </div>
              <div className="mb-2 flex items-center gap-1 text-xs text-muted-foreground">
                <Clock className="h-3 w-3" />
                {formatDate(localVersion.updatedAt)}
              </div>
              <ScrollArea className="h-32 rounded border bg-muted/50 p-2">
                <pre className="text-xs">{formatData(localVersion.data)}</pre>
              </ScrollArea>
            </div>

            {/* Server version */}
            <div
              className={cn(
                'cursor-pointer rounded-lg border p-4 transition-colors',
                choice === 'server' && 'border-primary bg-primary/5',
              )}
              role="button"
              tabIndex={0}
              onClick={() => setChoice('server')}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') setChoice('server')
              }}
            >
              <div className="mb-3 flex items-center gap-2">
                <RadioGroupItem value="server" id="server" />
                <Label htmlFor="server" className="flex cursor-pointer items-center gap-2">
                  <Cloud className="h-4 w-4" />
                  <span className="font-medium">Server Version</span>
                </Label>
                <Badge variant="secondary" className="ml-auto">
                  {serverVersion.updatedBy || 'Unknown'}
                </Badge>
              </div>
              <div className="mb-2 flex items-center gap-1 text-xs text-muted-foreground">
                <Clock className="h-3 w-3" />
                {formatDate(serverVersion.updatedAt)}
              </div>
              <ScrollArea className="h-32 rounded border bg-muted/50 p-2">
                <pre className="text-xs">{formatData(serverVersion.data)}</pre>
              </ScrollArea>
            </div>
          </div>
        </RadioGroup>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={handleResolve} disabled={isResolving}>
            {isResolving ? (
              'Resolving...'
            ) : (
              <>
                <Check className="mr-2 h-4 w-4" />
                Keep {choice === 'local' ? 'Local' : 'Server'} Version
              </>
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
