# Cosmos Reason 2

Data curation and quality assessment using NVIDIA Cosmos Reason 2. Evaluates synthetic data for training suitability, filtering low-quality samples and annotating data with reasoning metadata.

## Capabilities

| Capability            | Description                                     |
|-----------------------|-------------------------------------------------|
| Quality assessment    | Score synthetic frames for training suitability |
| Data filtering        | Remove low-quality or unrealistic samples       |
| Reasoning annotations | Attach quality metadata to curated datasets     |

## Integration Points

| Component              | Description                                      |
|------------------------|--------------------------------------------------|
| Cosmos Transfer output | Assess quality of sim-to-real transformed frames |
| Cosmos Predict output  | Validate predicted frame plausibility            |
| Training pipeline      | Curated dataset consumed by RL/IL training       |

## References

- [NVIDIA Cosmos Platform](https://developer.nvidia.com/cosmos)
- [Cosmos Cookbook](https://github.com/NVIDIA/Cosmos-Cookbook)
