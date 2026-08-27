# Hardware-in-the-Loop Evaluation

CPU-only and independently non-commanding validation for the Ubuntu K3s HiL compute plane. Physical motion is not implemented.

## 🚀 Quick Start

After `02-connect-osmo-backend.sh` reports a connection receipt, run the two independent validation milestones:

```bash
data-pipeline/setup/hil/03-run-cpu-smoke.sh \
  --connection-file <connection-receipt>

data-pipeline/setup/hil/04-run-no-command-check.sh \
  --connection-file <connection-receipt>
```

The CPU proof requests zero GPUs and must complete on the owned local node. The no-command proof proposes ten deterministic six-axis actions. `NoCommandUr10eAdapter.apply_action()` rejects every action with `NO_COMMAND_TRANSPORT`; the expected applied-action count is zero.

## 📤 Result Durability

The no-command workflow declares a unique `azure://<account>/<container>/workflows/data/hil/no-command/<workflow>` output URI and writes through OSMO's `{{output}}` directory. The Arc-connected K3s runtime controller uses the existing OSMO user-assigned managed identity through the `osmo-workflow` ServiceAccount.

The check queries the completed OSMO task upload record, downloads the declared URI with `osmo data download`, and verifies the exact artifact set, manifest bytes and digests, safety summary, and absence of credential-shaped content.

Static checks validate the workflow and artifact contract. Live Arc federation, runtime upload, and OSMO download validation require the target environment. Do not add a SAS, storage key, direct uploader, or alternate output path.

## 📦 Components

| Path                                                 | Purpose                                                                      |
|------------------------------------------------------|------------------------------------------------------------------------------|
| `no_command_runner.py`                               | Deterministic observation, proposal, rejection, timing, and artifact runtime |
| `config/ur10e-no-command.json`                       | UR10E joint order and no-command safety contract                             |
| `config/ur10e-observations.jsonl`                    | Deterministic six-axis observation fixture                                   |
| `workflows/osmo/cpu-smoke.yaml`                      | CPU-only OSMO scheduling gate                                                |
| `workflows/osmo/hil-evaluation.yaml`                 | OSMO no-command gate                                                         |
| `data-pipeline/setup/hil/03-run-cpu-smoke.sh`        | Public CPU scheduling milestone                                              |
| `data-pipeline/setup/hil/04-run-no-command-check.sh` | Public no-command safety milestone                                           |

## 🛡️ Safety Boundary

The implemented adapter contains no RTDE control client, ROS command publisher, serial interface, USB device, CAN interface, host mount, or robot endpoint. The OSMO pool rejects privileged and host-networked workloads.

The CPU and no-command proofs complete this journey. No command transport or physical motion is supported.

## 📚 Documentation

- [HiL Evaluation](../../docs/evaluation/hil-evaluation.md)
- [Ubuntu HiL OSMO Backend](../../docs/recipes/tier-3-production/ubuntu-hil-osmo-backend.md)
