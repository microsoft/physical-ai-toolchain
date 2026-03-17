/**
 * Dialog-based editor for global joint configuration defaults.
 *
 * Operates on a local copy of the defaults config — changes are only
 * persisted when the user explicitly clicks Save.
 */

import { AlertTriangle, ArrowRightLeft, Pencil, Plus, Settings, Trash2 } from 'lucide-react'
import { type KeyboardEvent, useEffect, useRef, useState } from 'react'

import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Separator } from '@/components/ui/separator'

import {
  getJointColor,
  JOINT_COLORS,
  JOINT_GROUPS,
  type JointGroup,
  OBSERVATION_LABELS,
} from './joint-constants'

export interface JointConfigDefaultsEditorProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  groups: JointGroup[]
  labels: Record<string, string>
  onSave: (config: { groups: JointGroup[]; labels: Record<string, string> }) => void
  isSaving?: boolean
  colors?: string[]
}

interface DraftJoint {
  id: string
  index: number
  label: string
}

interface DraftGroup {
  id: string
  label: string
  jointIds: string[]
}

function createDraftState(groups: JointGroup[], labels: Record<string, string>) {
  const groupedIndices = new Set(groups.flatMap((group) => group.indices))
  const allIndices = [
    ...new Set([...groups.flatMap((group) => group.indices), ...Object.keys(labels).map(Number)]),
  ].sort((left, right) => left - right)

  const joints: Record<string, DraftJoint> = {}
  const jointIdsByIndex = new Map<number, string>()

  for (const index of allIndices) {
    const jointId = `joint-${index}`
    jointIdsByIndex.set(index, jointId)
    joints[jointId] = {
      id: jointId,
      index,
      label: labels[String(index)] ?? OBSERVATION_LABELS[index] ?? `Ch ${index}`,
    }
  }

  const draftGroups: DraftGroup[] = groups.map((group) => ({
    id: group.id,
    label: group.label,
    jointIds: group.indices
      .map((index) => jointIdsByIndex.get(index))
      .filter((jointId): jointId is string => Boolean(jointId)),
  }))

  const groupedJointIds = new Set(draftGroups.flatMap((group) => group.jointIds))
  for (const index of allIndices) {
    if (groupedIndices.has(index)) {
      continue
    }

    const jointId = jointIdsByIndex.get(index)
    if (jointId && !groupedJointIds.has(jointId)) {
      groupedJointIds.add(jointId)
    }
  }

  return { groups: draftGroups, joints }
}

function createBuiltInLabels() {
  const builtInLabels: Record<string, string> = {}
  for (const [index, label] of Object.entries(OBSERVATION_LABELS)) {
    builtInLabels[String(index)] = label
  }
  return builtInLabels
}

function buildPersistedConfig(groups: DraftGroup[], joints: Record<string, DraftJoint>) {
  const nextLabels: Record<string, string> = {}
  for (const joint of Object.values(joints)) {
    nextLabels[String(joint.index)] = joint.label
  }

  return {
    groups: groups.map((group) => ({
      id: group.id,
      label: group.label,
      indices: group.jointIds.map((jointId) => joints[jointId].index),
    })),
    labels: nextLabels,
  }
}

function InlineEditField({
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
    inputRef.current?.focus()
    inputRef.current?.select()
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
      onBlur={() => {
        const trimmed = text.trim()
        if (trimmed && trimmed !== value) onCommit(trimmed)
        else onCancel()
      }}
      className="border-b border-primary bg-transparent px-1 text-sm outline-none"
    />
  )
}

function IndexEditField({
  value,
  onCommit,
  onCancel,
}: {
  value: number
  onCommit: (val: number) => void
  onCancel: () => void
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [num, setNum] = useState(String(value))

  useEffect(() => {
    inputRef.current?.focus()
    inputRef.current?.select()
  }, [])

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      const parsed = parseInt(num, 10)
      if (!isNaN(parsed) && parsed >= 0) onCommit(parsed)
      else onCancel()
    } else if (e.key === 'Escape') {
      e.preventDefault()
      onCancel()
    }
  }

  return (
    <input
      ref={inputRef}
      type="number"
      min={0}
      value={num}
      onChange={(e) => setNum(e.target.value)}
      onKeyDown={handleKeyDown}
      onBlur={() => {
        const parsed = parseInt(num, 10)
        if (!isNaN(parsed) && parsed >= 0) onCommit(parsed)
        else onCancel()
      }}
      className="w-10 border-b border-primary bg-transparent text-center text-[10px] outline-none"
    />
  )
}

let _groupCounter = 0

export function JointConfigDefaultsEditor({
  open,
  onOpenChange,
  groups: initialGroups,
  labels: initialLabels,
  onSave,
  isSaving,
  colors = JOINT_COLORS,
}: JointConfigDefaultsEditorProps) {
  const [groups, setGroups] = useState<DraftGroup[]>(
    () => createDraftState(initialGroups, initialLabels).groups,
  )
  const [joints, setJoints] = useState<Record<string, DraftJoint>>(
    () => createDraftState(initialGroups, initialLabels).joints,
  )
  const [editingJoint, setEditingJoint] = useState<string | null>(null)
  const [editingGroup, setEditingGroup] = useState<string | null>(null)
  const [assigningJoint, setAssigningJoint] = useState<string | null>(null)
  const [movingJoint, setMovingJoint] = useState<string | null>(null)
  const [editingIndex, setEditingIndex] = useState<string | null>(null)
  const groupRefs = useRef(new Map<string, HTMLDivElement>())

  // Reset local state when dialog opens with new props
  useEffect(() => {
    if (open) {
      const nextDraftState = createDraftState(initialGroups, initialLabels)
      setGroups(nextDraftState.groups)
      setJoints(nextDraftState.joints)
      setEditingJoint(null)
      setEditingGroup(null)
      setAssigningJoint(null)
      setMovingJoint(null)
      setEditingIndex(null)
    }
  }, [open, initialGroups, initialLabels])

  useEffect(() => {
    if (!editingGroup) {
      return
    }

    const groupElement = groupRefs.current.get(editingGroup)
    groupElement?.scrollIntoView({ block: 'nearest' })
  }, [editingGroup, groups])

  const groupedJointIds = new Set(groups.flatMap((group) => group.jointIds))
  const ungroupedJointIds = Object.values(joints)
    .filter((joint) => !groupedJointIds.has(joint.id))
    .sort((left, right) => left.index - right.index)
    .map((joint) => joint.id)

  const duplicateIndices = Object.entries(
    Object.values(joints).reduce<Record<number, number>>((counts, joint) => {
      counts[joint.index] = (counts[joint.index] ?? 0) + 1
      return counts
    }, {}),
  )
    .filter(([, count]) => count > 1)
    .map(([index]) => Number(index))
    .sort((left, right) => left - right)

  const hasDuplicateIndices = duplicateIndices.length > 0

  const handleEditJointLabel = (jointId: string, label: string) => {
    setJoints((prev) => ({ ...prev, [jointId]: { ...prev[jointId], label } }))
    setEditingJoint(null)
  }

  const handleEditGroupLabel = (groupId: string, label: string) => {
    setGroups((prev) => prev.map((g) => (g.id === groupId ? { ...g, label } : g)))
    setEditingGroup(null)
  }

  const handleDeleteGroup = (groupId: string) => {
    setGroups((prev) => prev.filter((g) => g.id !== groupId))
  }

  const handleAddGroup = () => {
    _groupCounter++
    const newGroup: DraftGroup = {
      id: `custom-${Date.now()}-${_groupCounter}`,
      label: 'New Group',
      jointIds: [],
    }
    setGroups((prev) => [...prev, newGroup])
    setEditingGroup(newGroup.id)
  }

  const handleAssignJoint = (jointId: string, groupId: string) => {
    setGroups((prev) =>
      prev.map((g) => {
        if (g.id === groupId)
          return { ...g, jointIds: [...g.jointIds.filter((id) => id !== jointId), jointId] }
        return { ...g, jointIds: g.jointIds.filter((id) => id !== jointId) }
      }),
    )
    setAssigningJoint(null)
  }

  const handleUnassignJoint = (jointId: string) => {
    setGroups((prev) =>
      prev.map((g) => ({
        ...g,
        jointIds: g.jointIds.filter((id) => id !== jointId),
      })),
    )
  }

  const handleMoveJoint = (jointId: string, toGroupId: string) => {
    setGroups((prev) =>
      prev.map((g) => {
        if (g.id === toGroupId)
          return { ...g, jointIds: [...g.jointIds.filter((id) => id !== jointId), jointId] }
        return { ...g, jointIds: g.jointIds.filter((id) => id !== jointId) }
      }),
    )
    setMovingJoint(null)
  }

  const handleEditIndex = (jointId: string, newIdx: number) => {
    setJoints((prev) => ({ ...prev, [jointId]: { ...prev[jointId], index: newIdx } }))
    setEditingIndex(null)
  }

  const handleSave = () => {
    if (hasDuplicateIndices) {
      return
    }

    onSave(buildPersistedConfig(groups, joints))
  }

  const handleCancel = () => {
    onOpenChange(false)
  }

  const handleReset = () => {
    const nextDraftState = createDraftState(JOINT_GROUPS, createBuiltInLabels())
    setGroups(nextDraftState.groups)
    setJoints(nextDraftState.joints)
    setEditingJoint(null)
    setEditingGroup(null)
    setAssigningJoint(null)
    setMovingJoint(null)
    setEditingIndex(null)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[80vh] max-w-2xl flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Settings className="h-5 w-5" />
            Joint Configuration Defaults
          </DialogTitle>
          <DialogDescription>
            Edit the default joint names and groupings applied to new datasets.
          </DialogDescription>
        </DialogHeader>

        {hasDuplicateIndices && (
          <Alert variant="destructive" className="mb-4">
            <AlertTriangle className="h-4 w-4" />
            <AlertDescription>
              One or more joint labels now share the same index. Fix duplicate indices before
              saving.
            </AlertDescription>
          </Alert>
        )}

        <div data-testid="joint-config-scroll-area" className="min-h-0 flex-1 overflow-y-auto">
          <div className="flex flex-col gap-4 py-2 pr-4">
            {groups.map((group) => (
              <div
                key={group.id}
                ref={(element) => {
                  if (element) {
                    groupRefs.current.set(group.id, element)
                    return
                  }

                  groupRefs.current.delete(group.id)
                }}
                className="rounded-lg border p-3"
              >
                <div className="mb-2 flex items-center justify-between">
                  {editingGroup === group.id ? (
                    <InlineEditField
                      value={group.label}
                      onCommit={(val) => handleEditGroupLabel(group.id, val)}
                      onCancel={() => setEditingGroup(null)}
                    />
                  ) : (
                    <span className="text-sm font-semibold">{group.label}</span>
                  )}
                  <div className="flex items-center gap-1">
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-6 w-6"
                      aria-label="Edit group label"
                      onClick={() => setEditingGroup(group.id)}
                    >
                      <Pencil className="h-3 w-3" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-6 w-6 text-destructive"
                      aria-label="Delete group"
                      onClick={() => handleDeleteGroup(group.id)}
                    >
                      <Trash2 className="h-3 w-3" />
                    </Button>
                  </div>
                </div>

                <div className="flex flex-wrap gap-1.5">
                  {group.jointIds.map((jointId) => {
                    const joint = joints[jointId]
                    return (
                      <div
                        key={jointId}
                        className="border-current/20 group/chip inline-flex items-center gap-1 rounded border py-0.5 pl-1.5 pr-0.5 text-xs"
                        style={{ color: getJointColor(joint.index, colors) }}
                      >
                        {editingIndex === jointId ? (
                          <IndexEditField
                            value={joint.index}
                            onCommit={(val) => handleEditIndex(jointId, val)}
                            onCancel={() => setEditingIndex(null)}
                          />
                        ) : (
                          <button
                            data-testid="joint-index"
                            className="bg-current/10 hover:bg-current/20 cursor-pointer rounded px-1 font-mono text-[10px]"
                            aria-label="Edit joint index"
                            onClick={() => setEditingIndex(jointId)}
                          >
                            {joint.index}
                          </button>
                        )}
                        <span
                          data-joint-color
                          className="h-2 w-2 flex-shrink-0 rounded-full"
                          style={{ backgroundColor: getJointColor(joint.index, colors) }}
                        />
                        {editingJoint === jointId ? (
                          <InlineEditField
                            value={joint.label}
                            onCommit={(val) => handleEditJointLabel(jointId, val)}
                            onCancel={() => setEditingJoint(null)}
                          />
                        ) : (
                          <span>{joint.label}</span>
                        )}
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-4 w-4 opacity-0 transition-opacity group-hover/chip:opacity-100"
                          aria-label="Edit joint label"
                          onClick={() => setEditingJoint(jointId)}
                        >
                          <Pencil className="h-2.5 w-2.5" />
                        </Button>
                        {movingJoint === jointId ? (
                          <div data-testid="group-picker" className="flex gap-1">
                            {groups
                              .filter((g) => g.id !== group.id)
                              .map((g) => (
                                <Button
                                  key={g.id}
                                  variant="outline"
                                  size="sm"
                                  className="h-5 px-1.5 text-[10px]"
                                  onClick={() => handleMoveJoint(jointId, g.id)}
                                >
                                  {g.label}
                                </Button>
                              ))}
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-5 px-1 text-[10px]"
                              onClick={() => setMovingJoint(null)}
                            >
                              ✕
                            </Button>
                          </div>
                        ) : (
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-4 w-4 opacity-0 transition-opacity group-hover/chip:opacity-100"
                            aria-label="Move to group"
                            onClick={() => setMovingJoint(jointId)}
                          >
                            <ArrowRightLeft className="h-2.5 w-2.5" />
                          </Button>
                        )}
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-4 w-4 opacity-0 transition-opacity group-hover/chip:opacity-100"
                          aria-label="Remove joint from group"
                          onClick={() => handleUnassignJoint(jointId)}
                        >
                          <Trash2 className="h-2.5 w-2.5" />
                        </Button>
                      </div>
                    )
                  })}
                  {group.jointIds.length === 0 && (
                    <span className="text-xs italic text-muted-foreground">No joints assigned</span>
                  )}
                </div>
              </div>
            ))}

            {ungroupedJointIds.length > 0 && (
              <>
                <Separator />
                <div data-testid="ungrouped-joints" className="rounded-lg border border-dashed p-3">
                  <span className="mb-2 block text-sm font-semibold text-muted-foreground">
                    Ungrouped Joints
                  </span>
                  <div className="flex flex-wrap gap-1.5">
                    {ungroupedJointIds.map((jointId) => {
                      const joint = joints[jointId]
                      return (
                        <div
                          key={jointId}
                          className="border-current/20 group/chip inline-flex items-center gap-1 rounded border py-0.5 pl-1.5 pr-0.5 text-xs"
                          style={{ color: getJointColor(joint.index, colors) }}
                        >
                          {editingIndex === jointId ? (
                            <IndexEditField
                              value={joint.index}
                              onCommit={(val) => handleEditIndex(jointId, val)}
                              onCancel={() => setEditingIndex(null)}
                            />
                          ) : (
                            <button
                              data-testid="joint-index"
                              className="bg-current/10 hover:bg-current/20 cursor-pointer rounded px-1 font-mono text-[10px]"
                              aria-label="Edit joint index"
                              onClick={() => setEditingIndex(jointId)}
                            >
                              {joint.index}
                            </button>
                          )}
                          <span
                            data-joint-color
                            className="h-2 w-2 flex-shrink-0 rounded-full"
                            style={{ backgroundColor: getJointColor(joint.index, colors) }}
                          />
                          {editingJoint === jointId ? (
                            <InlineEditField
                              value={joint.label}
                              onCommit={(val) => handleEditJointLabel(jointId, val)}
                              onCancel={() => setEditingJoint(null)}
                            />
                          ) : (
                            <span>{joint.label}</span>
                          )}
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-4 w-4 opacity-0 transition-opacity group-hover/chip:opacity-100"
                            aria-label="Edit joint label"
                            onClick={() => setEditingJoint(jointId)}
                          >
                            <Pencil className="h-2.5 w-2.5" />
                          </Button>
                          {assigningJoint === jointId ? (
                            <div className="flex gap-1">
                              {groups.map((g) => (
                                <Button
                                  key={g.id}
                                  variant="outline"
                                  size="sm"
                                  className="h-5 px-1.5 text-[10px]"
                                  onClick={() => handleAssignJoint(jointId, g.id)}
                                >
                                  {g.label}
                                </Button>
                              ))}
                            </div>
                          ) : (
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-4 w-4 opacity-0 transition-opacity group-hover/chip:opacity-100"
                              aria-label="Assign to group"
                              onClick={() => setAssigningJoint(jointId)}
                            >
                              <Plus className="h-2.5 w-2.5" />
                            </Button>
                          )}
                        </div>
                      )
                    })}
                  </div>
                </div>
              </>
            )}
          </div>
        </div>

        <DialogFooter className="flex items-center justify-between gap-2 pt-4">
          <Button variant="outline" size="sm" onClick={handleReset}>
            Reset
          </Button>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={handleAddGroup}>
              <Plus className="mr-1 h-3.5 w-3.5" />
              Add Group
            </Button>
            <Button variant="ghost" size="sm" onClick={handleCancel}>
              Cancel
            </Button>
            <Button size="sm" onClick={handleSave} disabled={isSaving || hasDuplicateIndices}>
              {isSaving ? 'Saving…' : 'Save'}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
