# Drift Detection

Algorithms and pipelines for detecting policy performance degradation across a fleet of deployed
robots, a core signal source of the fleet-intelligence cognition layer (T5 — Operate). "Fleet" means a
fleet of robots. See [tier-model.md](../../docs/design/tier-model.md) for canonical tier and vocabulary
definitions.

## Status

Planned: placeholder for future implementation. Part of the roadmap **fleet intelligence** layer (T5);
this domain ships 0 Python files and design specs only.

> [!NOTE]
> Drift detection feeds the retraining loop, which is graded on the
> [autonomy ladder (T5.0–T5.3)](../../docs/design/tier-model.md#the-autonomy-ladder-t50t53). Detected
> drift should surface signals to a human (T5.0–T5.1) rather than auto-close the loop: drift detection
> needs statistical power that only exists at fleet scale, and acting on weak signals is a foot-gun.

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
