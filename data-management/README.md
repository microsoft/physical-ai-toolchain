# Data Management

Dataset curation, storage, annotation, versioning, and the viewer application for the Physical AI Toolchain.

## 📁 Directory Structure

```text
data-management/
├── viewer/                            # Dataset Analysis Tool (FastAPI + React)
├── setup/                             # Viewer deployment scripts
├── tools/                             # CLI tools for dataset operations
├── specifications/                    # Domain specification documents
├── examples/                          # Data management workflow examples
└── README.md                          # This file
```

## 🚀 Quick Start

### Start the viewer locally

```bash
cd data-management/viewer
./start.sh
```

The viewer launches a FastAPI backend on port 8000 and a React frontend on port 5173 with API proxy.

### Run with Docker Compose

```bash
cd data-management/viewer
docker compose up
```

## 📦 Components

| Component | Location | Description |
|-----------|----------|-------------|
| Viewer Backend | `viewer/backend/` | FastAPI REST API for dataset browsing, annotation, export |
| Viewer Frontend | `viewer/frontend/` | React/Vite/TypeScript UI for episode visualization |
| Deploy Script | `setup/` | Kubernetes deployment for hosted viewer |
| CLI Tools | `tools/` | Dataset filtering, splitting, merging, conversion, validation |
| Specifications | `specifications/` | Domain contracts and interface definitions |

## 📖 Documentation

| Guide | Description |
|-------|-------------|
| [Viewer README](viewer/README.md) | Architecture, API reference, auth configuration |
| [Dataset Curation Spec](specifications/dataset-curation.specification.md) | Filtering, splitting, merging, conversion, validation |
| [Viewer Deployment Spec](specifications/viewer-deployment.specification.md) | Kubernetes deployment architecture |

## 🗂️ Dataset Storage

Datasets remain at the repository root in `datasets/` and are not stored within this domain directory. The viewer application reads from local filesystem paths or Azure Blob Storage depending on configuration.
