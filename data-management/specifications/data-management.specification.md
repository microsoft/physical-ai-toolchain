# Data Management

Domain contracts for dataset storage, formats, versioning, and access patterns in the Physical AI Toolchain.

## Dataset Formats

| Format  | Extension | Use Case                                                         |
|---------|-----------|------------------------------------------------------------------|
| HDF5    | `.hdf5`   | Dense episode storage with frame data, actions, and observations |
| LeRobot | directory | Hugging Face LeRobot-compatible dataset structure                |
| Raw     | directory | Unprocessed sensor recordings with metadata sidecar files        |

## Storage Backends

| Backend            | Protocol   | Configuration                                              |
|--------------------|------------|------------------------------------------------------------|
| Local filesystem   | File path  | `DATASETS_PATH` environment variable                       |
| Azure Blob Storage | `wasbs://` | Storage account, container, and credential via environment |
| Hugging Face Hub   | HTTPS      | Repository ID and optional token                           |

## Directory Layout

Datasets follow a consistent structure regardless of format:

```text
<dataset-name>/
├── meta/
│   ├── info.json                      # Schema version, format, episode count
│   └── tasks.json                     # Task definitions and parameters
├── data/                              # Episode data (format-specific)
└── annotations/                       # Optional annotation overlays
```

## Versioning

Dataset versions use the naming convention `<project>--<dataset>--<timestamp>` where the timestamp follows `YYYY_MM_DD_HH_MM_SS` format. Each version is an immutable snapshot.

## Access Patterns

- Read-only access for training and inference workloads
- Write access limited to data collection pipelines and annotation tools
- The viewer application provides read access with optional annotation writes
- CLI tools operate on local copies and produce new versioned outputs
