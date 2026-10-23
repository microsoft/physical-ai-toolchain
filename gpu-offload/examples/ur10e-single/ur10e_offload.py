# cspell:ignore autocast gethostname
"""GPU offload seam for the ur10e-single Pi0.5 deployment.

The control container next to the UR10e holds robot and camera I/O only. Every
call in this module executes on the GPU server stage: ``remote.yaml`` lists
:class:`PolicyRunner` methods under ``remotefuncs`` and the lerobot policy and
processor classes under ``remoteclasses``.

Listing the lerobot classes matters. :meth:`PolicyRunner.load` returns the policy
and both processor pipelines, and because those classes are remoted the caller
receives proxies rather than serialized objects: roughly 7 GB of weights stay
resident on the GPU stage. Handing the same proxies back to
:meth:`PolicyRunner.get_action` rehydrates them server-side, so preprocessing,
the flow-matching pass, and postprocessing all run next to the device.

Only plain values cross the wire. The MessagePack codec accepts ``str``,
``float``, ``int``, ``bytes``, ``list``, ``dict``, and torch tensors; NumPy
arrays have no adapter, which is why the caller converts observations to CPU
tensors before calling :meth:`get_action`.

:meth:`PolicyRunner.load` refuses a checkpoint unless its files match the
candidate manifest from the VLA training pipeline and every weight loads, so no
mode reaches the robot with a corrupted, swapped, or randomly initialized policy.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import socket
import weakref
from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, Any

import torch

if TYPE_CHECKING:
    from lerobot.policies.pretrained import PreTrainedPolicy

logger = logging.getLogger(__name__)

_infer_count = 0

# Deployments copy the manifest that training/vla/scripts/finalize_candidate.py
# writes into the checkpoint directory; it is the one file the digest excludes.
CANDIDATE_MANIFEST_NAME = "candidate-manifest.json"
_POLICY_TYPE = "pi05"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_ERROR_DETAIL_LIMIT = 500
_NOT_LOADED = object()
_verified_candidates: weakref.WeakKeyDictionary[Any, dict[str, str]] = weakref.WeakKeyDictionary()


class CheckpointVerificationError(RuntimeError):
    """The checkpoint is not a verified training candidate or its weights did not load."""


def _canonical_json(value: Any) -> str:
    """Serialize the way the training pipeline does before hashing."""
    return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"), sort_keys=True)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _candidate_files(checkpoint: Path) -> list[dict[str, str]]:
    """List checkpoint files exactly as the training pipeline lists candidate files."""
    entries = []
    for path in sorted(checkpoint.rglob("*")):
        relative = path.relative_to(checkpoint).as_posix()
        if path.is_file() and relative != CANDIDATE_MANIFEST_NAME:
            with path.open("rb") as handle:
                entries.append({"path": relative, "sha256": hashlib.file_digest(handle, "sha256").hexdigest()})
    return entries


def _describe_file_mismatch(listed: Any, actual: list[dict[str, str]]) -> str:
    if not isinstance(listed, list) or not all(isinstance(entry, dict) and "path" in entry for entry in listed):
        return "the files do not match artifact_manifest_sha256"
    expected = {entry["path"]: entry.get("sha256") for entry in listed}
    found = {entry["path"]: entry["sha256"] for entry in actual}
    differences = {
        "missing": sorted(expected.keys() - found.keys()),
        "unexpected": sorted(found.keys() - expected.keys()),
        "changed": sorted(name for name in expected.keys() & found.keys() if expected[name] != found[name]),
    }
    return "; ".join(f"{label}: {', '.join(names)}" for label, names in differences.items() if names) or (
        "the files do not match artifact_manifest_sha256"
    )


def verify_candidate_manifest(checkpoint_path: str) -> dict[str, str]:
    """Require the checkpoint directory to hold exactly the files of a pi05 training candidate.

    The manifest catches corrupted, truncated, added, removed, and swapped files and
    records the candidate's lineage. It is not a signature: anyone who can rewrite the
    checkpoint directory can also rewrite the manifest.

    Returns:
        The candidate's artifact digest, lineage fingerprints, and evidence job.

    Raises:
        CheckpointVerificationError: The manifest is missing or invalid, or the files differ from it.
    """
    checkpoint = Path(checkpoint_path)
    manifest_path = checkpoint / CANDIDATE_MANIFEST_NAME
    try:
        record = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise CheckpointVerificationError(
            f"No {CANDIDATE_MANIFEST_NAME} in {checkpoint}; deploy a training-pipeline candidate with its manifest"
        ) from error
    except (OSError, ValueError) as error:
        raise CheckpointVerificationError(f"Unable to read {manifest_path}: {error}") from error
    if not isinstance(record, dict) or record.get("schema_version") != 1 or record.get("kind") != "candidate":
        raise CheckpointVerificationError(f"{manifest_path} is not a candidate manifest")
    if record.get("policy_type") != _POLICY_TYPE:
        raise CheckpointVerificationError(
            f"{manifest_path} describes a {record.get('policy_type')!r} candidate, not {_POLICY_TYPE}"
        )
    for field in ("artifact_manifest_sha256", "training_fingerprint", "dataset_fingerprint"):
        value = record.get(field)
        if not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value):
            raise CheckpointVerificationError(f"{manifest_path} has no valid {field}")
    for field in ("created_at", "evidence_job", "candidate_output", "manifest_output"):
        if not isinstance(record.get(field), str) or not record[field]:
            raise CheckpointVerificationError(f"{manifest_path} has no {field}")

    expected = record["artifact_manifest_sha256"]
    listed = record.get("files")
    if listed is not None and _sha256_text(_canonical_json(listed)) != expected:
        raise CheckpointVerificationError(f"{manifest_path} lists files that do not match its artifact digest")
    actual = _candidate_files(checkpoint)
    if _sha256_text(_canonical_json(actual)) != expected:
        raise CheckpointVerificationError(
            f"{checkpoint} does not match its candidate manifest: {_describe_file_mismatch(listed, actual)}"
        )
    return {
        field: record[field]
        for field in ("artifact_manifest_sha256", "training_fingerprint", "dataset_fingerprint", "evidence_job")
    }


def _shorten(text: str) -> str:
    return text if len(text) <= _ERROR_DETAIL_LIMIT else f"{text[:_ERROR_DETAIL_LIMIT]}..."


def load_complete_policy(policy_class: Any, checkpoint_path: str, config: Any) -> Any:
    """Load a policy and fail unless the returned object received every weight.

    LeRobot's pi05 loader catches weight-loading errors, prints a warning, and returns
    a randomly initialized policy. Recording ``load_state_dict`` while the policy is
    built shows whether the returned object completed a strict load.

    Raises:
        CheckpointVerificationError: The load was skipped, failed, or left keys unmatched.
    """
    outcomes: weakref.WeakKeyDictionary[torch.nn.Module, Any] = weakref.WeakKeyDictionary()
    original_load_state_dict = torch.nn.Module.load_state_dict

    def recording_load_state_dict(module: torch.nn.Module, *args: Any, **kwargs: Any) -> Any:
        try:
            result = original_load_state_dict(module, *args, **kwargs)
        except Exception as error:
            outcomes[module] = error
            raise
        outcomes[module] = result
        return result

    torch.nn.Module.load_state_dict = recording_load_state_dict
    try:
        policy = policy_class.from_pretrained(checkpoint_path, config=config)
    finally:
        torch.nn.Module.load_state_dict = original_load_state_dict

    name = type(policy).__name__
    outcome = outcomes.get(policy, _NOT_LOADED)
    if outcome is _NOT_LOADED:
        raise CheckpointVerificationError(f"{name} was returned without loading the weights in {checkpoint_path}")
    if isinstance(outcome, Exception):
        raise CheckpointVerificationError(
            f"{name} failed to load the weights in {checkpoint_path}: {_shorten(str(outcome))}"
        ) from outcome
    missing = list(getattr(outcome, "missing_keys", ()))
    unexpected = list(getattr(outcome, "unexpected_keys", ()))
    if missing or unexpected:
        raise CheckpointVerificationError(
            f"{name} loaded {checkpoint_path} with {len(missing)} missing and {len(unexpected)} unexpected keys: "
            f"{_shorten(', '.join([*missing, *unexpected]))}"
        )
    return policy


def _debug_inference(observation: dict[str, Any], action: torch.Tensor, policy: Any) -> None:
    """Emit a per-call fingerprint of the observation as seen by the GPU stage.

    Enabled by ``UR10E_DEBUG_INFERENCE``. Distinguishes a stalled client loop
    from a stalled policy: the counter proves server-side re-execution, the
    image digests prove the observation varies across the wire, and the queue
    length shows whether ``select_action`` is refilling its chunk every call.
    """
    fingerprint = {}
    for name, value in sorted(observation.items()):
        if isinstance(value, torch.Tensor) and value.ndim >= 3:
            digest = hashlib.sha1(value.detach().cpu().numpy().tobytes()).hexdigest()
            fingerprint[name] = digest[:10]
    state = observation.get("observation.state")
    queues = getattr(policy, "_queues", None)
    print(
        json.dumps(
            {
                "event": "server_infer",
                "n": _infer_count,
                "obj_id": id(policy),
                "images": fingerprint,
                "state": [round(float(v), 4) for v in state.flatten().tolist()] if state is not None else None,
                "action": [round(float(v), 4) for v in action.flatten().tolist()],
                "queue_len": {k: len(v) for k, v in queues.items()} if isinstance(queues, dict) else None,
            }
        ),
        flush=True,
    )


class PolicyRunner:
    """Load a Pi0.5 checkpoint and run single-step inference on the GPU stage.

    The instance itself is dehydrated onto the server on first use, so its state
    is restricted to strings.
    """

    def __init__(self, checkpoint_path: str, device: str = "cuda", compile_model: bool = False) -> None:
        self.checkpoint_path = checkpoint_path
        self.device = device
        self.compile_model = compile_model

    def load(self, rename_map: dict[str, str] | None = None) -> tuple[Any, Any, Any]:
        """Load the policy and its saved processor pipelines.

        ``singleinstance`` in ``remote.yaml`` keeps one :class:`PolicyRunner` per
        stage, so the weights are read once and reused by every control cycle.

        The processors are rebuilt from the checkpoint rather than from dataset
        statistics: the normalizer safetensors saved next to ``model.safetensors``
        already carry the training quantiles, which keeps NumPy statistics off the
        wire.

        ``compile_model`` is resolved before construction because the policy binds
        ``torch.compile`` in ``__init__``. The checkpoint enables it, which costs
        roughly 200 s of autotuning on the first inference; the control loop wants
        a predictable first cycle instead.

        The checkpoint must match its candidate manifest before any weights are read,
        and the policy must complete a strict weight load. Either failure raises, so the
        caller never receives a policy to drive the robot with.

        Returns ``(policy, preprocessor, postprocessor)`` as remote proxies.

        Raises:
            CheckpointVerificationError: The checkpoint failed verification or its weights did not load.
        """
        from lerobot.configs.policies import PreTrainedConfig
        from lerobot.policies.factory import make_pre_post_processors
        from lerobot.policies.pi05.modeling_pi05 import PI05Policy

        candidate = verify_candidate_manifest(self.checkpoint_path)
        logger.info(
            "Checkpoint %s matches candidate manifest %s", self.checkpoint_path, candidate["artifact_manifest_sha256"]
        )
        logger.info("Loading pi05 checkpoint from %s onto %s", self.checkpoint_path, self.device)
        # PreTrainedConfig resolves the concrete config class from the checkpoint's
        # "type" discriminator; the subclass alone cannot parse that field.
        config = PreTrainedConfig.from_pretrained(self.checkpoint_path)
        if config.type != _POLICY_TYPE:
            raise CheckpointVerificationError(
                f"{self.checkpoint_path} holds a {config.type!r} policy, not {_POLICY_TYPE}"
            )
        config.compile_model = self.compile_model
        policy = load_complete_policy(PI05Policy, self.checkpoint_path, config)
        _verified_candidates[policy] = candidate
        policy.to(self.device)
        policy.eval()

        overrides: dict[str, dict[str, Any]] = {"device_processor": {"device": self.device}}
        if rename_map:
            overrides["rename_observations_processor"] = {"rename_map": rename_map}

        preprocessor, postprocessor = make_pre_post_processors(
            policy_cfg=policy.config,
            pretrained_path=self.checkpoint_path,
            preprocessor_overrides=overrides,
        )
        logger.info("Policy ready on %s (cuda available: %s)", self.device, torch.cuda.is_available())
        return policy, preprocessor, postprocessor

    def describe(self, policy: PreTrainedPolicy) -> dict[str, Any]:
        """Return the policy settings the control loop needs locally.

        The control loop reads ``policy.config.device`` and ``policy.config.use_amp``
        every cycle. Fetching them once as plain values avoids per-attribute round
        trips against the proxy. ``hostname`` resolves on the server, so it names the
        pod that actually holds the weights. The candidate fields identify the verified
        training candidate the stage is serving.
        """
        candidate = _verified_candidates.get(policy, {})
        return {
            "device": str(policy.config.device),
            "use_amp": bool(policy.config.use_amp),
            "n_action_steps": int(policy.config.n_action_steps),
            "policy_class": type(policy).__name__,
            "cuda_available": bool(torch.cuda.is_available()),
            "cuda_device_name": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "",
            "hostname": socket.gethostname(),
            "candidate_manifest_sha256": candidate.get("artifact_manifest_sha256", ""),
            "training_fingerprint": candidate.get("training_fingerprint", ""),
            "evidence_job": candidate.get("evidence_job", ""),
        }

    def reset(self, policy: PreTrainedPolicy, preprocessor: Any, postprocessor: Any) -> None:
        """Clear the action queue and processor state between episodes."""
        policy.reset()
        preprocessor.reset()
        postprocessor.reset()

    def get_action(
        self,
        policy: PreTrainedPolicy,
        preprocessor: Any,
        postprocessor: Any,
        observation: dict[str, Any],
        use_amp: bool = False,
    ) -> torch.Tensor:
        """Run one inference step and return the action as a CPU tensor.

        Mirrors ``lerobot.utils.control_utils.predict_action`` with the tensor
        conversion already done by the caller. The postprocessor ends in a device
        step targeting the CPU, so the returned tensor is safe to serialize back.
        """
        amp = use_amp and torch.cuda.is_available()
        global _infer_count
        _infer_count += 1
        with (
            torch.inference_mode(),
            torch.autocast(device_type="cuda") if amp else nullcontext(),
        ):
            processed = preprocessor(observation)
            action = policy.select_action(processed)
            action = postprocessor(action)
        action = action.detach().cpu()
        if os.environ.get("UR10E_DEBUG_INFERENCE"):
            _debug_inference(observation, action, policy)
        return action
