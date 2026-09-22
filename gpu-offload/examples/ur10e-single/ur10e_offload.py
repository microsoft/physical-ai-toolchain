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
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import socket
from contextlib import nullcontext
from typing import TYPE_CHECKING, Any

import torch

if TYPE_CHECKING:
    from lerobot.policies.pretrained import PreTrainedPolicy

logger = logging.getLogger(__name__)

_infer_count = 0


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

        Returns ``(policy, preprocessor, postprocessor)`` as remote proxies.
        """
        from lerobot.configs.policies import PreTrainedConfig
        from lerobot.policies.factory import make_pre_post_processors
        from lerobot.policies.pi05.modeling_pi05 import PI05Policy

        logger.info("Loading pi05 checkpoint from %s onto %s", self.checkpoint_path, self.device)
        # PreTrainedConfig resolves the concrete config class from the checkpoint's
        # "type" discriminator; the subclass alone cannot parse that field.
        config = PreTrainedConfig.from_pretrained(self.checkpoint_path)
        config.compile_model = self.compile_model
        policy = PI05Policy.from_pretrained(self.checkpoint_path, config=config)
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
        pod that actually holds the weights.
        """
        return {
            "device": str(policy.config.device),
            "use_amp": bool(policy.config.use_amp),
            "n_action_steps": int(policy.config.n_action_steps),
            "policy_class": type(policy).__name__,
            "cuda_available": bool(torch.cuda.is_available()),
            "cuda_device_name": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "",
            "hostname": socket.gethostname(),
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
