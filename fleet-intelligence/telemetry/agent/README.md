# Telemetry Agent

On-robot telemetry agent for collecting and forwarding policy execution and health metrics to the cloud telemetry pipeline.

## 📂 Components

| Component | Description                                                   |
|-----------|---------------------------------------------------------------|
| Collector | Gathers policy execution metrics and hardware health data     |
| Formatter | Transforms raw metrics into schema-compliant telemetry events |
| Forwarder | Publishes events to the IoT Operations MQTT broker            |

## ⚙️ Configuration

The agent runs as a sidecar container alongside the inference node on edge devices. Configuration covers topic subscriptions, collection intervals, and MQTT broker endpoints.

## 📋 Status

Planned — awaiting IoT Operations integration and edge deployment pipeline.
