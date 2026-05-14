/**
 * Joint selector for trajectory visualization.
 *
 * Renders grouped toggle chips organized by actuator category,
 * with per-group and global selection controls. Supports inline editing,
 * context menus, and drag-and-drop reordering when editable.
 */

import {
  DndContext,
  type DragEndEvent,
  DragOverlay,
  type DragStartEvent,
  PointerSensor,
  pointerWithin,
  useDroppable,
  useSensor,
  useSensors,
} from '@dnd-kit/core'
import { horizontalListSortingStrategy, SortableContext, useSortable } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { Settings } from 'lucide-react'
import { type KeyboardEvent, useCallback, useEffect, useRef, useState } from 'react'

import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuTrigger,
} from '@/components/ui/context-menu'
import { cn } from '@/lib/utils'

import { getJointColor, getJointLabel, JOINT_GROUPS, type JointGroup } from './joint-constants'

interface JointSelectorProps {
  jointCount: number
  selectedJoints: number[]
  onSelectJoints: (joints: number[]) => void
  colors: string[]
  groups?: JointGroup[]
  labels?: Record<string, string>
  editable?: boolean
  onEditJointLabel?: (index: number, label: string) => void
  onEditGroupLabel?: (groupId: string, label: string) => void
  onCreateGroup?: (label: string, jointIndices: number[]) => void
  onDeleteGroup?: (groupId: string) => void
  onMoveJoint?: (
    jointIndex: number,
    fromGroupId: string,
    toGroupId: string,
    toPosition: number,
  ) => void
  onOpenDefaults?: () => void
}

function InlineEdit({
  value,
  onCommit,
  onCancel,
}: {
  value: string
  onCommit: (val: string) => void
  onCancel: () => void
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [text, setText] = useState(value)

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      inputRef.current?.focus()
      inputRef.current?.select()
    }, 0)

    return () => window.clearTimeout(timeoutId)
  }, [])

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      const trimmed = text.trim()
      if (trimmed) onCommit(trimmed)
      else onCancel()
    } else if (e.key === 'Escape') {
      e.preventDefault()
      onCancel()
    }
  }

  return (
    <input
      ref={inputRef}
      value={text}
      onChange={(e) => setText(e.target.value)}
      onKeyDown={handleKeyDown}
      onBlur={onCancel}
      className="border-primary w-20 border-b bg-transparent text-xs outline-hidden"
    />
  )
}

function SortableChip({
  idx,
  isSelected,
  color,
  label,
  editable,
  editingJoint,
  onToggle,
  onStartEdit,
  onCommitEdit,
  onCancelEdit,
  onCreateGroup,
}: {
  idx: number
  isSelected: boolean
  color: string
  label: string
  editable?: boolean
  editingJoint: number | null
  onToggle: () => void
  onStartEdit: () => void
  onCommitEdit: (val: string) => void
  onCancelEdit: () => void
  onCreateGroup?: (jointIdx: number) => void
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: `joint-${idx}`,
    disabled: !editable || editingJoint === idx,
  })

  const style = {
    color,
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : undefined,
  }

  const chipClasses = cn(
    'inline-flex items-center gap-1 px-1.5 py-0.5 text-xs rounded-sm border transition-all',
    isSelected ? 'border-current font-medium' : 'border-transparent opacity-40 hover:opacity-70',
  )

  if (editingJoint === idx) {
    return (
      <div ref={setNodeRef} data-joint-chip className={chipClasses} style={style}>
        <span className="h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: color }} />
        <InlineEdit value={label} onCommit={onCommitEdit} onCancel={onCancelEdit} />
      </div>
    )
  }

  const chip = (
    <button
      ref={setNodeRef}
      type="button"
      data-joint-chip
      onClick={onToggle}
      className={chipClasses}
      style={style}
      {...attributes}
      {...listeners}
    >
      <span className="h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: color }} />
      {label}
    </button>
  )

  if (!editable) return chip

  return (
    <ContextMenu>
      <ContextMenuTrigger asChild>{chip}</ContextMenuTrigger>
      <ContextMenuContent
        className="min-w-[140px]"
        onCloseAutoFocus={(event) => event.preventDefault()}
      >
        <ContextMenuItem className="text-xs" onSelect={onStartEdit}>
          Edit Name
        </ContextMenuItem>
        {onCreateGroup && (
          <ContextMenuItem className="text-xs" onSelect={() => onCreateGroup(idx)}>
            New Grouping
          </ContextMenuItem>
        )}
      </ContextMenuContent>
    </ContextMenu>
  )
}

/** Droppable group container — each group is a separate drop target. */
function DroppableGroup({
  groupId,
  children,
  items,
}: {
  groupId: string
  children: React.ReactNode
  items: string[]
}) {
  const { setNodeRef, isOver } = useDroppable({ id: `group-${groupId}` })

  return (
    <SortableContext items={items} strategy={horizontalListSortingStrategy}>
      <div
        ref={setNodeRef}
        data-group-id={groupId}
        className={cn(
          'flex items-center gap-1 rounded-sm px-0.5 transition-colors',
          isOver && 'bg-accent/40',
        )}
      >
        {children}
      </div>
    </SortableContext>
  )
}

export function JointSelector({
  jointCount,
  selectedJoints,
  onSelectJoints,
  colors,
  groups = JOINT_GROUPS,
  labels,
  editable,
  onEditJointLabel,
  onEditGroupLabel,
  onCreateGroup,
  onDeleteGroup,
  onMoveJoint,
  onOpenDefaults,
}: JointSelectorProps) {
  const [editingJoint, setEditingJoint] = useState<number | null>(null)
  const [editingGroup, setEditingGroup] = useState<string | null>(null)
  const [activeId, setActiveId] = useState<string | null>(null)

  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }))

  const resolveLabel = useCallback(
    (idx: number) => labels?.[String(idx)] ?? getJointLabel(idx),
    [labels],
  )

  const toggleJoint = (jointIdx: number) => {
    if (selectedJoints.includes(jointIdx)) {
      onSelectJoints(selectedJoints.filter((j) => j !== jointIdx))
    } else {
      onSelectJoints([...selectedJoints, jointIdx].sort((a, b) => a - b))
    }
  }

  const selectAll = () => {
    onSelectJoints(Array.from({ length: jointCount }, (_, i) => i))
  }

  const clearAll = () => {
    onSelectJoints([])
  }

  const toggleGroup = (indices: number[]) => {
    const valid = indices.filter((i) => i < jointCount)
    const allSelected = valid.every((i) => selectedJoints.includes(i))
    if (allSelected) {
      onSelectJoints(selectedJoints.filter((j) => !valid.includes(j)))
    } else {
      const merged = new Set([...selectedJoints, ...valid])
      onSelectJoints([...merged].sort((a, b) => a - b))
    }
  }

  const handleCreateGroup = useCallback(
    (jointIdx: number) => {
      onCreateGroup?.('New Group', [jointIdx])
    },
    [onCreateGroup],
  )

  const handleDragStart = (event: DragStartEvent) => {
    setActiveId(event.active.id as string)
  }

  const findGroupForJoint = useCallback(
    (jointIdx: number): string => {
      for (const g of groups) {
        if (g.indices.includes(jointIdx)) return g.id
      }
      return 'other'
    },
    [groups],
  )

  const handleDragEnd = (event: DragEndEvent) => {
    setActiveId(null)
    const { active, over } = event
    if (!over || !onMoveJoint) return

    const activeIdx = parseInt((active.id as string).replace('joint-', ''), 10)
    const overId = over.id as string

    const fromGroupId = findGroupForJoint(activeIdx)

    // Dropped on a group container
    if (overId.startsWith('group-')) {
      const toGroupId = overId.replace('group-', '')
      if (fromGroupId === toGroupId) return
      const toGroup = groups.find((g) => g.id === toGroupId)
      onMoveJoint(activeIdx, fromGroupId, toGroupId, toGroup?.indices.length ?? 0)
      return
    }

    // Dropped on another joint chip
    if (overId.startsWith('joint-')) {
      const overIdx = parseInt(overId.replace('joint-', ''), 10)
      if (activeIdx === overIdx) return
      const toGroupId = findGroupForJoint(overIdx)
      const toGroup = groups.find((g) => g.id === toGroupId)
      if (!toGroup) return
      const toPosition = toGroup.indices.indexOf(overIdx)
      onMoveJoint(activeIdx, fromGroupId, toGroupId, toPosition)
    }
  }

  if (jointCount === 0) {
    return <span className="text-muted-foreground text-sm">No joints available</span>
  }

  const allGroupedIndices = new Set(groups.flatMap((g) => g.indices))
  const visibleGroups = groups
    .map((g) => ({ ...g, indices: g.indices.filter((i) => i < jointCount) }))
    .filter((g) => g.indices.length > 0)

  const otherIndices = Array.from({ length: jointCount }, (_, i) => i).filter(
    (i) => !allGroupedIndices.has(i),
  )

  const renderGroupLabel = (group: { id: string; label: string; indices: number[] }) => {
    const allActive = group.indices.every((i) => selectedJoints.includes(i))

    if (editingGroup === group.id) {
      return (
        <InlineEdit
          value={group.label}
          onCommit={(val) => {
            onEditGroupLabel?.(group.id, val)
            setEditingGroup(null)
          }}
          onCancel={() => setEditingGroup(null)}
        />
      )
    }

    const labelButton = (
      <button
        onClick={() => toggleGroup(group.indices)}
        className={cn(
          'text-xs font-medium whitespace-nowrap transition-colors',
          allActive ? 'text-foreground' : 'text-muted-foreground hover:text-foreground',
        )}
      >
        {group.label}
      </button>
    )

    if (!editable) return labelButton

    return (
      <ContextMenu>
        <ContextMenuTrigger asChild>{labelButton}</ContextMenuTrigger>
        <ContextMenuContent
          className="min-w-[140px]"
          onCloseAutoFocus={(event) => event.preventDefault()}
        >
          <ContextMenuItem className="text-xs" onSelect={() => setEditingGroup(group.id)}>
            Edit Name
          </ContextMenuItem>
          {onDeleteGroup && (
            <ContextMenuItem
              className="text-destructive focus:text-destructive text-xs"
              onSelect={() => onDeleteGroup(group.id)}
            >
              Delete Grouping
            </ContextMenuItem>
          )}
        </ContextMenuContent>
      </ContextMenu>
    )
  }

  const renderChips = (indices: number[]) =>
    indices.map((idx) => (
      <SortableChip
        key={idx}
        idx={idx}
        isSelected={selectedJoints.includes(idx)}
        color={getJointColor(idx, colors)}
        label={resolveLabel(idx)}
        editable={editable}
        editingJoint={editingJoint}
        onToggle={() => toggleJoint(idx)}
        onStartEdit={() => setEditingJoint(idx)}
        onCommitEdit={(val) => {
          onEditJointLabel?.(idx, val)
          setEditingJoint(null)
        }}
        onCancelEdit={() => setEditingJoint(null)}
        onCreateGroup={onCreateGroup ? handleCreateGroup : undefined}
      />
    ))

  const activeIdx = activeId ? parseInt(activeId.replace('joint-', ''), 10) : null

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={pointerWithin}
      onDragStart={handleDragStart}
      onDragEnd={handleDragEnd}
    >
      <div className="flex flex-col gap-1">
        {/* Global controls */}
        <div className="flex items-center gap-1">
          <button
            onClick={selectAll}
            className={cn(
              'rounded-sm border px-2 py-0.5 text-xs transition-colors',
              selectedJoints.length === jointCount
                ? 'border-primary bg-primary text-primary-foreground'
                : 'bg-muted text-muted-foreground hover:border-border border-transparent',
            )}
          >
            All
          </button>
          <button
            onClick={clearAll}
            className={cn(
              'rounded-sm border px-2 py-0.5 text-xs transition-colors',
              selectedJoints.length === 0
                ? 'border-primary bg-primary text-primary-foreground'
                : 'bg-muted text-muted-foreground hover:border-border border-transparent',
            )}
          >
            None
          </button>
          {onOpenDefaults && (
            <button
              onClick={onOpenDefaults}
              className="bg-muted text-muted-foreground hover:border-border rounded-sm border border-transparent px-1.5 py-0.5 text-xs transition-colors"
              aria-label="Edit joint defaults"
              title="Edit joint defaults"
            >
              <Settings className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
        <div className="flex flex-wrap gap-x-3 gap-y-1">
          {visibleGroups.map((group) => (
            <DroppableGroup
              key={group.id}
              groupId={group.id}
              items={group.indices.map((i) => `joint-${i}`)}
            >
              <div data-testid={`joint-group-${group.id}`} className="flex items-center gap-1">
                {renderGroupLabel(group)}
                {renderChips(group.indices)}
              </div>
            </DroppableGroup>
          ))}

          {otherIndices.length > 0 && (
            <DroppableGroup groupId="other" items={otherIndices.map((i) => `joint-${i}`)}>
              <div data-testid="joint-group-other" className="flex items-center gap-1">
                <button
                  onClick={() => toggleGroup(otherIndices)}
                  className={cn(
                    'text-xs font-medium whitespace-nowrap transition-colors',
                    otherIndices.every((i) => selectedJoints.includes(i))
                      ? 'text-foreground'
                      : 'text-muted-foreground hover:text-foreground',
                  )}
                >
                  Other
                </button>
                {renderChips(otherIndices)}
              </div>
            </DroppableGroup>
          )}
        </div>
      </div>

      <DragOverlay>
        {activeIdx !== null && (
          <button
            className="inline-flex items-center gap-1 rounded-sm border border-current px-1.5 py-0.5 text-xs font-medium shadow-lg"
            style={{ color: getJointColor(activeIdx, colors) }}
          >
            <span
              className="h-2 w-2 shrink-0 rounded-full"
              style={{ backgroundColor: getJointColor(activeIdx, colors) }}
            />
            {resolveLabel(activeIdx)}
          </button>
        )}
      </DragOverlay>
    </DndContext>
  )
}
