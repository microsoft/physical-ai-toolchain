# Drift Detection

Algorithms and pipelines for detecting policy performance degradation in deployed robot fleets.

## 📂 Approaches

| Method                 | Description                                                                 |
|------------------------|-----------------------------------------------------------------------------|
| Distribution shift     | Statistical tests on observation/action distributions vs. training baseline |
| Performance regression | Success rate and task completion metric tracking over time                  |
| Latency anomaly        | Inference timing deviation from established baseline                        |

## 🏗️ Architecture

Drift detection consumes telemetry events from Event Hubs, computes statistical metrics against training baselines, and emits drift signals to the alerting pipeline.

## 📋 Status

Planned — awaiting telemetry pipeline and baseline metric storage.
