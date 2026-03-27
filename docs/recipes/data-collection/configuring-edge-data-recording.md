# Configuring Edge Data Recording

Create a recording configuration for ROS 2 edge data capture on NVIDIA Jetson devices. By the end of this recipe, you will have a validated YAML configuration controlling topic selection, compression, episode triggers, and disk monitoring.

> [!NOTE]
> This recipe covers configuration authoring. The recording service that executes these configurations is part of the edge deployment (see [Data Pipeline](../../data-pipeline/README.md)).

## 📋 Prerequisites

| Requirement   | Details                                                                    |
|---------------|----------------------------------------------------------------------------|
| NVIDIA Jetson | JetPack 6.0+ installed                                                     |
| ROS 2         | Humble or later with `rosbag2` packages                                    |
| Storage       | Sufficient disk space for recording sessions (SSD recommended)             |
| IDE           | VS Code or any editor with YAML support (optional: JSON Schema validation) |

## 🚀 Steps

### Step 1: Start from a platform example

The repository includes platform-specific examples. Copy one as your starting point:

```bash
# For a UR10E robotic arm
cp data-pipeline/capture/config/examples/ur10e-6dof-arm.yaml my-recording-config.yaml

# For a mobile manipulator
cp data-pipeline/capture/config/examples/mobile-manipulator.yaml my-recording-config.yaml
```

### Step 2: Configure topics

Edit the `topics` section to match your robot's ROS 2 topic namespace. Each entry specifies the topic name, recording frequency, and compression algorithm:

```yaml
topics:
  - name: /joint_states
    frequency_hz: 100.0
    compression: lz4

  - name: /camera/color/image_raw
    frequency_hz: 30.0
    compression: zstd

  - name: /imu/data
    frequency_hz: 200.0
    compression: lz4
```

Choose compression based on data characteristics:

| Algorithm | Ratio | CPU Overhead | Best For                                  |
|-----------|-------|--------------|-------------------------------------------|
| `none`    | 1x    | 0%           | Debugging, maximum write speed            |
| `lz4`     | 2-3x  | <10%         | High-frequency numeric data (joints, IMU) |
| `zstd`    | 3-5x  | 20-30%       | Images and low-frequency data             |

### Step 3: Configure episode triggers

Set the trigger type that controls episode start/stop:

```yaml
# GPIO trigger — physical button on Jetson GPIO header
trigger:
  type: gpio
  pin: 17
  active_high: true
```

```yaml
# ROS 2 service trigger — start/stop via service calls
trigger:
  type: service
  start_service: /recording/start
  stop_service: /recording/stop
```

```yaml
# Timer trigger — fixed-duration episodes
trigger:
  type: timer
  duration_sec: 60.0
```

### Step 4: Configure disk monitoring

Prevent storage exhaustion during long recording sessions:

```yaml
disk_thresholds:
  warning_percent: 80
  critical_percent: 95
```

The recording service logs a warning at 80% disk usage and stops recording at 95% to prevent data corruption.

### Step 5: Configure gap detection

Detect missing messages that indicate data quality issues:

```yaml
gap_detection:
  threshold_ms: 100.0
  severity: warning
```

A threshold of 100ms balances sensitivity with false positives for most robotic platforms. Lower the threshold for safety-critical applications.

### Step 6: Set the output directory

```yaml
output_dir: /data/recordings
```

Use an SSD-backed path for reliable high-throughput recording.

### Step 7: Enable JSON Schema validation (optional)

Add the schema reference to the first line of your config for IDE autocomplete and inline validation:

```yaml
# yaml-language-server: $schema=./recording_config.schema.json
```

The schema file is at `data-pipeline/capture/config/recording_config.schema.json`.

## ✅ Verify

Validate the configuration against the Pydantic models:

```bash
cd data-pipeline/capture
python -c "
from models.config_models import RecordingConfig
import yaml

with open('../../my-recording-config.yaml') as f:
    config = RecordingConfig(**yaml.safe_load(f))
print(f'Valid config: {len(config.topics)} topics, trigger={config.trigger.type}')
"
```

A successful validation prints the topic count and trigger type without errors.

## ⚙️ Configuration Reference

| Section           | Field              | Type   | Required | Description                                |
|-------------------|--------------------|--------|----------|--------------------------------------------|
| `topics[]`        | `name`             | string | yes      | ROS 2 topic path starting with `/`         |
| `topics[]`        | `frequency_hz`     | float  | yes      | Target recording frequency (0, 1000]       |
| `topics[]`        | `compression`      | string | no       | `none`, `lz4`, or `zstd` (default: `none`) |
| `trigger`         | `type`             | string | yes      | `gpio`, `service`, or `timer`              |
| `disk_thresholds` | `warning_percent`  | int    | no       | Disk usage warning threshold               |
| `disk_thresholds` | `critical_percent` | int    | no       | Disk usage stop threshold                  |
| `gap_detection`   | `threshold_ms`     | float  | no       | Missing message detection threshold        |
| `output_dir`      | —                  | string | no       | Recording output directory                 |

See [Chunking and Compression Configuration](../../data-pipeline/chunking-compression-config.md) for advanced bag splitting options.

## 🔗 Related Recipes

- [Preparing Datasets for Training](preparing-datasets-for-training.md) — process recorded data for training
- [Your First LeRobot Training Job](../training/your-first-lerobot-training-job.md) — train a policy with collected data

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
