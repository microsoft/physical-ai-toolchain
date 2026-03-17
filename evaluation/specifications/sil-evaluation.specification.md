# SiL Evaluation Specification

Software-in-the-loop (SiL) evaluation validates trained policies in simulation.

## Scope

- Policy evaluation against success metrics in Isaac Sim
- Checkpoint monitoring and continuous evaluation
- LeRobot replay-based evaluation against ground truth
- Trajectory visualization and metric aggregation

## Inputs

- Trained model checkpoint or exported policy (ONNX/TorchScript)
- Isaac Sim environment configuration
- Evaluation parameters (episodes, thresholds)

## Outputs

- Success rate and reward metrics
- Trajectory plots and comparison visualizations
- MLflow run with logged metrics and artifacts
