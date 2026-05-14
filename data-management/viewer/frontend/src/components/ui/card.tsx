import * as React from 'react'

import { cn } from '@/lib/utils'

type CardDivProps = React.HTMLAttributes<HTMLDivElement> & { ref?: React.Ref<HTMLDivElement> }
type CardHeadingProps = React.HTMLAttributes<HTMLHeadingElement> & {
  ref?: React.Ref<HTMLHeadingElement>
}
type CardParagraphProps = React.HTMLAttributes<HTMLParagraphElement> & {
  ref?: React.Ref<HTMLParagraphElement>
}

function Card({ className, ref, ...props }: CardDivProps) {
  return (
    <div
      ref={ref}
      className={cn('bg-card text-card-foreground rounded-xl border shadow-sm', className)}
      {...props}
    />
  )
}

function CardHeader({ className, ref, ...props }: CardDivProps) {
  return <div ref={ref} className={cn('flex flex-col space-y-1.5 p-6', className)} {...props} />
}

function CardTitle({ className, ref, ...props }: CardHeadingProps) {
  return (
    // eslint-disable-next-line jsx-a11y/heading-has-content
    <h3
      ref={ref}
      className={cn('leading-none font-semibold tracking-tight', className)}
      {...props}
    />
  )
}

function CardDescription({ className, ref, ...props }: CardParagraphProps) {
  return <p ref={ref} className={cn('text-muted-foreground text-sm', className)} {...props} />
}

function CardContent({ className, ref, ...props }: CardDivProps) {
  return <div ref={ref} className={cn('p-6 pt-0', className)} {...props} />
}

function CardFooter({ className, ref, ...props }: CardDivProps) {
  return <div ref={ref} className={cn('flex items-center p-6 pt-0', className)} {...props} />
}

export { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle }
