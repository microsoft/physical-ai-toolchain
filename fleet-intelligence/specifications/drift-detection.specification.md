# Drift Detection

Algorithms and pipelines for detecting policy performance degradation in deployed robot fleets.

## Status

Planned — placeholder for future implementation.

## Components

| Component                      | Description                                                                       |
|--------------------------------|-----------------------------------------------------------------------------------|
| Distribution Shift Detector    | Statistical tests comparing observation/action distributions to training baseline |
| Performance Regression Monitor | Task success rate and completion metric tracking                                  |
| Latency Anomaly Detector       | Inference timing deviation from established operating baseline                    |
| Baseline Store                 | Training-time metric distributions used as comparison reference                   |
| Signal Aggregator              | Combines multiple drift indicators into composite drift score                     |

## Detection Methods

| Method                 | Metric                                          | Technique                              |
|------------------------|-------------------------------------------------|----------------------------------------|
| Action distribution    | Action vector norms and component distributions | KL divergence, Kolmogorov-Smirnov test |
| Observation statistics | Input feature means and variances               | CUSUM, exponential moving average      |
| Performance tracking   | Task success rate, episode duration             | Sliding window regression              |
| Latency monitoring     | Inference time percentiles                      | Threshold breach counting              |
