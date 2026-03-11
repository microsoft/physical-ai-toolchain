import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { Badge } from '@/components/ui/badge'
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/components/ui/command'
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuTrigger,
} from '@/components/ui/context-menu'
import { Slider, SliderRange, SliderThumb, SliderTrack } from '@/components/ui/slider'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Textarea } from '@/components/ui/textarea'

describe('shared ui primitives', () => {
  it('supports external filtering around command input state', async () => {
    const user = userEvent.setup()

    function FilterableCommand() {
      const [query, setQuery] = useState('')
      const items = ['houston_lerobot_fixed', 'customer_lerobot']
      const filteredItems = items.filter((item) => item.includes(query.toLowerCase()))

      return (
        <Command shouldFilter={false}>
          <CommandInput value={query} onValueChange={setQuery} placeholder="Filter datasets" />
          <CommandList>
            <CommandEmpty>No datasets match the current filter.</CommandEmpty>
            <CommandGroup heading="Datasets">
              {filteredItems.map((item) => (
                <CommandItem key={item} value={item}>
                  {item}
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      )
    }

    render(<FilterableCommand />)

    await user.type(screen.getByPlaceholderText('Filter datasets'), 'customer')

    expect(screen.getByText('customer_lerobot')).toBeInTheDocument()
    expect(screen.queryByText('houston_lerobot_fixed')).not.toBeInTheDocument()
  })

  it('opens a context menu from a trigger and runs item actions', async () => {
    const user = userEvent.setup()
    const onRename = vi.fn()

    render(
      <ContextMenu>
        <ContextMenuTrigger>Right X</ContextMenuTrigger>
        <ContextMenuContent>
          <ContextMenuItem onSelect={onRename}>Edit Name</ContextMenuItem>
        </ContextMenuContent>
      </ContextMenu>,
    )

    fireEvent.contextMenu(screen.getByText('Right X'))
    await user.click(await screen.findByText('Edit Name'))

    expect(onRename).toHaveBeenCalledTimes(1)
  })

  it('renders multiple slider thumbs for range values', () => {
    render(
      <Slider value={[10, 20]} max={100}>
        <SliderTrack>
          <SliderRange />
        </SliderTrack>
        <SliderThumb aria-label="Range start" />
        <SliderThumb aria-label="Range end" />
      </Slider>,
    )

    expect(screen.getAllByRole('slider')).toHaveLength(2)
  })

  it('captures textarea input', async () => {
    const user = userEvent.setup()

    render(<Textarea placeholder="Describe the issue..." />)

    const textarea = screen.getByPlaceholderText('Describe the issue...')
    await user.type(textarea, 'Frame drift near the end of the episode')

    expect(textarea).toHaveValue('Frame drift near the end of the episode')
  })

  it('renders semantic table structure for shared data tables', () => {
    render(
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Class</TableHead>
            <TableHead>Count</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          <TableRow>
            <TableCell>person</TableCell>
            <TableCell>12</TableCell>
          </TableRow>
        </TableBody>
      </Table>,
    )

    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Class' })).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: 'person' })).toBeInTheDocument()
  })

  it('supports semantic badge tones for repeated status states', () => {
    render(
      <Badge variant="status" tone="warning">
        Pending review
      </Badge>,
    )

    expect(screen.getByText('Pending review')).toHaveClass(
      'bg-status-warning-subtle',
      'text-status-warning-foreground',
      'border-status-warning-border',
    )
  })
})
