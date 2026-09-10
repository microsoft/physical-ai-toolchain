import type { OperatorCamera, OperatorMode, OperatorSessionSettings } from '@/api/operator'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
interface OperatorSessionConfigProps {
  cameras: OperatorCamera[]
  disabled: boolean
  mode: OperatorMode
  settings: OperatorSessionSettings
  onChange: (settings: OperatorSessionSettings) => void
}

export function OperatorSessionConfig({
  cameras,
  disabled,
  mode,
  settings,
  onChange,
}: OperatorSessionConfigProps) {
  const updateNumber = (key: keyof OperatorSessionSettings, value: string) => {
    onChange({ ...settings, [key]: Number(value) })
  }

  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <div className="space-y-1">
        <Label htmlFor="operator-control-fps">Control FPS</Label>
        <Input
          id="operator-control-fps"
          aria-label="Control FPS"
          type="number"
          min={1}
          max={120}
          value={settings.controlFps}
          disabled={disabled}
          onChange={(event) => updateNumber('controlFps', event.target.value)}
        />
      </div>
      {cameras.map((camera) => (
        <div key={camera.name} className="space-y-1">
          <Label htmlFor={`operator-camera-${camera.name}`} className="capitalize">
            {camera.name} camera FPS
          </Label>
          <Input
            id={`operator-camera-${camera.name}`}
            aria-label={`${camera.name[0].toUpperCase()}${camera.name.slice(1)} camera FPS`}
            type="number"
            min={1}
            max={60}
            value={settings.cameraFps[camera.name] ?? camera.defaultFps}
            disabled={disabled}
            onChange={(event) =>
              onChange({
                ...settings,
                cameraFps: {
                  ...settings.cameraFps,
                  [camera.name]: Number(event.target.value),
                },
              })
            }
          />
        </div>
      ))}
      <div className="flex items-center gap-2 self-end pb-2 sm:col-span-2">
        <Checkbox
          id="operator-target-clamp"
          aria-label="Follower target clamp"
          checked={settings.maxRelativeTarget !== null}
          disabled={disabled || mode === 'policy'}
          onCheckedChange={(checked) =>
            onChange({ ...settings, maxRelativeTarget: checked ? 2 : null })
          }
        />
        <Label htmlFor="operator-target-clamp">Follower target clamp</Label>
        {settings.maxRelativeTarget !== null && (
          <Input
            aria-label="Follower target clamp degrees"
            type="number"
            min={0.1}
            max={mode === 'policy' ? 5 : 180}
            step={0.1}
            value={settings.maxRelativeTarget}
            disabled={disabled}
            className="ml-auto w-24"
            onChange={(event) =>
              onChange({ ...settings, maxRelativeTarget: Number(event.target.value) })
            }
          />
        )}
      </div>
      {(mode === 'record' || mode === 'policy') && (
        <div className="space-y-1 sm:col-span-2">
          <Label htmlFor="operator-task">Task</Label>
          <Input
            id="operator-task"
            aria-label="Task"
            value={settings.task}
            disabled={disabled}
            onChange={(event) => onChange({ ...settings, task: event.target.value })}
          />
        </div>
      )}
      {mode === 'policy' && (
        <div className="space-y-1 sm:col-span-2">
          <Label htmlFor="operator-rollout-time">Rollout seconds</Label>
          <Input
            id="operator-rollout-time"
            aria-label="Rollout seconds"
            type="number"
            min={1}
            max={300}
            value={settings.rolloutTimeS}
            disabled={disabled}
            onChange={(event) => updateNumber('rolloutTimeS', event.target.value)}
          />
        </div>
      )}
      {mode === 'record' && (
        <>
          <div className="space-y-1">
            <Label htmlFor="operator-destination">Save destination</Label>
            <Select
              value={settings.saveDestination}
              disabled={disabled}
              onValueChange={(value) =>
                onChange({
                  ...settings,
                  saveDestination: value as OperatorSessionSettings['saveDestination'],
                })
              }
            >
              <SelectTrigger id="operator-destination" aria-label="Save destination">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="local">Local</SelectItem>
                <SelectItem value="local_and_hub">Local + Hugging Face</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="operator-dataset-name">Dataset name</Label>
            <Input
              id="operator-dataset-name"
              aria-label="Dataset name"
              value={settings.datasetName}
              disabled={disabled}
              onChange={(event) => onChange({ ...settings, datasetName: event.target.value })}
            />
          </div>
          {settings.saveDestination === 'local_and_hub' && (
            <div className="space-y-1 sm:col-span-2">
              <Label htmlFor="operator-hub-repo">Hugging Face repository</Label>
              <Input
                id="operator-hub-repo"
                aria-label="Hugging Face repository"
                placeholder="owner/dataset"
                value={settings.hubRepoId ?? ''}
                disabled={disabled}
                onChange={(event) =>
                  onChange({ ...settings, hubRepoId: event.target.value || null })
                }
              />
            </div>
          )}
          <div className="space-y-1">
            <Label htmlFor="operator-episodes">Number of episodes</Label>
            <Input
              id="operator-episodes"
              aria-label="Number of episodes"
              type="number"
              min={1}
              max={1000}
              value={settings.numEpisodes}
              disabled={disabled}
              onChange={(event) => updateNumber('numEpisodes', event.target.value)}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="operator-episode-time">Episode seconds</Label>
            <Input
              id="operator-episode-time"
              aria-label="Episode seconds"
              type="number"
              min={1}
              value={settings.episodeTimeS}
              disabled={disabled}
              onChange={(event) => updateNumber('episodeTimeS', event.target.value)}
            />
          </div>
        </>
      )}
    </div>
  )
}
