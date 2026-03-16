# Data Pipeline

Robot-to-cloud data capture architecture for Physical AI training datasets.

## Status

Active — core data ingestion path.

## Components

| Component               | Description                                                        |
|-------------------------|--------------------------------------------------------------------|
| ROS 2 Recording Service | Edge service capturing topic data during robot operation           |
| Arc-enabled Kubernetes  | Azure Arc connectivity for edge cluster management                 |
| Azure Blob Storage      | Cloud destination for recorded episodes                            |
| Recording Configuration | YAML-based schema controlling topic selection and episode triggers |

## Architecture

Data flows from robot sensors through the ROS 2 recording service on Arc-connected edge devices to Azure Blob Storage. Episodes are segmented by configurable triggers (time-based, event-based) and validated for quality gaps before upload.

| Stage        | Location     | Description                                    |
|--------------|--------------|------------------------------------------------|
| Capture      | Edge device  | ROS 2 topics recorded to local disk            |
| Validation   | Edge device  | Gap detection, compression verification        |
| Upload       | Edge → Cloud | Episode transfer to Azure Blob Storage         |
| Registration | Cloud        | Dataset catalog entry for training consumption |

## Edge Requirements

| Requirement        | Value                                     |
|--------------------|-------------------------------------------|
| ROS 2 distribution | Humble or later                           |
| Kubernetes         | Arc-enabled K3s or AKS Edge Essentials    |
| Connectivity       | Intermittent cloud connectivity supported |
| Storage            | Local SSD for recording buffer            |
