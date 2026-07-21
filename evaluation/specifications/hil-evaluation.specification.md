# HiL Evaluation Specification

Hardware-in-the-loop (HiL) evaluation places deployment-representative policy hardware in a bounded control loop. Tier 0 runs the simulator and policy on separate directly connected hosts. Tier 3 retains the existing OSMO-orchestrated no-command compute-plane validation.

## Evaluation Modes

| Mode | Simulator | Policy | Physical robot | Infrastructure | Evidence |
|------|-----------|--------|----------------|----------------|----------|
| Offline replay | None | Development compute | None | Local process | Policy predictions against recorded observations and actions |
| Software-in-the-loop | Development compute | Same development compute path | None | Local process | Simulator behavior with in-process policy inference |
| T0 target-policy-hardware HiL | Development workstation | Separate deployment-representative host | None | ROS 2 and Docker | Bounded simulator control through the target policy host |
| T3 orchestrated HiL | None in the implemented no-command gate | K3s workload | None | OSMO control plane and local K3s backend | Scheduling, observation, proposal, rejection, timing, and artifacts |
| Real-robot execution | Optional | Deployment hardware | Present | Robot-specific runtime | Physical task and safety outcomes |

Offline replay and software-in-the-loop results do not prove target-hardware behavior. T3 scheduling proof does not prove the T0 simulator loop. Real-robot execution is outside the T0 HiL command path.

## Ownership

| Surface | Owner | Boundary |
|---------|-------|----------|
| T0 HiL runners, configuration, policy-host image, and local results | `evaluation/hil/` | Direct local execution only |
| T0 replay and software-in-the-loop implementations | `evaluation/sil/` | Existing behavior remains unchanged |
| T3 HiL workflow definitions | `evaluation/hil/workflows/osmo/` | Existing no-command orchestration |
| T3 host, K3s, and OSMO backend setup | `data-pipeline/setup/hil/` | Existing infrastructure setup and submission |

T0 code does not import or invoke production modules from `evaluation/sil/`, `data-pipeline/setup/hil/`, physical robot command code, Azure, Kubernetes, OSMO, Arc, or FluxCD.

## T0 Topology

```text
Development workstation                         Deployment-representative host
+--------------------------------------+         +-------------------------------+
| Isaac Sim or Isaac Lab               | ROS 2   | ACT or JIT policy process     |
| - publishes one observation          |-------> | - consumes local artifacts    |
| - waits for one action               |<------- | - publishes one action        |
| - applies action in simulation only  |         | - exits after N actions       |
| - writes summary.json on success     |         +-------------------------------+
+--------------------------------------+
```

Each framework uses exactly one direct simulator command and one direct policy-host command. The repository does not provide a launcher, supervisor, SSH wrapper, retry, or local-policy fallback.

## Runtime Profiles

| Process | Runtime | Architecture | Included dependencies | Excluded dependencies |
|---------|---------|--------------|-----------------------|-----------------------|
| IL policy host | `ros:jazzy-ros-base-noble`, Python 3.12 | `linux/amd64` | ROS 2 Jazzy messages, CV bridge, Torch, NumPy, LeRobot 0.6 | Isaac, Azure, MLflow, OSMO, dataset replay |
| RL policy host | Same policy-host image | `linux/amd64` | ROS 2 Jazzy messages, Torch, NumPy | Isaac Lab, ONNX, Azure, OSMO |
| IL simulator | Repository-pinned Isaac Lab 2.3.2 and Isaac Sim 5.1-compatible image | Simulator host | Isaac Sim native ROS 2 bridge | Target-host `rclpy`, physical robot drivers |
| RL simulator | Repository-pinned Isaac Lab 2.3.2 and Isaac Sim 5.1-compatible image | Simulator host | Isaac Lab task runtime and native ROS 2 bridge | Local or alternate-format policy fallback |

Jetson GPU support requires a separate image pinned to one Jetson model and JetPack/L4T release. The x86_64 CPU image is not a Jetson profile.

## Shared Exchange Lifecycle

Both commands require the same positive `--steps` value and the same `--run-id`. Topic names use `/hil/<framework>/<run-id>/...`.

The ROS 2 contract uses volatile durability and depth one. The simulator publishes only when no observation is outstanding. The policy host publishes exactly one action for that observation. The simulator applies that action before publishing the next observation. A response received after its bounded timeout raises `TimeoutError`; the command does not retry or choose another policy.

The policy host exits after publishing `--steps` actions. The simulator exits after applying `--steps` actions. Final HiL acceptance records `hostname` and `uname -m` immediately before each command and requires distinct simulator and policy-host names.

## LeRobot ACT Contract

The IL slice requires a processor-format LeRobot 0.6 ACT policy trained from data matching the configured UR10E scene. The policy host loads only the local policy directory supplied through `--policy-path`:

| Required file | Purpose |
|---------------|---------|
| `config.json` | ACT policy configuration |
| `model.safetensors` | Policy weights |
| `policy_preprocessor.json` | Observation preprocessing and normalization |
| `policy_postprocessor.json` | Action postprocessing and unnormalization |

The policy host loads `ACTPolicy` and both processor pipelines directly under `evaluation/hil/`. It applies the exact target device override and resets the ACT action queue once before the first observation. The bounded run is one episode; the simulator does not reset mid-run.

### IL Scene Configuration

The required JSON scene configuration contains:

| Field | Contract |
|-------|----------|
| `usd_path` | User-supplied local scene path |
| `articulation_prim_path` | UR10E articulation prim |
| `camera_prim_path` | RGB camera prim |
| `joint_names` | Canonical six-joint UR10E order |
| `reset_joint_positions` | Six reset positions in radians |
| `image_width` | `848` |
| `image_height` | `480` |
| `image_encoding` | `rgb8` |
| `physics_dt` | Positive simulation step in seconds |
| `rendering_dt` | Positive render step in seconds |
| `seed` | Simulator seed |
| `steps` | Positive exchange count repeated on the policy-host command |
| `response_timeout_s` | Positive bounded action wait |

The runner consumes these values literally. It does not download an asset, discover a substitute prim, convert a scene, or change action semantics.

### IL ROS 2 Topics

| Topic suffix | Message | Producer | Consumer | Meaning |
|--------------|---------|----------|----------|---------|
| `joint_state` | `sensor_msgs/JointState` | Isaac Sim | ACT policy host | Six positions in canonical joint order |
| `image` | `sensor_msgs/Image` | Isaac Sim | ACT policy host | RGB8 `480x848` observation |
| `action` | `trajectory_msgs/JointTrajectory` | ACT policy host | Isaac Sim | Six delta-radian actions |

`JointState` and `Image` share one simulator observation stamp. The target joins only that complete pair. The response copies the stamp into `JointTrajectory.header.stamp`, sets `joint_names` to the canonical six-joint order, and contains exactly one point with six `positions` values.

Isaac Sim receives the standard message through a generic native ROS 2 subscriber. Simulator code reads `points[0].positions`, verifies the declared canonical order, and commands each simulated joint to its current position plus the returned delta. The message is never forwarded to a physical controller.

## Isaac Lab JIT Contract

The RL slice supports `Isaac-Velocity-Rough-Anymal-C-v0` with one environment, the `policy` observation group, and a local policy artifact directory containing `policy.pt` and `policy_io.json`.

Both commands require `--policy-path` and `--io-descriptor`. `--io-descriptor` points to the `policy_io.json` adjacent to `policy.pt`.

### I/O Descriptor

| Field | Contract |
|-------|----------|
| `schema_version` | Descriptor schema version |
| `policy_file` | `policy.pt` |
| `task_id` | `Isaac-Velocity-Rough-Anymal-C-v0` |
| `observation_group` | `policy` |
| `control_period_s` | Positive task control period |
| `observation_terms` | Ordered observation term objects |
| `action_terms` | Ordered action term objects |

Each term object contains `name`, `dtype`, and `shape`. `dtype` is `float32`; every shape dimension is positive. The flattened observation and action dimensions are the sums of the term shapes in descriptor order. The sidecar is derived from the exact Isaac Lab I/O descriptor export for the policy's task configuration.

### RL ROS 2 Topics

| Topic suffix | Message | Producer | Consumer | Meaning |
|--------------|---------|----------|----------|---------|
| `observation` | `std_msgs/Float32MultiArray` | Isaac Lab | JIT policy host | Flattened descriptor-ordered `policy` observation |
| `action` | `std_msgs/Float32MultiArray` | JIT policy host | Isaac Lab | Flattened descriptor-ordered task action |

The simulator numbers exchanges from one and writes that sequence to `layout.data_offset`. The policy response copies the sequence. This distinguishes a new action from the generic subscriber's retained output without adding a custom message.

The policy host converts each observation to float32 batch shape `[1, observation_dimension]`, calls the JIT policy once, requires output shape `[1, action_dimension]`, and publishes row zero. Isaac Lab steps the environment only after receiving the matching action. Terminated environments reset while the total exchange count continues.

## T0 Result Contract

The simulator owns the result. At invocation start it creates the requested output directory and removes only an existing `summary.json`. On success it writes a temporary JSON file and atomically replaces `summary.json`. On failure it removes the temporary file, re-raises the originating error, and leaves no success summary.

### Common Fields

| Field | Type | Meaning |
|-------|------|---------|
| `schema_version` | String | Result schema version |
| `mode` | String | `t0-target-policy-hardware-hil` |
| `framework` | String | `lerobot-act` or `isaaclab-jit` |
| `run_id` | String | Run-specific ROS 2 namespace identifier |
| `policy` | Object | Local path and policy identity |
| `task_or_scene` | Object | Task ID or configured scene identity |
| `seed` | Integer | Simulator seed |
| `requested_steps` | Integer | Requested exchange count |
| `completed_steps` | Integer | Applied action count |
| `started_at` | String | UTC timestamp |
| `finished_at` | String | UTC timestamp |
| `outcomes` | Object | Framework-specific outcomes |
| `timing` | Object | Latency and simulator timing |

`timing.round_trip_latency_ms` contains integer `sample_count` plus `minimum`, `mean`, `p95`, and `maximum` in milliseconds over every successful exchange. `p95` uses `numpy.percentile(samples, 95)`. `sample_count` equals `completed_steps`.

`timing.simulated_seconds` is total simulator time advanced during the bounded loop. `timing.wall_seconds` is wall time around that loop. `timing.real_time_factor` equals `simulated_seconds / wall_seconds`.

### Framework Outcomes

| Framework | Required outcome fields |
|-----------|-------------------------|
| LeRobot ACT | Reset count, final ordered joint positions, termination reason `step-limit` |
| Isaac Lab JIT | Completed episode returns, termination count, reset count, final partial-episode return |

The result records measurements and outcomes but does not define universal policy-promotion thresholds.

## T0 Artifact Boundaries

| Tier | Policy artifact behavior |
|------|--------------------------|
| T0 and T1 | Consume an already-local policy directory |
| T2 | Materialize one exact Azure Machine Learning model name/version locally before invoking the unchanged T0 commands |
| T3 | Deliver selected files through the existing passive Azure Container Registry carrier and orchestrate through OSMO/K3s |

T0 commands do not call Azure or pull the passive ACR carrier. The public `lerobot/aloha_sim_insertion_human` dataset remains an offline training/replay example and is not an input to the UR10E HiL scene. ALOHA Isaac conversion, data collection, and retraining are separate work.

## T3 Orchestrated HiL

The existing T3 path uses an Ubuntu workstation running K3s as the compute plane and an OSMO control plane hosted in Azure AKS. It validates scheduling and no-command behavior; physical motion is not implemented.

### T3 Topology

| Component | Location | Responsibility |
|-----------|----------|----------------|
| OSMO control plane | Azure AKS | Workflow API, backend registration, configuration, and user access |
| HiL compute plane | Ubuntu K3s cluster | External OSMO backend operator and HiL workloads |
| Robot and sensors | Physical site | Reserved for deferred physical validation |
| Storage | Azure Blob or approved local staging | Episode recordings, logs, metrics, and run artifacts |

The edge backend operator initiates an outbound connection to the OSMO control plane. The control plane does not require an inbound route to the robot site.

### T3 Execution Modes

| Mode | Status | Safety requirement | Expected use |
|------|--------|--------------------|--------------|
| Dry run | Implemented | No command-capable transport exists; negative command probe must fail | Validate scheduling, observation shape, timing, and artifacts |
| Operator-confirmed motion | Deferred | Operator confirms workspace, robot state, and E-stop readiness | First physical validation |
| Bounded physical run | Deferred | Independent E-stop and configured action/workspace limits | Short reproducible evaluation episode |

### T3 Required Inputs

* Policy image digest and model version
* Robot identifier and hardware configuration
* Sensor and observation configuration
* OSMO backend name and endpoint mode
* Kubernetes namespace and ServiceAccount configuration
* Storage account/container and selected authentication mode
* Safety limits, operator identity, and E-stop procedure
* Run duration, task definition, and success criteria

### T3 Required Artifacts

Each run records policy identity, robot state, observations, proposed actions, timestamps, safety events, outcomes, storage status, and artifact checksums. Write artifacts to durable local storage before upload and redact credentials from logs and metadata.

### T3 Status

The repository implements Ubuntu host preparation, K3s installation, optional Arc onboarding, private OSMO connectivity, external backend deployment, a CPU-only OSMO smoke workflow, and a UR10E-shaped no-command run with local artifact checksums. VPN, K3s, external backend, Arc, GPU, and robot validation require user-owned infrastructure and hardware. GPU and physical motion remain deferred.
