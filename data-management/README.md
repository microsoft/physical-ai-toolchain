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

| Component       | Location           | Description                                                   |
|-----------------|--------------------|---------------------------------------------------------------|
| Viewer Backend  | `viewer/backend/`  | FastAPI REST API for dataset browsing, annotation, export     |
| Viewer Frontend | `viewer/frontend/` | React/Vite/TypeScript UI for episode visualization            |
| Deploy Script   | `setup/`           | Kubernetes deployment for hosted viewer                       |
| CLI Tools       | `tools/`           | Dataset filtering, splitting, merging, conversion, validation |
| Specifications  | `specifications/`  | Domain contracts and interface definitions                    |

## 📖 Documentation

| Guide                                                                       | Description                                           |
|-----------------------------------------------------------------------------|-------------------------------------------------------|
| [Viewer README](viewer/README.md)                                           | Architecture, API reference, auth configuration       |
| [Dataset Curation Spec](specifications/dataset-curation.specification.md)   | Filtering, splitting, merging, conversion, validation |
| [Viewer Deployment Spec](specifications/viewer-deployment.specification.md) | Kubernetes deployment architecture                    |

## 🗂️ Dataset Storage

Datasets remain at the repository root in `datasets/` and are not stored within this domain directory. The viewer application reads from local filesystem paths or Azure Blob Storage depending on configuration.

## 🛠️ Build and Deploy Paths

The viewer image follows a two-track build model. Choose the path that matches your trust boundary.

### Inner-loop (local development)

Run the viewer directly against your workstation Python and Node toolchains. No image is produced and nothing is signed; this path is for iteration only and must not be promoted to shared environments.

```bash
cd data-management/viewer
./start.sh
```

For container parity without signing, `docker compose up` builds an unsigned local image and is appropriate only for the developer's own machine.

### Cloud path (shared and staging environments)

Shared and staging clusters consume only signed images produced by `.github/workflows/dataviewer-image-publish.yml`. The workflow builds the image, pushes it to ACR, signs it (cosign keyless by default; Notation + Azure Key Vault when `signing_mode = notation`), and emits SPDX, SLSA, CycloneDX, and OpenVEX attestations. Edge Kyverno policies reject any image that lacks a valid signature on its digest.

`data-management/setup/deploy-dataviewer.sh` no longer builds images. It deploys an existing signed digest produced by the cloud workflow, calling `scripts/security/verify-image.sh` before applying manifests. Pass the digest explicitly:

```bash
./data-management/setup/deploy-dataviewer.sh \
  --image myacr.azurecr.io/dataviewer@sha256:<digest>
```

See [Container Image Signing](../docs/security/container-signing.md) for the full architecture, attestation contract, and admission enforcement.
