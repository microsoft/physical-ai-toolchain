# Data Pipeline

Robot-to-cloud data capture for Physical AI training. Edge devices record ROS 2 topic data during robot operation, validate episode quality, and upload datasets to Azure Blob Storage for training consumption.

## 📁 Directory Structure

```text
data-pipeline/
├── capture/                           # ROS 2 recording service
│   ├── config/                        # Recording configuration and schema
│   │   ├── examples/                  # Platform-specific config examples
│   │   ├── recording_config.yaml      # Default recording configuration
│   │   ├── recording_config.schema.json # JSON Schema for validation
│   │   └── generate_config_schema.py  # Schema generator from Pydantic models
│   ├── models/                        # Pydantic configuration models
│   ├── scripts/                       # Service lifecycle and upload scripts
│   └── tests/                         # Unit tests for config models
├── arc/                               # Arc Kubernetes manifests and policies
├── setup/                             # Arc agent provisioning scripts
├── examples/                          # Example configurations overview
└── specifications/                    # Domain and config specifications
```

## 📋 Specifications

| Document | Description |
|----------|-------------|
| [Data Pipeline](specifications/data-pipeline.specification.md) | Robot-to-cloud capture architecture and edge requirements |
| [Recording Configuration](specifications/recording-config.specification.md) | Configuration schema, validation, and file locations |

## 🏗️ Architecture

| Stage | Location | Description |
|-------|----------|-------------|
| Capture | Edge device | ROS 2 topics recorded to local disk via recording service |
| Validation | Edge device | Gap detection, compression verification |
| Upload | Edge → Cloud | Episode transfer to Azure Blob Storage |
| Registration | Cloud | Dataset catalog entry for training consumption |

## ⚙️ Configuration

Recording behavior is controlled by YAML configuration validated against a JSON Schema. See the [configuration reference](capture/config/README.md) for field definitions and platform examples.
