---
description: Configuration reference for topic recording, episode triggers, disk monitoring, and gap detection
author: Microsoft
ms.date: 2026-09-19
ms.topic: reference
---

# ROS 2 Edge Recording Configuration

Configuration schema and Pydantic models for proposed ROS 2 edge recording settings. The repository validates configuration values; it does not implement a recording service, automatic triggers, frequency downsampling, per-topic compression, disk monitoring, or gap detection.

## 📋 Configuration Files

| File                                                                 | Purpose                                           |
|----------------------------------------------------------------------|---------------------------------------------------|
| [recording_config.yaml](recording_config.yaml)                       | Default configuration for UR10E 6-DOF robotic arm |
| [recording_config.schema.json](recording_config.schema.json)         | JSON Schema for IDE autocomplete and validation   |
| [examples/mobile-manipulator.yaml](examples/mobile-manipulator.yaml) | Mobile manipulator platform example               |

These files are repository-specific configuration examples, not native `ros2 bag record` input. Native recording uses explicit topic arguments and a separate MCAP writer YAML via `--storage-config-file`; see [Chunking and Compression Configuration](../../../docs/data-pipeline/chunking-compression-config.md). A future consumer must load and validate these settings explicitly.

## 🎯 Topics Configuration

Topics describe intended message selection, target frequency, and compression. Validating these fields does not apply them to ROS 2 recording.

### Field Reference

| Name           | Type   | Default  | Valid Range           | Description                      |
|----------------|--------|----------|-----------------------|----------------------------------|
| `name`         | string | required | ROS 2 topic path      | Topic name starting with `/`     |
| `frequency_hz` | float  | required | (0, 1000]             | Target recording frequency in Hz |
| `compression`  | string | `none`   | `none`, `lz4`, `zstd` | Compression algorithm            |

### Compression Choices

| Value  | Intended choice                   |
|--------|-----------------------------------|
| `none` | No compression                    |
| `lz4`  | Low-latency compression candidate |
| `zstd` | Higher compression candidate      |

Compression ratio and CPU use depend on payload, codec settings, and hardware. Measure them on representative recordings; these schema values neither configure the MCAP writer nor guarantee a performance level.

### Example

```yaml
topics:
  - name: /joint_states
    frequency_hz: 100.0
    compression: lz4

  - name: /camera/color/image_raw
    frequency_hz: 30.0
    compression: zstd
```

## 🎬 Trigger Configuration

The schema accepts one trigger configuration per recording session. No trigger listener or episode start/stop implementation is provided.

### GPIO Trigger

Hardware button or switch connected to GPIO pin.

| Name          | Type    | Default  | Valid Range     | Description                           |
|---------------|---------|----------|-----------------|---------------------------------------|
| `type`        | string  | required | `gpio`          | Trigger discriminator                 |
| `pin`         | integer | required | [0, 27]         | GPIO pin number (BCM numbering)       |
| `active_high` | boolean | `true`   | `true`, `false` | Trigger on HIGH if true, LOW if false |

```yaml
trigger:
  type: gpio
  pin: 17
  active_high: true
```

### Position Trigger

Position-trigger settings describe joint indices and tolerances. The schema checks matching array lengths, not robot motion or target-pose detection.

| Name            | Type           | Default  | Valid Range  | Description                                      |
|-----------------|----------------|----------|--------------|--------------------------------------------------|
| `type`          | string         | required | `position`   | Trigger discriminator                            |
| `joint_indices` | array[integer] | required | min length 1 | Joint indices to monitor                         |
| `tolerances`    | array[float]   | required | min length 1 | Position tolerance per joint (radians or meters) |

Array lengths must match. Validation fails if `tolerances` count differs from `joint_indices` count.

```yaml
trigger:
  type: position
  joint_indices: [0, 1, 2, 3, 4, 5]
  tolerances: [0.01, 0.01, 0.01, 0.01, 0.01, 0.01]
```

### VR Trigger

VR controller button for demonstration recording workflows.

| Name         | Type   | Default  | Valid Range                               | Description            |
|--------------|--------|----------|-------------------------------------------|------------------------|
| `type`       | string | required | `vr`                                      | Trigger discriminator  |
| `controller` | string | required | `left`, `right`                           | VR controller side     |
| `button`     | string | required | `trigger`, `grip`, `primary`, `secondary` | Button name to monitor |

```yaml
trigger:
  type: vr
  controller: right
  button: trigger
```

## 💾 Disk Usage Thresholds

Disk thresholds express intended monitoring limits; they do not install or run a disk monitor.

| Name               | Type    | Default | Valid Range | Description                   |
|--------------------|---------|---------|-------------|-------------------------------|
| `warning_percent`  | integer | 80      | [0, 100]    | Warning threshold percentage  |
| `critical_percent` | integer | 95      | [0, 100]    | Critical threshold percentage |

Validation enforces `warning_percent < critical_percent`. Warning logs and recording shutdown require a separate runtime implementation; these values alone do not prevent disk exhaustion.

```yaml
disk_thresholds:
  warning_percent: 80
  critical_percent: 95
```

## 🔍 Gap Detection

Gap-detection settings describe intended quality checks for missing messages.

| Name           | Type   | Default   | Valid Range                    | Description                             |
|----------------|--------|-----------|--------------------------------|-----------------------------------------|
| `threshold_ms` | float  | 100.0     | >0                             | Gap detection threshold in milliseconds |
| `severity`     | string | `warning` | `warning`, `error`, `critical` | Severity level for gap events           |

The model validates the positive threshold and allowed severity. Timestamp tracking, event logging, and episode metadata persistence are not implemented by this configuration package.

```yaml
gap_detection:
  threshold_ms: 100.0
  severity: warning
```

## 📦 Examples

### UR10E 6-DOF Arm

Default configuration in [recording_config.yaml](recording_config.yaml) describes joint states, RGB camera, and IMU topics for a Universal Robots UR10E arm with a proposed GPIO trigger.

### Mobile Manipulator

Example in [examples/mobile-manipulator.yaml](examples/mobile-manipulator.yaml) demonstrates multi-modal sensing with RGB-D camera, LiDAR, and odometry topics using position-based triggering.

## ✅ Validation

Call `RecordingConfig.model_validate()` to validate a loaded mapping. Invalid input raises `ValidationError`; there is no repository-owned recording-service startup hook. The `output_dir` must be an absolute path to an existing writable directory on the machine doing validation.

### Validation Rules

| Rule               | Error Message Pattern                                        |
|--------------------|--------------------------------------------------------------|
| Topic name format  | `Topic name must start with /: <name>`                       |
| Topic uniqueness   | `Duplicate topic names found: [<names>]`                     |
| Frequency range    | Pydantic numeric constraints: greater than 0, at most 1000   |
| Threshold ordering | `Warning threshold (<n>%) must be less than critical (<m>%)` |
| Array length match | `Tolerance count (<n>) must match joint index count (<m>)`   |

### JSON Schema Integration

IDE autocomplete and validation are enabled via JSON Schema. Add this directive to the top of configuration files:

```yaml
# yaml-language-server: $schema=./recording_config.schema.json
```

VS Code with the YAML extension (redhat.vscode-yaml) provides inline validation and field suggestions.

### Model Validation Example

Run with the `data-pipeline` Python 3.12+ environment and `data-pipeline/capture/` on the Python import path (for example, as the working directory). This mapping represents loaded configuration; it does not launch recording. Create the output directory separately before validation.

```python
from __future__ import annotations

from pydantic import ValidationError

from models.config_models import RecordingConfig

config_data = {
    "topics": [{"name": "/joint_states", "frequency_hz": 100.0}],
    "trigger": {"type": "gpio", "pin": 17},
    "output_dir": "/data/recordings",
}

try:
    config = RecordingConfig.model_validate(config_data)
except ValidationError as exc:
    for error in exc.errors():
        loc = " -> ".join(str(x) for x in error["loc"])
        print(f"[{loc}] {error['msg']}")
    raise SystemExit(1)
```

## 🔧 Schema Maintenance

The JSON Schema (`recording_config.schema.json`) is a **derived artifact** generated from pydantic models in `data-pipeline/capture/models/config_models.py`.

### When to Regenerate Schema

Regenerate the schema whenever you modify:

* Configuration field names or types
* Validation constraints (min/max values, string patterns)
* Field descriptions or documentation
* Trigger types or their parameters

### How to Regenerate Schema

```bash
# Run from the repository root using the data-pipeline lock
uv run --project data-pipeline --frozen python data-pipeline/capture/config/generate_config_schema.py

# Verify the updated schema
git diff -- data-pipeline/capture/config/recording_config.schema.json
```

The schema generation script:

1. Adds `data-pipeline/capture/` to its import path and imports `models.config_models`
2. Calls `RecordingConfig.model_json_schema()`
3. Writes formatted JSON to `data-pipeline/capture/config/recording_config.schema.json`

### Validation Limits

Review the generated schema diff and run the existing capture tests after model changes. JSON Schema describes field constraints, but Python validators additionally check relationships and the local output directory; IDE validation alone does not establish runtime readiness.

## 🔗 Related Documentation

* [LeRobot Training](../../../docs/training/lerobot-training.md) - Dataset and policy training guidance
* [AzureML Evaluation Job Debugging](../../../docs/evaluation/azureml-evaluation-job-debugging.md) - Training pipeline integration
