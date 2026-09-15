---
description: Configuration reference for topic recording, episode triggers, disk monitoring, and gap detection
author: Microsoft
ms.date: 2026-09-10
ms.topic: reference
---

# ROS 2 Edge Recording Configuration

Configuration schema for ROS 2 edge recording system controlling topic selection, episode triggers, disk usage monitoring, and data quality detection. Platform engineers prepare these configurations for factory floor operators who deploy them to edge devices.

## 📋 Configuration Files

| File                                                                 | Purpose                                              |
|----------------------------------------------------------------------|------------------------------------------------------|
| [recording_config.yaml](recording_config.yaml)                       | Default configuration for UR10E 6-DOF robotic arm    |
| [recording_config.schema.json](recording_config.schema.json)         | JSON Schema for IDE autocomplete and validation      |
| [examples/mobile-manipulator.yaml](examples/mobile-manipulator.yaml) | Mobile manipulator platform example                  |
| [generate_config_schema.py](generate_config_schema.py)               | Regenerates the JSON Schema from the Pydantic models |

Place configuration files at `config/recording_config.yaml` on edge devices. The recording service validates configuration at startup and exits with descriptive errors if validation fails.

## 🎯 Topics Configuration

Topics define which ROS 2 messages to record during episodes with frequency downsampling and compression settings.

### Field Reference

| Name           | Type   | Default  | Valid Range           | Description                      |
|----------------|--------|----------|-----------------------|----------------------------------|
| `name`         | string | required | ROS 2 topic path      | Topic name starting with `/`     |
| `frequency_hz` | float  | required | (0, 1000]             | Target recording frequency in Hz |
| `compression`  | string | `none`   | `none`, `lz4`, `zstd` | Compression algorithm            |

### Compression Algorithms

| Algorithm | Ratio | CPU Overhead | Use Case                                    |
|-----------|-------|--------------|---------------------------------------------|
| `none`    | 1x    | 0%           | Uncompressed recording, maximum write speed |
| `lz4`     | 2-3x  | <10%         | High-frequency topics (joint states, IMU)   |
| `zstd`    | 3-5x  | 20-30%       | Images and low-frequency data               |

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

Triggers control episode start/stop. Configure one trigger type per recording session.

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

Episodes start when robot reaches target pose within tolerance.

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

Disk monitoring prevents storage exhaustion during recording sessions.

| Name               | Type    | Default | Valid Range | Description                   |
|--------------------|---------|---------|-------------|-------------------------------|
| `warning_percent`  | integer | 80      | [0, 100]    | Warning threshold percentage  |
| `critical_percent` | integer | 95      | [0, 100]    | Critical threshold percentage |

Validation enforces `warning_percent < critical_percent`. Recording system logs warnings when disk usage exceeds `warning_percent` and halts new episodes when usage exceeds `critical_percent`.

```yaml
disk_thresholds:
  warning_percent: 80
  critical_percent: 95
```

## 🔍 Gap Detection

Gap detection identifies missing messages during recording for quality assurance.

| Name           | Type   | Default   | Valid Range                    | Description                             |
|----------------|--------|-----------|--------------------------------|-----------------------------------------|
| `threshold_ms` | float  | 100.0     | >0                             | Gap detection threshold in milliseconds |
| `severity`     | string | `warning` | `warning`, `error`, `critical` | Severity level for gap events           |

The system tracks last message timestamp per topic and flags gaps exceeding `threshold_ms`. Gap events are logged with configured severity and stored in episode metadata for post-processing analysis.

```yaml
gap_detection:
  threshold_ms: 100.0
  severity: warning
```

## 📂 Output Directory

`output_dir` sets the root directory on the edge device where recorded episodes are written.

| Name         | Type   | Default            | Valid Range   | Description                          |
|--------------|--------|--------------------|---------------|--------------------------------------|
| `output_dir` | string | `/data/recordings` | Absolute path | Root directory for recorded episodes |

The path is validated at startup and must be absolute, already exist, be a directory, and be writable by the recording service. The directory is not created for you.

```yaml
output_dir: /data/recordings
```

## 🧩 Top-Level Fields

| Field             | Required | Default                                     |
|-------------------|----------|---------------------------------------------|
| `topics`          | Yes      | —                                           |
| `trigger`         | Yes      | —                                           |
| `disk_thresholds` | No       | `warning_percent: 80, critical_percent: 95` |
| `gap_detection`   | No       | `threshold_ms: 100.0, severity: warning`    |
| `output_dir`      | No       | `/data/recordings`                          |

Unknown top-level fields are rejected. The configuration model sets `extra: "forbid"`, so a misspelled key fails validation rather than being silently ignored.

## 📦 Examples

### UR10E 6-DOF Arm

Default configuration in [recording_config.yaml](recording_config.yaml) records joint states, RGB camera, and IMU data from a Universal Robots UR10E arm with GPIO trigger.

### Mobile Manipulator

Example in [examples/mobile-manipulator.yaml](examples/mobile-manipulator.yaml) demonstrates multi-modal sensing with RGB-D camera, LiDAR, and odometry topics using position-based triggering.

## ✅ Validation

Configuration files are validated using Pydantic models at service startup. Validation is fail-fast: the service exits immediately with descriptive error messages if configuration is invalid.

### Validation Rules

Custom validators raise these messages:

| Rule                 | Error Message Pattern                                        |
|----------------------|--------------------------------------------------------------|
| Topic name format    | `Topic name must start with /: <name>`                       |
| Topic uniqueness     | `Duplicate topic names found: [<names>]`                     |
| Threshold ordering   | `Warning threshold (<n>%) must be less than critical (<m>%)` |
| Array length match   | `Tolerance count (<n>) must match joint index count (<m>)`   |
| Output path absolute | `output_dir must be an absolute path, got: <path>`           |
| Output path exists   | `output_dir does not exist: <path>`                          |
| Output path is a dir | `output_dir is not a directory: <path>`                      |
| Output path writable | `output_dir is not writable: <path>`                         |

Range and enum constraints (`frequency_hz`, `pin`, `warning_percent`, `critical_percent`, `threshold_ms`, `compression`, `severity`) are enforced by Pydantic field constraints and report Pydantic's standard messages, for example `Input should be greater than 0` or `Input should be less than or equal to 1000`.

### JSON Schema Integration

IDE autocomplete and validation are enabled via JSON Schema. Add this directive to the top of configuration files:

```yaml
# yaml-language-server: $schema=./recording_config.schema.json
```

VS Code with the YAML extension (redhat.vscode-yaml) provides inline validation and field suggestions.

### Runtime Example

Run from `data-pipeline/capture/` so that `models` resolves on the import path:

```python
from pathlib import Path
import yaml
from pydantic import ValidationError
from models.config_models import RecordingConfig

config_path = Path("config/recording_config.yaml")
with config_path.open() as f:
    config_data = yaml.safe_load(f)

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

Run these commands from the repository root. The generation script writes to a path relative to the repository root, so running it from another directory writes the schema to the wrong location.

```bash
# Install the pydantic version the schema check uses, then regenerate
uv pip install 'pydantic==2.12.5'
python data-pipeline/capture/config/generate_config_schema.py

# Verify the updated schema
git diff data-pipeline/capture/config/recording_config.schema.json
```

The schema generation script:

1. Imports pydantic models from `models.config_models`
2. Calls `RecordingConfig.model_json_schema()`
3. Writes formatted JSON to `data-pipeline/capture/config/recording_config.schema.json`

### CI/CD Validation

The [validate-config-schema.yml](../../../.github/workflows/validate-config-schema.yml) workflow regenerates the schema and fails the build if the committed file differs from the generated output. If you see a schema drift failure, regenerate the schema using the command above and commit the result.

## 🔗 Related Documentation

* [Preparing Datasets for Training](../../../docs/recipes/data-collection/preparing-datasets-for-training.md) - Dataset structure and LeRobot training handoff
* [AzureML Evaluation Job Debugging](../../../docs/evaluation/azureml-evaluation-job-debugging.md) - Training pipeline integration
