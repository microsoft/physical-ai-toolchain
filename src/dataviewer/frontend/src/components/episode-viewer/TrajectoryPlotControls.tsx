import type { JointGroup } from './joint-constants'
import { JOINT_COLORS } from './joint-constants'
import { JointSelector } from './JointSelector'

interface TrajectoryPlotControlsProps {
  jointCount: number
  selectedJoints: number[]
  onSelectJoints: (joints: number[]) => void
  groups: JointGroup[]
  labels: Record<string, string>
  onEditJointLabel: (jointIndex: number, label: string) => void
  onEditGroupLabel: (groupId: string, label: string) => void
  onCreateGroup: (label: string, joints: number[]) => void
  onDeleteGroup: (groupId: string) => void
  onMoveJoint: (jointIndex: number, sourceGroupId: string, targetGroupId: string, toPosition: number) => void
  onOpenDefaults: () => void
  showVelocity: boolean
  onSetShowVelocity: (value: boolean) => void
  showNormalized: boolean
  isNormalizationDisabled: boolean
  onToggleNormalization: () => void
}

export function TrajectoryPlotControls({
  jointCount,
  selectedJoints,
  onSelectJoints,
  groups,
  labels,
  onEditJointLabel,
  onEditGroupLabel,
  onCreateGroup,
  onDeleteGroup,
  onMoveJoint,
  onOpenDefaults,
  showVelocity,
  onSetShowVelocity,
  showNormalized,
  isNormalizationDisabled,
  onToggleNormalization,
}: TrajectoryPlotControlsProps) {
  return (
    <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
      <div
        data-testid="trajectory-joint-selector-scroll"
        className="w-full flex-1 min-w-0 max-h-40 overflow-y-auto pr-2 lg:max-h-32"
      >
        <JointSelector
          jointCount={jointCount}
          selectedJoints={selectedJoints}
          onSelectJoints={onSelectJoints}
          colors={JOINT_COLORS}
          groups={groups}
          labels={labels}
          editable
          onEditJointLabel={onEditJointLabel}
          onEditGroupLabel={onEditGroupLabel}
          onCreateGroup={onCreateGroup}
          onDeleteGroup={onDeleteGroup}
          onMoveJoint={onMoveJoint}
          onOpenDefaults={onOpenDefaults}
        />
      </div>
      <div className="flex w-full shrink-0 flex-wrap items-center gap-2 lg:w-auto lg:justify-end lg:self-start">
        <button
          onClick={() => onSetShowVelocity(false)}
          className={!showVelocity ? 'px-2 py-1 text-xs rounded bg-primary text-primary-foreground' : 'px-2 py-1 text-xs rounded bg-muted text-muted-foreground'}
        >
          Position
        </button>
        <button
          onClick={() => onSetShowVelocity(true)}
          className={showVelocity ? 'px-2 py-1 text-xs rounded bg-primary text-primary-foreground' : 'px-2 py-1 text-xs rounded bg-muted text-muted-foreground'}
        >
          Velocity
        </button>
        <button
          type="button"
          aria-pressed={showNormalized}
          aria-disabled={isNormalizationDisabled}
          disabled={isNormalizationDisabled}
          onClick={onToggleNormalization}
          className={isNormalizationDisabled
            ? 'px-2 py-1 text-xs rounded border transition-colors cursor-not-allowed border-transparent bg-muted text-muted-foreground/60'
            : showNormalized
              ? 'px-2 py-1 text-xs rounded border transition-colors border-primary bg-primary text-primary-foreground'
              : 'px-2 py-1 text-xs rounded border transition-colors border-transparent bg-muted text-muted-foreground hover:border-border'}
        >
          Normalize
        </button>
      </div>
    </div>
  )
}
