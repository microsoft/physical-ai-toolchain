"""Stateful service wrapper for the VLM-as-judge harness.

``JudgeService`` is the single integration surface used by both the dataviewer
backend (per-episode annotation) and the policy-evaluation pipeline
(rollout-MP4 scoring). It owns:

- a lazily constructed ``JudgeBackend`` and ``JudgeAgent``;
- a disk-backed ``JudgeCache`` for idempotent re-runs;
- frame extraction + multi-view tiling.

Backends are loaded on first use so importing the service costs no GPU
memory and no network — the dataviewer can mount the API even if the
backend is configured but not yet provisioned.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .agent import AgentConfig, JudgeAgent
from .backend import EchoBackend, JudgeBackend, OpenAICompatibleBackend, Qwen3VLBackend
from .cache import JudgeCache
from .frames import FrameWindow, extract_frames, tile_horizontally
from .judge import JudgeResult
from .prompts import PROMPT_VERSION

_LOGGER = logging.getLogger("evaluation.vlm_judge")


@dataclass(frozen=True, slots=True)
class BackendConfig:
    """Backend configuration for ``JudgeService``."""

    kind: str = "qwen3-vl"
    """One of ``qwen3-vl``, ``openai-compat``, ``echo``."""

    model_id: str = "Qwen/Qwen3-VL-4B-Instruct"
    revision: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    device_map: str = "auto"
    dtype: str = "bfloat16"


@dataclass(frozen=True, slots=True)
class FrameConfig:
    """Frame extraction settings."""

    n_frames: int = 12
    target_size: tuple[int, int] = (448, 448)


@dataclass(frozen=True, slots=True)
class ServiceConfig:
    """Top-level service configuration."""

    backend: BackendConfig = field(default_factory=BackendConfig)
    frames: FrameConfig = field(default_factory=FrameConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    cache_dir: Path | None = None


class JudgeService:
    """Stateful judge service shared across the dataviewer and policy eval."""

    def __init__(self, config: ServiceConfig | None = None) -> None:
        self._config = config or ServiceConfig()
        self._backend: JudgeBackend | None = None
        self._agent: JudgeAgent | None = None
        self._cache = JudgeCache(self._config.cache_dir)

    @property
    def config(self) -> ServiceConfig:
        return self._config

    @property
    def model_id(self) -> str:
        return self._config.backend.model_id

    def warmup(self) -> None:
        """Eagerly build the backend so the first request does not pay the load cost."""
        self._ensure_agent()

    def judge_episode(
        self,
        *,
        episode_id: str,
        instruction: str,
        video_paths: Mapping[str, Path | str],
        from_s: float | None = None,
        to_s: float | None = None,
        force: bool = False,
        cache_dir: Path | None = None,
        process_method: str | None = None,
    ) -> JudgeResult:
        """Score a single episode given one or more view MP4 paths.

        ``from_s`` / ``to_s`` slice an episode out of a chunked v3.0 video.
        When ``force`` is ``False`` and a cache entry exists, returns the
        cached result without invoking the backend. ``cache_dir`` overrides the
        service-level cache for this call so the dataviewer can store judgments
        beside the dataset being evaluated. ``process_method`` overrides the
        configured process-reward method ('gvl' or 'chronological') for this
        call and is reflected in the cache key.
        """
        effective_method = process_method or self._config.agent.process_method
        agent_config = replace(self._config.agent, process_method=effective_method)
        cache = self.cache_for(cache_dir)
        cache_key = cache.key(
            video_paths=video_paths,
            instruction=instruction,
            judge_model=self.model_id,
            prompt_version=PROMPT_VERSION,
            from_s=from_s,
            to_s=to_s,
            agent_config=agent_config,
        )
        if not force and cache.enabled:
            cached = cache.get(cache_key)
            if cached is not None:
                _LOGGER.info("Cache hit for %s (%s)", episode_id, cache_key[:12])
                return _result_from_dict(cached)

        frames = self._extract(video_paths=video_paths, from_s=from_s, to_s=to_s)
        agent = self._ensure_agent()
        result = agent.judge(
            episode_id=episode_id,
            instruction=instruction,
            frames=frames,
            process_method=effective_method,
        )
        cache.put(cache_key, result.to_dict())
        return result

    def cache_for(self, cache_dir: Path | None = None) -> JudgeCache:
        """Return a cache rooted at ``cache_dir`` or the service-level default.

        The dataviewer passes a per-dataset directory so judgments live beside
        the episodes they describe; the CLI and policy-eval pipeline pass
        ``None`` and reuse the shared ``cache_dir`` from config.
        """
        if cache_dir is None:
            return self._cache
        return JudgeCache(cache_dir)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_agent(self) -> JudgeAgent:
        if self._agent is None:
            backend = self._build_backend(self._config.backend)
            self._backend = backend
            self._agent = JudgeAgent(backend, config=self._config.agent)
        return self._agent

    def _extract(
        self,
        *,
        video_paths: Mapping[str, Path | str],
        from_s: float | None,
        to_s: float | None,
    ):
        if not video_paths:
            raise ValueError("video_paths must contain at least one entry")
        frame_cfg = self._config.frames
        per_view = []
        for view in sorted(video_paths):
            window = FrameWindow(
                path=Path(video_paths[view]),
                from_s=from_s,
                to_s=to_s,
            )
            per_view.append(
                extract_frames(
                    window,
                    n_frames=frame_cfg.n_frames,
                    target_size=frame_cfg.target_size,
                ),
            )
        return per_view[0] if len(per_view) == 1 else tile_horizontally(per_view)

    @staticmethod
    def _build_backend(cfg: BackendConfig) -> JudgeBackend:
        if cfg.kind == "qwen3-vl":
            return Qwen3VLBackend(
                model_id=cfg.model_id,
                revision=cfg.revision,
                device_map=cfg.device_map,
                dtype=cfg.dtype,
            )
        if cfg.kind == "openai-compat":
            if not cfg.base_url:
                raise ValueError("BackendConfig.base_url is required for openai-compat")
            return OpenAICompatibleBackend(
                model=cfg.model_id,
                base_url=cfg.base_url,
                api_key=cfg.api_key,
            )
        if cfg.kind == "echo":
            return EchoBackend()
        raise ValueError(f"Unknown backend kind: {cfg.kind}")


def _result_from_dict(payload: dict[str, Any]) -> JudgeResult:
    """Round-trip a ``JudgeResult`` from a cached JSON dict, tolerating extras."""
    fields = {
        "episode_id",
        "instruction",
        "judge_model",
        "prompt_version",
        "n_frames",
        "outcome_success",
        "outcome_confidence",
        "outcome_n_valid_votes",
        "progress_per_frame",
        "voc",
        "milestones",
        "failure_mode",
    }
    kwargs = {k: payload[k] for k in fields if k in payload}
    return JudgeResult(**kwargs)


__all__ = [
    "BackendConfig",
    "FrameConfig",
    "JudgeService",
    "ServiceConfig",
    "_result_from_dict",
]
