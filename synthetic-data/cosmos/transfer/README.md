# Cosmos Transfer 2.5

Sim-to-real image transformation using NVIDIA Cosmos Transfer 2.5. Converts simulation-rendered frames from Isaac Sim into photorealistic images suitable for training real-world robot policies.

## Capabilities

| Capability        | Description                                                           |
|-------------------|-----------------------------------------------------------------------|
| Style transfer    | Transform simulation renders to match real-world visual distributions |
| Domain adaptation | Bridge the sim-to-real gap for visual policy training                 |
| Batch processing  | Process full episode frame sequences                                  |

## Integration Points

| Component         | Description                                  |
|-------------------|----------------------------------------------|
| Isaac Sim output  | Rendered frames from simulation environments |
| Training pipeline | Photorealistic frames fed to RL/IL training  |
| Cosmos Predict    | Output frames can chain to future prediction |

## References

- [Cosmos Transfer 2.5](https://github.com/NVIDIA/Cosmos-Transfer2.5)
- [NVIDIA Cosmos Platform](https://developer.nvidia.com/cosmos)
