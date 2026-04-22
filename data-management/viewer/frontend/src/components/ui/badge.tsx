import { cva, type VariantProps } from 'class-variance-authority'
import * as React from 'react'

import { getSemanticToneClasses, type SemanticTone } from '@/lib/semantic-state'
import { cn } from '@/lib/utils'

const badgeVariants = cva(
  'inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold transition-colors focus:outline-hidden focus:ring-2 focus:ring-ring focus:ring-offset-2',
  {
    variants: {
      variant: {
        default: 'border-transparent bg-primary text-primary-foreground hover:bg-primary/80',
        secondary:
          'border-transparent bg-secondary text-secondary-foreground hover:bg-secondary/80',
        destructive:
          'border-transparent bg-destructive text-destructive-foreground hover:bg-destructive/80',
        outline: 'text-foreground',
        status: '',
      },
      tone: {
        neutral: '',
        info: '',
        success: '',
        warning: '',
        danger: '',
      },
    },
    compoundVariants: [
      {
        variant: 'status',
        tone: 'neutral',
        className: getSemanticToneClasses('badge', 'neutral'),
      },
      {
        variant: 'status',
        tone: 'info',
        className: getSemanticToneClasses('badge', 'info'),
      },
      {
        variant: 'status',
        tone: 'success',
        className: getSemanticToneClasses('badge', 'success'),
      },
      {
        variant: 'status',
        tone: 'warning',
        className: getSemanticToneClasses('badge', 'warning'),
      },
      {
        variant: 'status',
        tone: 'danger',
        className: getSemanticToneClasses('badge', 'danger'),
      },
    ],
    defaultVariants: {
      variant: 'default',
      tone: 'neutral',
    },
  },
)

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>, VariantProps<typeof badgeVariants> {
  tone?: SemanticTone
}

function Badge({ className, variant, tone, ...props }: BadgeProps) {
  return <div className={cn(badgeVariants({ variant, tone }), className)} {...props} />
}

export { Badge }
