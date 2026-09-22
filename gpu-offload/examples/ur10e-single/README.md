# ur10e-single with GPU inference offloaded by the mutating controller

<!-- cspell:ignore paligemma pyautogui teleoperator -->

Runs the `ur10e-single` Pi0.5 deployment as a control container next to the UR10e while
the policy executes in a GPU server-stage pod. The workload image is built with the
remoter SDK layered in through `sitecustomize`, so the offload is configured by the
Kubernetes manifest rather than by rewriting the deployment around an SDK.

## 🧭 What This Example Demonstrates

The offload pattern from the xavier tutorial's `Dockerfile.pyremote2`, applied to a real
robot deployment: an existing workload image is layered with the SDK and a
`sitecustomize` hook, and Python installs the offload before any application code runs.
[`first-run`](../first-run/README.md) shows the same layering at minimal size with no
hardware; this example carries the parts that only appear with a real workload, namely a
7 GB checkpoint that must stay resident on the stage and a closed control loop that
cannot afford to send it over the wire.

| Layer                | Source                                                         | Role                                                           |
|----------------------|----------------------------------------------------------------|----------------------------------------------------------------|
| Workload environment | `ur10e-single` `pyproject.toml` and `uv.lock`                  | lerobot 0.4.4, torch, UR10e RTDE, and RealSense drivers        |
| Remoter SDK          | [`runtime/`](../../runtime) published as a payload image       | Copied in with `COPY --from`, installed into the workload venv |
| Startup hook         | `remoter/sitecustomizer.py` on `PYTHONPATH`                    | Starts the offload runtime at interpreter start                |
| Offload boundary     | [ur10e_offload.py](./ur10e_offload.py)                         | The class and methods `remote.yaml` moves to the GPU stage     |
| Opt-in signals       | [templates/manifests.yaml.tpl](./templates/manifests.yaml.tpl) | `xavier: "true"` label and `xavierconfig` annotation           |

Everything else the workload needs is injected at admission: the `remote.yaml` mount,
`REMOTER_CONFIG`, `CONFIGFROMKUBE`, `SERVERLABEL`, and the generated GPU server
Deployment.

## 🧩 The Offload Boundary

`remote.yaml` names two kinds of target:

| Entry                                              | Kind          | Effect                                                                  |
|----------------------------------------------------|---------------|-------------------------------------------------------------------------|
| `lerobot.policies.pi05.modeling_pi05/PI05Policy`   | `remoteclass` | The loaded policy stays resident on the stage; the client holds a proxy |
| `lerobot.processor.pipeline/DataProcessorPipeline` | `remoteclass` | Both processor pipelines stay resident the same way                     |
| `ur10e_offload/PolicyRunner/load`                  | `remotefunc`  | Reads roughly 7 GB of weights onto the GPU once per stage               |
| `ur10e_offload/PolicyRunner/get_action`            | `remotefunc`  | One inference step per control cycle                                    |

Remoting the lerobot classes is what keeps the weights off the wire. `PolicyRunner.load`
returns the policy and both pipelines; because those classes are remoted, the control
container receives proxies rather than serialized objects, and passing the proxies back
into `get_action` rehydrates them on the stage.

> [!NOTE]
> `PolicyProcessorPipeline` is a `TypeAlias` for `DataProcessorPipeline`. Dehydration is
> keyed on `"<module>/<class>"` of the concrete runtime class, so the alias and the base
> class are not interchangeable here.

What crosses the wire each cycle:

| Direction       | Payload                                                          |
|-----------------|------------------------------------------------------------------|
| Client to stage | Observation as CPU float32 tensors, roughly 2 MB for two cameras |
| Stage to client | Action tensor, seven values                                      |

The MessagePack codec accepts `str`, `int`, `float`, `bytes`, `list`, `dict`, and torch
tensors. NumPy arrays have no adapter, so `run_ur10e.py` converts observations to CPU
tensors before the call. The default encoded-payload ceiling is 8 MiB; two `320x240` and
`424x240` RGB frames as float32 fit under it, but adding cameras or raising resolution
moves toward that limit.

## 📋 Prerequisites

| Requirement         | Detail                                                                                                                |
|---------------------|-----------------------------------------------------------------------------------------------------------------------|
| GPU node            | 16 GB VRAM or more, with the NVIDIA device plugin advertising `nvidia.com/gpu`                                        |
| Cluster runtime     | `k3s` on the GPU host: the checkpoint and the registry are host-local, which a `kind` node container cannot reach     |
| `gpu-offload` chart | Installed cluster-wide (`mise run d-offload-43-install-controller`)                                                   |
| Host registry       | Running and registered as a k3s mirror (`mise run b-host-20-registry`)                                                |
| `ur10e-single`      | Checked out beside this repository, or `UR10E_SOURCE_PATH` set to it                                                  |
| Trained checkpoint  | A pi05 checkpoint directory on the node holding `config.json`, `model.safetensors`, and the saved processor pipelines |
| HuggingFace cache   | The gated `google/paligemma-3b-pt-224` tokenizer cached on the node; the pods run with `HF_HUB_OFFLINE=1`             |
| UR10e               | Only for `headless` and `record` modes; `self-check` mode needs no robot                                              |

> [!NOTE]
> The server stage requests a whole GPU. On a single-GPU node it cannot start while
> another offload example holds the device, and scaling that example down is not enough:
> the controller recreates a server stage for as long as its client deployment exists.
> Uninstall the other example first, for example `mise run d-offload-90-teardown`.

## 🚀 Run

1. Point the tasks at the checkpoint and the tokenizer cache:

   ```bash
   cd gpu-offload
   mise run a-env-init
   ```

   Then set `UR10E_MODEL_HOST_PATH` and `UR10E_HF_CACHE_HOST_PATH` in `.env`.

2. Start the host registry once per machine:

   ```bash
   mise run b-host-20-registry
   ```

3. Build, push, deploy, and verify:

   ```bash
   mise run g-ur10e-40-build-image
   mise run g-ur10e-41-push-image
   mise run g-ur10e-50-deploy
   mise run g-ur10e-51-check-inference
   ```

   `g-ur10e-50-deploy` installs with `policy.mode=self-check` unless `UR10E_MODE` says
   otherwise. `g-ur10e-51-check-inference` asserts that the load happened on the
   server-stage pod, that CUDA was available there, that the control pod received an
   action, and that only the server stage holds a GPU allocation.

4. Drive the arm. `g-ur10e-52-demo` is the one-command demo: it deploys in `headless`
   mode with USB passthrough, then follows the run until the arm has homed again.

   ```bash
   mise run g-ur10e-52-demo
   ```

   The UR10e moves to its home pose as soon as the policy loads, runs the policy for
   `UR10E_MAX_STEPS` steps (80 by default), and homes again on the way out. Keep the
   workspace clear and stay on the e-stop.

5. Remove the workloads when finished. The checkpoint on the node is untouched:

   ```bash
   mise run g-ur10e-90-teardown
   ```

## ⚙️ Configuration

| Value                       | Default                                                      | Purpose                                                                             |
|-----------------------------|--------------------------------------------------------------|-------------------------------------------------------------------------------------|
| `policy.mode`               | `self-check`                                                 | `self-check` drives synthetic observations; `headless` and `record` drive the UR10e |
| `policy.task`               | `Pick up the large white gear and place it in the blue bin.` | Language instruction; pi05 was trained single-task on this string                   |
| `policy.fps`                | `10`                                                         | Training control frequency; the action chunk replays at this rate                   |
| `policy.nActionSteps`       | `50`                                                         | Actions consumed per predicted chunk                                                |
| `robot.configPath`          | `/workspace/script/ur10e_config_demo.json`                   | Robot and camera configuration carried in the image                                 |
| `model.hostPath`            | none                                                         | Checkpoint directory on the node; required                                          |
| `huggingFaceCache.hostPath` | none                                                         | Tokenizer cache on the node; required                                               |
| `image.registry`            | `localhost:5000`                                             | Host-local registry the cluster mirrors                                             |
| `robot.logEvery`            | `50`                                                         | Emit one `action` event every N steps                                               |
| `robot.debugInference`      | empty                                                        | When set, the GPU stage logs a per-call observation fingerprint and action          |

> [!WARNING]
> Running the loop at the wrong rate replays the action chunk too fast and causes
> overshoot and missed grasps. Changing `policy.task` drifts the text embedding
> off-distribution. Keep both aligned with training.

### Modes

`self-check` loads the checkpoint on the stage, pushes synthetic observations through it,
emits a `self_check_passed` event, then keeps cycling at `policy.fps`. The loop stays open
deliberately: the controller reconciles the GPU stage against a live client, so a container
that exits would tear the stage down and reload 7 GB on restart.

`headless` drives the arm from a terminal with no display, no dataset, and no
teleoperator. It moves the arm to the home pose, then runs the same per-step sequence the
record loop performs — observation, dataset frame, offloaded inference, action — and
returns the arm home when it stops. `Ctrl+C` and `SIGTERM` both exit through the homing
path, and `--max-steps` bounds the run.

The ur10e-single plugin builds a Gradio dashboard in `UR10E.__init__` and gates episode
start on a button click, falling back to a blocking `input()` when the dashboard is
unavailable; its package import also pulls in `pyautogui`, which resolves `DISPLAY` at
import time. Headless mode replaces the dashboard and stubs `pyautogui`, so none of that
reaches a container. It also homes directly rather than through the driver's `reset`
action, which detours via the submissive pose at full speed.

`record` runs the stock `lerobot-record` loop with inference redirected at three seams:
`make_policy` and `make_pre_post_processors` return local stand-ins, and `predict_action`
converts the observation and calls the remote `PolicyRunner`. The stand-ins exist because
the record loop reads `policy.config.device` and `policy.config.use_amp` every cycle; a
remote proxy would turn each read into a round trip. Robot and camera I/O are untouched.

> [!NOTE]
> The checkpoint sets `compile_model: true`, which costs roughly 200 s of `torch.compile`
> autotuning on the first inference. `PolicyRunner` overrides it to `false` so the first
> control cycle is predictable.

## 📦 Caching

Nothing in the deployment path pulls from the internet at run time.

| Artifact         | Cache                                                 | Mechanism                                        |
|------------------|-------------------------------------------------------|--------------------------------------------------|
| Workload image   | Host registry on `localhost:5000`                     | k3s mirror in `/etc/rancher/k3s/registries.yaml` |
| Python wheels    | Host `uv` cache, `$UV_CACHE_DIR` or `$HOME/.cache/uv` | Bind-mounted into the build                      |
| Model checkpoint | Node directory, `UR10E_MODEL_HOST_PATH`               | hostPath `PersistentVolume` and claim            |
| Tokenizer        | Node HuggingFace cache, `UR10E_HF_CACHE_HOST_PATH`    | hostPath `PersistentVolume` and claim            |

The build stages the `ur10e-single` sources into `.ur10e-src` under this directory so the
build context stays inside `gpu-offload` instead of widening to the parent directory. The
staging directory is removed when the build finishes.

## 🧩 Volume Propagation

The controller copies `configMap`, `downwardAPI`, `emptyDir`, `ephemeral`,
`persistentVolumeClaim`, `projected`, and `secret` volumes from the client container into
the generated server pod, and rejects raw `hostPath` volumes. The checkpoint and the
tokenizer cache are therefore published as hostPath `PersistentVolume` objects with
matching claims, and the control container declares both mounts even though it never reads
them. Environment variables on the control container are merged into the server container
the same way, so `UR10E_CHECKPOINT_PATH` and `HF_HOME` resolve identically on both sides.

## 📦 Files

| Path                           | Content                                                            |
|--------------------------------|--------------------------------------------------------------------|
| `ur10e_offload.py`             | Offload seam; the class whose methods run on the GPU stage         |
| `run_ur10e.py`                 | Control-loop entrypoint for all three modes                        |
| `remote.yaml`                  | Offload spec, mirrored into the ConfigMap the chart renders        |
| `Containerfile`                | Workload image with the remoter SDK and `sitecustomize` layered in |
| `templates/manifests.yaml.tpl` | ServiceAccount, RBAC, volumes, ConfigMap, and control Deployment   |
| `scripts/run-headless-host.sh` | Runs `headless` mode on the host against the ur10e-single venv     |
| `scripts/demo.sh`              | Deploys, drives the arm, and follows the run until it homes        |

## 🔍 Troubleshooting

| Symptom                                                     | Cause                                                                                                                            |
|-------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------|
| Server pod stays `Pending` on `Insufficient nvidia.com/gpu` | Another example still owns the device; uninstall its release rather than scaling it down                                         |
| `ImagePullBackOff` on `localhost:5000/...`                  | The registry is not running, or k3s was not restarted after the mirror was written                                               |
| `unsupported type ...; register an explicit adapter`        | A NumPy array or other unsupported value reached the wire; convert it before the call                                            |
| No `loaded` event after several minutes                     | The checkpoint volume is empty, or the tokenizer is missing from the node HuggingFace cache                                      |
| `CodecLimitsError` on `get_action`                          | The observation exceeds the 8 MiB encoded ceiling; reduce camera count or resolution                                             |
| `KeyError: 'DISPLAY'` at import                             | Something imported the ur10e-single teleoperator; only `headless` mode stubs `pyautogui`                                         |
| The arm never moves in `headless` mode                      | `UR10E.active` is still false; homing must complete before servoJ is unlocked                                                    |
| The action is identical on every step                       | `singleinstance: true` was set on `get_action`; it memoizes the first result and never re-runs the call                          |
| The client hangs in `load()` and the stage sends no result  | A terminating client claimed the single-instance slot; scale the client to zero and wait for deletion before replacing the stage |
