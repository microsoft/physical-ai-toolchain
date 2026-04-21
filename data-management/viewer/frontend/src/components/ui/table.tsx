import * as React from 'react'

import { cn } from '@/lib/utils'

function Table({
  className,
  ref,
  ...props
}: React.HTMLAttributes<HTMLTableElement> & { ref?: React.Ref<HTMLTableElement> }) {
  return (
    <div className="relative w-full overflow-auto">
      <table ref={ref} className={cn('w-full caption-bottom text-sm', className)} {...props} />
    </div>
  )
}

function TableHeader({
  className,
  ref,
  ...props
}: React.HTMLAttributes<HTMLTableSectionElement> & { ref?: React.Ref<HTMLTableSectionElement> }) {
  return <thead ref={ref} className={cn('[&_tr]:border-b', className)} {...props} />
}

function TableBody({
  className,
  ref,
  ...props
}: React.HTMLAttributes<HTMLTableSectionElement> & { ref?: React.Ref<HTMLTableSectionElement> }) {
  return <tbody ref={ref} className={cn('[&_tr:last-child]:border-0', className)} {...props} />
}

function TableFooter({
  className,
  ref,
  ...props
}: React.HTMLAttributes<HTMLTableSectionElement> & { ref?: React.Ref<HTMLTableSectionElement> }) {
  return (
    <tfoot
      ref={ref}
      className={cn('bg-muted/50 border-t font-medium [&>tr]:last:border-b-0', className)}
      {...props}
    />
  )
}

function TableRow({
  className,
  ref,
  ...props
}: React.HTMLAttributes<HTMLTableRowElement> & { ref?: React.Ref<HTMLTableRowElement> }) {
  return (
    <tr
      ref={ref}
      className={cn(
        'hover:bg-muted/50 data-[state=selected]:bg-muted border-b transition-colors',
        className,
      )}
      {...props}
    />
  )
}

function TableHead({
  className,
  ref,
  ...props
}: React.ThHTMLAttributes<HTMLTableCellElement> & { ref?: React.Ref<HTMLTableCellElement> }) {
  return (
    <th
      ref={ref}
      className={cn(
        'text-muted-foreground h-10 px-2 text-left align-middle font-medium',
        className,
      )}
      {...props}
    />
  )
}

function TableCell({
  className,
  ref,
  ...props
}: React.TdHTMLAttributes<HTMLTableCellElement> & { ref?: React.Ref<HTMLTableCellElement> }) {
  return <td ref={ref} className={cn('p-2 align-middle', className)} {...props} />
}

function TableCaption({
  className,
  ref,
  ...props
}: React.HTMLAttributes<HTMLTableCaptionElement> & {
  ref?: React.Ref<HTMLTableCaptionElement>
}) {
  return (
    <caption ref={ref} className={cn('text-muted-foreground mt-4 text-sm', className)} {...props} />
  )
}

export { Table, TableBody, TableCaption, TableCell, TableFooter, TableHead, TableHeader, TableRow }
