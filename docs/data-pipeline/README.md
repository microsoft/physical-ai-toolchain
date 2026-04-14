---
sidebar_position: 1
title: Data Pipeline
description: Robot-to-cloud data capture, recording configuration, and Arc agent setup for the Physical AI Toolchain
author: Microsoft Robotics-AI Team
ms.date: 2026-03-14
ms.topic: overview
keywords:
  - data pipeline
  - recording
  - capture
  - arc agent
  - edge recording
  - ros2 bag
---

Robot-to-cloud data capture pipeline for recording, compressing, and uploading robotic training episodes. This section covers recording configuration, edge device setup, and data flow from ROS 2 nodes to Azure storage.

## 📖 Guides

| Guide                                                                    | Description                                                                                       |
|--------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------|
| [ACSA Setup for ROS 2 Bag Sync](acsa-setup.md)                           | Deploy Azure Container Storage for Arc to sync ROS 2 bag files from edge clusters to Blob Storage |
| [Chunking and Compression Configuration](chunking-compression-config.md) | Configure bag chunking thresholds and zstd compression for ROS 2 edge recording on Jetson devices |

## 🏗️ Architecture

```text
data-pipeline/
├── capture/
│   ├── config/          # Recording configuration and schema
│   ├── models/          # Pydantic config models
│   └── tests/           # Config model tests
├── setup/               # Arc agent setup scripts
└── specifications/      # Domain specifications
```
