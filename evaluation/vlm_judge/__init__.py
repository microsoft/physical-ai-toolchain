"""VLM-as-judge evaluation for robot manipulation episodes.

Open-weight first: a Qwen3-VL backend drives outcome MCQ scoring with
N-sample self-consistency and GVL shuffle-and-rank dense process reward.
The :class:`JudgeAgent` orchestrates a multi-step judgment chain (outcome
-> milestones -> failure attribution) on top of any ``JudgeBackend``,
and :class:`JudgeService` wraps the whole stack with a disk cache so the
dataviewer and policy-evaluation pipelines can call a single entry point.
"""

from __future__ import annotations

from .agent import AgentConfig, JudgeAgent
from .backend import EchoBackend, JudgeBackend, OpenAICompatibleBackend, Qwen3VLBackend
from .cache import JudgeCache
from .judge import JudgeResult, score_episode
from .service import BackendConfig, FrameConfig, JudgeService, ServiceConfig

__all__ = [
    "AgentConfig",
    "BackendConfig",
    "EchoBackend",
    "FrameConfig",
    "JudgeAgent",
    "JudgeBackend",
    "JudgeCache",
    "JudgeResult",
    "JudgeService",
    "OpenAICompatibleBackend",
    "Qwen3VLBackend",
    "ServiceConfig",
    "score_episode",
]
