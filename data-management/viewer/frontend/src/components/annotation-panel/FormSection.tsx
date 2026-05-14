import type { ReactNode } from 'react'

import { Label } from '@/components/ui/label'
import { cn } from '@/lib/utils'

interface FormSectionProps {
  label: string
  children: ReactNode
  htmlFor?: string
  labelId?: string
  className?: string
  description?: ReactNode
}

export function FormSection({
  label,
  children,
  htmlFor,
  labelId,
  className,
  description,
}: FormSectionProps) {
  return (
    <div className={cn('space-y-2', className)}>
      {htmlFor ? (
        <Label htmlFor={htmlFor}>{label}</Label>
      ) : (
        <div id={labelId} className="text-sm font-medium">
          {label}
        </div>
      )}
      {children}
      {description ? <p className="text-muted-foreground text-xs">{description}</p> : null}
    </div>
  )
}
