# Hardware-in-the-Loop Evaluation

CPU-only and independently non-commanding validation for the Ubuntu K3s HiL compute plane. Physical motion is not implemented.

## 🚀 Quick Start

Run the local UR10E-shaped no-command gate:

```bash
evaluation/hil/scripts/run-hil-evaluation.sh --mode local \
  --output-dir "$HOME/.local/share/physical-ai-toolchain/results/ur10e-no-command"
```

The run proposes ten deterministic six-axis actions. `NoCommandUr10eAdapter.apply_action()` rejects every action with `NO_COMMAND_TRANSPORT`; the expected applied-action count is zero.

## 📦 Components

| Path                                 | Purpose                                                                      |
|--------------------------------------|------------------------------------------------------------------------------|
| `no_command_runner.py`               | Deterministic observation, proposal, rejection, timing, and artifact runtime |
| `config/ur10e-no-command.json`       | UR10E joint order and no-command safety contract                             |
| `config/ur10e-observations.jsonl`    | Deterministic six-axis observation fixture                                   |
| `workflows/osmo/cpu-smoke.yaml`      | CPU-only OSMO scheduling gate                                                |
| `workflows/osmo/hil-evaluation.yaml` | OSMO no-command gate                                                         |
| `scripts/run-cpu-smoke.sh`           | Submit and wait for the CPU workflow                                         |
| `scripts/run-hil-evaluation.sh`      | Run locally or submit and wait through OSMO                                  |

## 🛡️ Safety Boundary

The implemented adapter contains no RTDE control client, ROS command publisher, serial interface, USB device, CAN interface, host mount, or robot endpoint. The OSMO pool rejects privileged and host-networked workloads.

Do not interpret this pipeline validation as evidence that physical execution is safe. Add physical motion only after defining the exact command owner, robot limits, safe pose, local operator confirmation, and independently verified E-stop procedure.

## 📚 Documentation

- [HiL Evaluation](../../docs/evaluation/hil-evaluation.md)
- [Ubuntu HiL OSMO Backend](../../docs/recipes/tier-3-production/ubuntu-hil-osmo-backend.md)
