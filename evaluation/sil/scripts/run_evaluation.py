"""LeRobot replay-based inference evaluation."""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import torch

_EVALUATION_ROOT = Path(__file__).resolve().parents[2]
if str(_EVALUATION_ROOT) not in sys.path:
    sys.path.insert(0, str(_EVALUATION_ROOT))
_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))
_VLA_SCRIPTS = _REPOSITORY_ROOT / "training" / "vla" / "scripts"
if str(_VLA_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_VLA_SCRIPTS))

from finalize_candidate import verify_candidate  # noqa: E402
from preflight_dataset import verify_dataset_manifest  # noqa: E402
from sil.hf_revision import resolve_hf_revision  # noqa: E402
from sil.policy_runner import VLA_POLICY_TYPES, load_pretrained_policy, postprocess_action  # noqa: E402
from vla_contracts import (  # noqa: E402
    SCHEMA_VERSION,
    ContractError,
    RecordKind,
    dataset_identity_fingerprint,
    fingerprint,
    load_record,
    write_record,
)

JOINT_NAMES: list[str] = []


_EVALUATION_SCHEMA_VERSION = 1
_VERDICT_SKIPPED = "skipped"
_BASELINE_NONE = "none"

_TOOLCHAIN_TO_VLA_METRIC = {
    "mse": "action_accuracy_l2",
    "mae": "action_accuracy_l1",
    "avg_inference_ms": "inference_latency_mean_ms",
    "throughput_hz": "throughput_hz",
}


def _write_vla_schema_v1(
    output_dir: Path,
    aggregate: dict[str, float | None],
    per_episode: list[dict],
    dataset_repo_id: str,
    policy_repo_id: str,
    episodes_requested: int,
    coverage_reasons: Iterable[str] = (),
) -> None:
    """Emit operational evaluation artifacts alongside eval_results.json."""
    status, reasons = _evaluation_status(episodes_requested, len(per_episode), coverage_reasons)
    metrics_payload = {
        "evaluation_schema_version": _EVALUATION_SCHEMA_VERSION,
        "aggregate_verdict": status,
        "reasons": reasons,
        "baseline_model_version": _BASELINE_NONE,
        "metrics": [
            {
                "name": _TOOLCHAIN_TO_VLA_METRIC.get(toolchain_name, toolchain_name),
                "value": float(value),
                "absolute_threshold": None,
                "absolute_verdict": _VERDICT_SKIPPED,
                "baseline_value": None,
                "regression_pct": 0.0,
                "regression_verdict": _VERDICT_SKIPPED,
            }
            for toolchain_name, value in aggregate.items()
            if value is not None
        ],
    }

    metrics_path = output_dir / "metrics.json"
    with open(metrics_path, "w") as f:
        json.dump(metrics_payload, f, indent=2, allow_nan=False)
    print(f"[INFO] VLA schema v1 metrics: {metrics_path}")

    failure_cases_path = output_dir / "failure_cases.jsonl"
    with open(failure_cases_path, "w") as f:
        for episode in per_episode:
            if not episode.get("rollout_error"):
                continue
            record = {
                "evaluation_schema_version": _EVALUATION_SCHEMA_VERSION,
                "episode_id": str(episode.get("episode", "unknown")),
                "dataset_id": dataset_repo_id,
                "dataset_version": "unknown",
                "domain_category": None,
                "model_version": policy_repo_id,
                "artifact_refs": [],
                "failure_mode": "rollout_error",
                "metric_values": {k: v for k, v in episode.items() if isinstance(v, (int, float))},
                "metric_thresholds_violated": [],
            }
            f.write(json.dumps(record) + "\n")
    print(f"[INFO] VLA schema v1 failure cases: {failure_cases_path}")


def _evaluation_status(
    episodes_requested: int,
    episodes_evaluated: int,
    coverage_reasons: Iterable[str] = (),
) -> tuple[str, list[str]]:
    """Classify replay coverage without applying quality thresholds."""
    if episodes_requested < 1:
        raise ValueError("episodes_requested must be positive")
    if episodes_evaluated < 0 or episodes_evaluated > episodes_requested:
        raise ValueError("episodes_evaluated must be between zero and episodes_requested")
    reasons = sorted(set(coverage_reasons))
    if episodes_evaluated == 0:
        reasons.append("no_usable_episodes")
    elif episodes_evaluated < episodes_requested:
        reasons.append("insufficient_episode_coverage")
    if reasons:
        return "inconclusive", reasons
    return "complete", []


def _require_finite_metrics(metrics: Mapping[str, float]) -> None:
    """Reject metrics that cannot be represented as promotion evidence."""
    non_finite = sorted(name for name, value in metrics.items() if not np.isfinite(value))
    if non_finite:
        raise ValueError(f"Non-finite evaluation metrics: {', '.join(non_finite)}")


def _verify_lifecycle_inputs(
    candidate_path: Path,
    candidate_manifest_path: Path,
    training_record_path: Path,
    dataset_path: Path,
    dataset_manifest_path: Path,
    dataset_asset_id: str,
    dataset_repo_id: str,
    policy_type: str,
    evidence_job: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Verify replay inputs and their cross-record lineage before model loading."""
    candidate = verify_candidate(candidate_path, candidate_manifest_path)
    training = load_record(training_record_path, RecordKind.RUN)
    dataset = verify_dataset_manifest(dataset_path, dataset_manifest_path, dataset_asset_id, dataset_repo_id)
    if candidate["policy_type"] != policy_type:
        raise ContractError("Candidate policy type does not match the requested evaluator policy type")
    if candidate["training_fingerprint"] != fingerprint(training):
        raise ContractError("Candidate training fingerprint does not match the training record")
    if candidate["dataset_fingerprint"] != dataset_identity_fingerprint(dataset):
        raise ContractError("Candidate dataset fingerprint does not match the mounted dataset")
    if candidate["evidence_job"] != evidence_job:
        raise ContractError("Candidate evidence job does not match the evaluator evidence job")
    return candidate, training, dataset


def _write_evaluation_record(
    output_dir: Path,
    candidate: Mapping[str, Any],
    training: Mapping[str, Any],
    dataset: Mapping[str, Any],
    evidence_job: str,
    evaluation_id: str,
    episodes_requested: int,
    episodes_evaluated: int,
    reasons: list[str],
    metrics: Mapping[str, float | None],
) -> dict[str, Any]:
    """Write candidate-bound operational evaluation evidence."""
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": RecordKind.EVALUATION.value,
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "evaluation_id": evaluation_id,
        "run_fingerprint": fingerprint(training),
        "candidate_fingerprint": fingerprint(candidate),
        "dataset_fingerprint": dataset_identity_fingerprint(dataset),
        "status": "inconclusive" if reasons else "complete",
        "outputs_complete": True,
        "episodes_requested": episodes_requested,
        "episodes_evaluated": episodes_evaluated,
        "reasons": reasons,
        "evaluation_output": f"azureml://jobs/{evidence_job}/outputs/evaluation",
        "metrics": {name: value for name, value in metrics.items() if value is not None},
    }
    write_record(output_dir / "evaluation-record.json", record)
    return record


def _setup_matplotlib():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


def _dim_label(j: int) -> str:
    return JOINT_NAMES[j] if j < len(JOINT_NAMES) else f"dim_{j}"


def plot_action_deltas(predicted, ground_truth, episode, fps):
    plt = _setup_matplotlib()
    n_steps, n_joints = predicted.shape
    t = np.arange(n_steps) / fps
    fig, axes = plt.subplots(n_joints, 1, figsize=(14, 2.5 * n_joints), sharex=True)
    fig.suptitle(f"Episode {episode} — Action Deltas: Predicted vs Ground Truth", fontsize=14, fontweight="bold")
    if n_joints == 1:
        axes = [axes]
    for j, ax in enumerate(axes):
        ax.plot(t, ground_truth[:, j], color="#2196F3", alpha=0.8, linewidth=1.2, label="Ground Truth")
        ax.plot(t, predicted[:, j], color="#FF5722", alpha=0.8, linewidth=1.2, label="Predicted")
        ax.fill_between(t, ground_truth[:, j], predicted[:, j], alpha=0.15, color="#9C27B0")
        ax.set_ylabel(_dim_label(j), fontsize=9)
        ax.grid(True, alpha=0.3)
        if j == 0:
            ax.legend(loc="upper right", fontsize=8)
    axes[-1].set_xlabel("Time (s)", fontsize=10)
    fig.tight_layout()
    return fig


def plot_cumulative_positions(predicted, ground_truth, episode, fps):
    plt = _setup_matplotlib()
    pred_pos = np.cumsum(predicted, axis=0)
    gt_pos = np.cumsum(ground_truth, axis=0)
    n_steps, n_joints = predicted.shape
    t = np.arange(n_steps) / fps
    fig, axes = plt.subplots(n_joints, 1, figsize=(14, 2.5 * n_joints), sharex=True)
    fig.suptitle(f"Episode {episode} — Reconstructed Positions", fontsize=14, fontweight="bold")
    if n_joints == 1:
        axes = [axes]
    for j, ax in enumerate(axes):
        ax.plot(t, gt_pos[:, j], color="#2196F3", alpha=0.8, linewidth=1.2, label="Ground Truth")
        ax.plot(t, pred_pos[:, j], color="#FF5722", alpha=0.8, linewidth=1.2, label="Predicted")
        ax.fill_between(t, gt_pos[:, j], pred_pos[:, j], alpha=0.15, color="#9C27B0")
        ax.set_ylabel(_dim_label(j), fontsize=9)
        ax.grid(True, alpha=0.3)
        if j == 0:
            ax.legend(loc="upper right", fontsize=8)
    axes[-1].set_xlabel("Time (s)", fontsize=10)
    fig.tight_layout()
    return fig


def plot_error_heatmap(predicted, ground_truth, episode, fps):
    plt = _setup_matplotlib()
    error = np.abs(predicted - ground_truth)
    n_steps, n_joints = error.shape
    labels = [_dim_label(j) for j in range(n_joints)]
    t = np.arange(n_steps) / fps
    fig, ax = plt.subplots(figsize=(14, 3))
    im = ax.imshow(
        error.T,
        aspect="auto",
        cmap="hot",
        interpolation="nearest",
        extent=[t[0], t[-1], n_joints - 0.5, -0.5],
    )
    ax.set_yticks(range(n_joints))
    ax.set_yticklabels(labels, fontsize=9)
    ax.set_xlabel("Time (s)", fontsize=10)
    ax.set_title(f"Episode {episode} — Absolute Error Heatmap", fontsize=12, fontweight="bold")
    fig.colorbar(im, ax=ax, label="Absolute Error")
    fig.tight_layout()
    return fig


def plot_summary_panel(predicted, ground_truth, inference_times, episode, fps):
    plt = _setup_matplotlib()
    error = np.abs(predicted - ground_truth)
    n_steps, n_joints = predicted.shape
    labels = [_dim_label(j) for j in range(n_joints)]
    t = np.arange(n_steps) / fps
    colors = plt.cm.tab10(np.linspace(0, 1, n_joints))
    fig, axes = plt.subplots(2, 2, figsize=(14, 8))
    fig.suptitle(f"Episode {episode} — Inference Summary", fontsize=14, fontweight="bold")
    ax = axes[0, 0]
    for j in range(n_joints):
        ax.plot(t, ground_truth[:, j], color=colors[j], alpha=0.6, linewidth=1.0)
        ax.plot(t, predicted[:, j], color=colors[j], alpha=0.6, linewidth=1.0, linestyle="--")
    ax.set_xlabel("Time (s)", fontsize=9)
    ax.set_ylabel("Action delta", fontsize=9)
    ax.set_title("All Dimensions (solid=GT, dashed=pred)", fontsize=10)
    ax.grid(True, alpha=0.3)
    ax = axes[0, 1]
    ax.boxplot([error[:, j] for j in range(n_joints)], tick_labels=labels, patch_artist=True)
    ax.set_ylabel("Absolute Error", fontsize=9)
    ax.set_title("Error Distribution per Dimension", fontsize=10)
    ax.tick_params(axis="x", rotation=45, labelsize=6 if n_joints > 8 else 8)
    ax.grid(True, alpha=0.3, axis="y")
    ax = axes[1, 0]
    inf_ms = inference_times * 1000
    ax.plot(inf_ms, color="#4CAF50", alpha=0.7, linewidth=0.8)
    ax.axhline(y=1000 / fps, color="#F44336", linestyle="--", alpha=0.7, label=f"Realtime ({1000 / fps:.1f}ms)")
    ax.set_xlabel("Step", fontsize=9)
    ax.set_ylabel("Inference time (ms)", fontsize=9)
    ax.set_title("Inference Latency", fontsize=10)
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)
    ax.set_ylim(0, min(np.percentile(inf_ms, 99) * 2, inf_ms.max() * 1.1))
    ax = axes[1, 1]
    per_joint_mae = np.mean(error, axis=0)
    bars = ax.bar(labels, per_joint_mae, color=colors[:n_joints], alpha=0.7)
    ax.set_ylabel("MAE", fontsize=9)
    ax.set_title("Per-Dimension Mean Absolute Error", fontsize=10)
    ax.tick_params(axis="x", rotation=45, labelsize=6 if n_joints > 8 else 8)
    ax.grid(True, alpha=0.3, axis="y")
    for bar, val in zip(bars, per_joint_mae, strict=False):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(), f"{val:.4f}", ha="center", va="bottom", fontsize=7)
    fig.tight_layout()
    return fig


def plot_aggregate_summary(episode_metrics):
    plt = _setup_matplotlib()
    episodes = [m["episode"] for m in episode_metrics]
    ep_labels = [str(e) for e in episodes]
    maes = [m["mae"] for m in episode_metrics]
    mses = [m["mse"] for m in episode_metrics]
    throughputs = [m["throughput_hz"] for m in episode_metrics]
    per_dim = np.array([m["per_dim_mae"] for m in episode_metrics])
    n_dims = per_dim.shape[1]
    labels = [_dim_label(j) for j in range(n_dims)]
    colors = plt.cm.tab10(np.linspace(0, 1, n_dims))
    fig, axes = plt.subplots(2, 2, figsize=(14, 8))
    fig.suptitle(f"Aggregate Inference Summary ({len(episodes)} episodes)", fontsize=14, fontweight="bold")
    ax = axes[0, 0]
    ax.bar(ep_labels, maes, color="#2196F3", alpha=0.7)
    ax.axhline(y=np.mean(maes), color="#F44336", linestyle="--", alpha=0.7, label=f"Mean: {np.mean(maes):.6f}")
    ax.set_xlabel("Episode")
    ax.set_ylabel("MAE")
    ax.set_title("Per-Episode MAE")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3, axis="y")
    ax = axes[0, 1]
    mean_per_dim = np.mean(per_dim, axis=0)
    ax.bar(labels, mean_per_dim, color=colors[:n_dims], alpha=0.7)
    for j in range(n_dims):
        ax.scatter([labels[j]] * len(episodes), per_dim[:, j], color=colors[j], s=15, alpha=0.5, zorder=3)
    ax.set_ylabel("MAE")
    ax.set_title("Per-Dimension MAE (mean + scatter)")
    ax.tick_params(axis="x", rotation=45, labelsize=6 if n_dims > 8 else 8)
    ax.grid(True, alpha=0.3, axis="y")
    ax = axes[1, 0]
    bar_colors = ["#4CAF50" if t >= 30 else "#FF5722" for t in throughputs]
    ax.bar(ep_labels, throughputs, color=bar_colors, alpha=0.7)
    ax.axhline(y=30, color="#F44336", linestyle="--", alpha=0.7, label="Realtime (30 Hz)")
    ax.set_xlabel("Episode")
    ax.set_ylabel("Throughput (Hz)")
    ax.set_title("Per-Episode Throughput")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3, axis="y")
    ax = axes[1, 1]
    ax.bar(ep_labels, mses, color="#9C27B0", alpha=0.7)
    ax.axhline(y=np.mean(mses), color="#F44336", linestyle="--", alpha=0.7, label=f"Mean: {np.mean(mses):.7f}")
    ax.set_xlabel("Episode")
    ax.set_ylabel("MSE")
    ax.set_title("Per-Episode MSE")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3, axis="y")
    fig.tight_layout()
    return fig


def _find_data_file(ds_dir: str, ep_idx: int, episode_record: dict | None = None) -> str | None:
    info_path = os.path.join(ds_dir, "meta", "info.json")
    if os.path.exists(info_path):
        with open(info_path) as f:
            ds_info = json.load(f)
    else:
        ds_info = {}

    if episode_record is not None:
        chunk_index = episode_record.get("data/chunk_index")
        file_index = episode_record.get("data/file_index")
        if isinstance(chunk_index, int) and isinstance(file_index, int):
            data_path_template = ds_info.get("data_path")
            if isinstance(data_path_template, str):
                candidate = os.path.join(
                    ds_dir,
                    data_path_template.format(chunk_index=chunk_index, file_index=file_index),
                )
            else:
                candidate = os.path.join(
                    ds_dir,
                    "data",
                    f"chunk-{chunk_index:03d}",
                    f"file-{file_index:03d}.parquet",
                )
            return candidate if os.path.exists(candidate) else None

    chunks_size = ds_info.get("chunks_size", 1000)
    ep_chunk = ep_idx // chunks_size
    candidates = [
        os.path.join(ds_dir, "data", f"chunk-{ep_chunk:03d}", f"episode_{ep_idx:06d}.parquet"),
        os.path.join(ds_dir, "data", f"chunk-{ep_idx:03d}", f"file-{ep_idx:03d}.parquet"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


def _find_video_file(ds_dir: str, video_key: str, ep_idx: int, episode_record: dict | None = None) -> str | None:
    info_path = os.path.join(ds_dir, "meta", "info.json")
    if os.path.exists(info_path):
        with open(info_path) as f:
            ds_info = json.load(f)
    else:
        ds_info = {}

    if episode_record is not None:
        chunk_index = episode_record.get(f"videos/{video_key}/chunk_index")
        file_index = episode_record.get(f"videos/{video_key}/file_index")
        if isinstance(chunk_index, int) and isinstance(file_index, int):
            video_path_template = ds_info.get("video_path")
            if isinstance(video_path_template, str):
                candidate = os.path.join(
                    ds_dir,
                    video_path_template.format(
                        video_key=video_key,
                        chunk_index=chunk_index,
                        file_index=file_index,
                    ),
                )
            else:
                candidate = os.path.join(
                    ds_dir,
                    "videos",
                    video_key,
                    f"chunk-{chunk_index:03d}",
                    f"file-{file_index:03d}.mp4",
                )
            return candidate if os.path.exists(candidate) else None

    chunks_size = ds_info.get("chunks_size", 1000)
    ep_chunk = ep_idx // chunks_size
    candidates = [
        os.path.join(ds_dir, "videos", video_key, f"chunk-{ep_chunk:03d}", f"episode_{ep_idx:06d}.mp4"),
        os.path.join(ds_dir, "videos", video_key, f"chunk-{ep_idx:03d}", f"file-{ep_idx:03d}.mp4"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


def _load_task_metadata(ds_dir: str) -> tuple[dict[int, str], dict[int, str], dict[int, dict]]:
    task_descriptions: dict[int, str] = {}
    tasks_parquet_path = Path(ds_dir) / "meta" / "tasks.parquet"
    if tasks_parquet_path.exists():
        import pyarrow.parquet as pq

        task_table = pq.read_table(tasks_parquet_path)
        task_columns = task_table.to_pydict()
        text_column = (
            "task"
            if "task" in task_columns
            else next(
                (name for name in task_columns if name != "task_index"),
                None,
            )
        )
        if text_column is not None:
            for task_index, task in zip(
                task_columns.get("task_index", []),
                task_columns[text_column],
                strict=True,
            ):
                if isinstance(task_index, int) and isinstance(task, str) and task.strip():
                    task_descriptions[task_index] = task
    else:
        tasks_path = os.path.join(ds_dir, "meta", "tasks.jsonl")
        if os.path.exists(tasks_path):
            with open(tasks_path) as f:
                for line in f:
                    record = json.loads(line)
                    task_index = record.get("task_index")
                    task = record.get("task")
                    if isinstance(task_index, int) and isinstance(task, str) and task.strip():
                        task_descriptions[task_index] = task

    episode_tasks: dict[int, str] = {}
    episode_records: dict[int, dict] = {}
    episode_files = sorted((Path(ds_dir) / "meta" / "episodes").glob("chunk-*/file-*.parquet"))
    if episode_files:
        import pyarrow.parquet as pq

        for episode_file in episode_files:
            for record in pq.read_table(episode_file).to_pylist():
                episode_index = record.get("episode_index")
                task = _task_from_episode_record(record, task_descriptions)
                if isinstance(episode_index, int):
                    episode_records[episode_index] = record
                    if task:
                        episode_tasks[episode_index] = task
    else:
        episodes_path = os.path.join(ds_dir, "meta", "episodes.jsonl")
        if os.path.exists(episodes_path):
            with open(episodes_path) as f:
                for line in f:
                    record = json.loads(line)
                    episode_index = record.get("episode_index")
                    task = _task_from_episode_record(record, task_descriptions)
                    if isinstance(episode_index, int):
                        episode_records[episode_index] = record
                        if task:
                            episode_tasks[episode_index] = task

    return task_descriptions, episode_tasks, episode_records


def _task_from_episode_record(record: dict, task_descriptions: dict[int, str]) -> str:
    tasks = record.get("tasks")
    if hasattr(tasks, "tolist"):
        tasks = tasks.tolist()
    if isinstance(tasks, str) and tasks.strip():
        return tasks
    if isinstance(tasks, list) and tasks:
        resolved_tasks = {
            task.strip() if isinstance(task, str) else task_descriptions.get(task, "") if isinstance(task, int) else ""
            for task in tasks
        }
        resolved_tasks.discard("")
        if len(resolved_tasks) == 1:
            return resolved_tasks.pop()
        if resolved_tasks:
            return ""

    task_index = record.get("task_index")
    if isinstance(task_index, int):
        return task_descriptions.get(task_index, "")
    return ""


def _resolve_frame_task(
    step: int,
    episode_index: int,
    data: dict[str, list],
    task_descriptions: dict[int, str],
    episode_tasks: dict[int, str],
) -> str:
    task_indices = data.get("task_index", [])
    if step < len(task_indices):
        task_index = task_indices[step]
        if isinstance(task_index, int) and task_index in task_descriptions:
            return task_descriptions[task_index]

    if episode_index in episode_tasks:
        return episode_tasks[episode_index]

    if len(task_descriptions) == 1:
        return next(iter(task_descriptions.values()))
    return ""


def _filter_episode_table(table: Any, episode_index: int, *, is_shared_file: bool) -> Any:
    if not is_shared_file or "episode_index" not in table.column_names:
        return table

    import pyarrow.compute as pc

    return table.filter(pc.equal(table["episode_index"], episode_index))


def _slice_episode_frames(
    frames: list[np.ndarray],
    episode_record: dict | None,
    image_key: str,
    fps: int | float,
) -> list[np.ndarray]:
    if episode_record is None:
        return frames

    from_timestamp = episode_record.get(f"videos/{image_key}/from_timestamp")
    to_timestamp = episode_record.get(f"videos/{image_key}/to_timestamp")
    if not isinstance(from_timestamp, (int, float)) or not isinstance(to_timestamp, (int, float)):
        return frames

    start_frame = max(0, round(from_timestamp * fps))
    end_frame = min(len(frames), round(to_timestamp * fps))
    return frames[start_frame:end_frame]


def main() -> int:
    global JOINT_NAMES

    import av
    import pyarrow.parquet as pq

    policy_repo_id = os.environ.get("POLICY_REPO_ID", "").strip()
    policy_type = os.environ.get("POLICY_TYPE", "act").strip().lower()
    dataset_repo_id = os.environ.get("DATASET_REPO_ID", "")
    task_prompt = os.environ.get("TASK_PROMPT", "").strip()
    policy_revision = os.environ.get("POLICY_REVISION", "").strip() or None
    dataset_revision = os.environ.get("DATASET_REVISION") or None
    eval_episodes = int(os.environ.get("EVAL_EPISODES", "10"))
    output_dir = Path(os.environ.get("OUTPUT_DIR", "/workspace/outputs/eval"))
    job_name = os.environ.get("JOB_NAME", "lerobot-eval")
    mlflow_enable = os.environ.get("MLFLOW_ENABLE", "false") == "true"

    if not policy_repo_id:
        print("[ERROR] POLICY_REPO_ID is required")
        return 1

    output_dir.mkdir(parents=True, exist_ok=True)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"[INFO] Using device: {device}")

    # Resolve dataset source
    dataset_dir_env = os.environ.get("DATASET_DIR", "")
    if dataset_dir_env and os.path.isdir(dataset_dir_env):
        dataset_dir = dataset_dir_env
        print(f"[INFO] Using blob-downloaded dataset: {dataset_dir}")
    elif dataset_repo_id and dataset_repo_id != "none":
        try:
            dataset_revision = resolve_hf_revision(dataset_repo_id, dataset_revision, revision_name="DATASET_REVISION")
        except ValueError as exc:
            print(f"[ERROR] {exc}")
            return 1
        from huggingface_hub import snapshot_download

        dataset_dir = snapshot_download(repo_id=dataset_repo_id, repo_type="dataset", revision=dataset_revision)
        print(f"[INFO] Dataset downloaded from HuggingFace: {dataset_dir}")
    else:
        print("[ERROR] Dataset source required: set DATASET_REPO_ID or blob storage params")
        return 1

    lifecycle_paths = {
        "candidate_manifest": os.environ.get("CANDIDATE_MANIFEST_PATH", ""),
        "training_record": os.environ.get("TRAINING_RECORD_PATH", ""),
        "dataset_manifest": os.environ.get("DATASET_MANIFEST_PATH", ""),
        "dataset_asset_id": os.environ.get("DATASET_ASSET_ID", ""),
        "evidence_job": os.environ.get("AZUREML_ROOT_RUN_ID", ""),
    }
    lifecycle_records = None
    if any(lifecycle_paths.values()):
        missing = sorted(name for name, value in lifecycle_paths.items() if not value)
        if missing:
            raise ContractError(f"Incomplete lifecycle evaluation inputs: {', '.join(missing)}")
        lifecycle_records = _verify_lifecycle_inputs(
            Path(policy_repo_id),
            Path(lifecycle_paths["candidate_manifest"]),
            Path(lifecycle_paths["training_record"]),
            Path(dataset_dir),
            Path(lifecycle_paths["dataset_manifest"]),
            lifecycle_paths["dataset_asset_id"],
            dataset_repo_id,
            policy_type,
            lifecycle_paths["evidence_job"],
        )

    # Load dataset info
    with open(os.path.join(dataset_dir, "meta", "info.json")) as f:
        info = json.load(f)
    fps = info["fps"]
    task_descriptions, episode_tasks, episode_records = _load_task_metadata(dataset_dir)

    # Resolve dimension labels from the dataset's action feature names so plots
    # carry real joint names; fall back to generic dim_N labels otherwise.
    action_names = info.get("features", {}).get("action", {}).get("names")
    JOINT_NAMES = list(action_names) if isinstance(action_names, list) else []

    # Identify video keys from features
    features = info.get("features", {})
    video_keys = [k for k, v in features.items() if v.get("dtype") in ("video", "image")]
    image_keys = [key for key in video_keys if key.startswith("observation.images.")] or video_keys
    if not image_keys:
        print("[ERROR] Dataset has no video or image observation features")
        return 1

    # Load policy (normalization is handled internally by select_action)
    print(f"[INFO] Loading policy from: {policy_repo_id}")
    try:
        policy_revision = resolve_hf_revision(policy_repo_id, policy_revision, revision_name="POLICY_REVISION")
    except ValueError as exc:
        print(f"[ERROR] {exc}")
        return 1
    try:
        bundle = load_pretrained_policy(
            repo_id=policy_repo_id,
            policy_type=policy_type,
            device=str(device),
            revision=policy_revision,
        )
    except ValueError as exc:
        print(f"[ERROR] {exc}")
        return 1
    policy = bundle.policy

    # Determine episode range
    episode_indices = sorted(episode_records)[:eval_episodes] if episode_records else list(range(eval_episodes))
    num_episodes = len(episode_indices)

    # Start MLflow run
    if mlflow_enable:
        import mlflow

        mlflow.start_run(run_name=job_name)
        mlflow.log_params(
            {
                "policy_repo_id": policy_repo_id,
                "policy_type": policy_type,
                "dataset_repo_id": dataset_repo_id,
                "task": task_prompt,
                "eval_episodes": num_episodes,
                "device": str(device),
                "fps": fps,
            }
        )

    all_episode_metrics = []
    coverage_reasons: set[str] = set()

    for ep in episode_indices:
        print(f"\n{'=' * 60}")
        print(f"Episode {ep}")
        print(f"{'=' * 60}")

        episode_record = episode_records.get(ep)
        data_file = _find_data_file(dataset_dir, ep, episode_record)
        if not data_file:
            print(f"  [WARNING] No data file for episode {ep}, skipping")
            continue
        table = pq.read_table(data_file)
        table = _filter_episode_table(table, ep, is_shared_file=episode_record is not None)
        data = {col: table[col].to_pylist() for col in table.column_names}
        if not data.get("timestamp"):
            print(f"  [WARNING] No data rows for episode {ep}, skipping")
            continue
        n_frames = len(data["timestamp"])

        video_files = {
            image_key: _find_video_file(dataset_dir, image_key, ep, episode_record) for image_key in image_keys
        }
        missing_video_keys = [image_key for image_key, video_file in video_files.items() if not video_file]
        if missing_video_keys:
            print(f"  [WARNING] Missing videos for episode {ep}: {', '.join(missing_video_keys)}, skipping")
            continue

        frames_by_key = {}
        for image_key, video_file in video_files.items():
            container = av.open(video_file)
            stream = container.streams.video[0]
            frames = [av_frame.to_ndarray(format="rgb24") for av_frame in container.decode(stream)]
            container.close()
            frames_by_key[image_key] = _slice_episode_frames(frames, episode_record, image_key, fps)
        empty_video_keys = [image_key for image_key, frames in frames_by_key.items() if not frames]
        if empty_video_keys:
            print(f"  [WARNING] Empty videos for episode {ep}: {', '.join(empty_video_keys)}, skipping")
            continue

        policy.reset()
        actions_predicted = []
        actions_ground_truth = []
        inference_times_list = []
        missing_task = False

        available_frames = min(len(frames) for frames in frames_by_key.values())
        for step in range(min(n_frames - 1, available_frames)):
            state = np.array(data["observation.state"][step], dtype=np.float32)
            gt_action = np.array(data["action"][step], dtype=np.float32)

            obs = {
                "observation.state": torch.from_numpy(state).float(),
                **{
                    image_key: torch.from_numpy(frames[step]).float().permute(2, 0, 1) / 255.0
                    for image_key, frames in frames_by_key.items()
                },
            }
            if bundle.policy_type in VLA_POLICY_TYPES:
                frame_task = task_prompt or _resolve_frame_task(step, ep, data, task_descriptions, episode_tasks)
                if not frame_task:
                    print(
                        f"[WARNING] Episode {ep} frame {step} has no task description required by {bundle.policy_type}"
                    )
                    coverage_reasons.add("missing_task_metadata")
                    missing_task = True
                    break
                obs["task"] = frame_task
            processed_obs = bundle.preprocessor(obs)

            t_start = time.time()
            with torch.inference_mode():
                action = policy.select_action(processed_obs)
            t_inf = time.time() - t_start
            inference_times_list.append(t_inf)

            processed_action = postprocess_action(bundle.postprocessor, action, bundle.policy_type)
            action_np = processed_action.squeeze(0).cpu().numpy()
            actions_predicted.append(action_np)
            actions_ground_truth.append(gt_action)

        if missing_task or not actions_predicted:
            print(f"  [WARNING] No inference steps for episode {ep}, skipping")
            continue

        pred = np.array(actions_predicted)
        gt = np.array(actions_ground_truth)
        inf_times = np.array(inference_times_list)

        mse = float(np.mean((pred - gt) ** 2))
        mae = float(np.mean(np.abs(pred - gt)))
        per_dim_mae = np.mean(np.abs(pred - gt), axis=0)
        avg_inf_ms = float(np.mean(inf_times) * 1000)
        throughput = float(1.0 / np.mean(inf_times))
        _require_finite_metrics(
            {
                "mse": mse,
                "mae": mae,
                "avg_inference_ms": avg_inf_ms,
                "throughput_hz": throughput,
            }
        )

        print(f"  Steps: {len(pred)}, MSE: {mse:.6f}, MAE: {mae:.6f}")
        print(f"  Avg inference: {avg_inf_ms:.1f}ms, Throughput: {throughput:.1f} Hz")

        ep_metrics = {
            "episode": ep,
            "steps": len(pred),
            "mse": mse,
            "mae": mae,
            "avg_inference_ms": avg_inf_ms,
            "throughput_hz": throughput,
            "per_dim_mae": per_dim_mae.tolist(),
        }
        all_episode_metrics.append(ep_metrics)

        npz_path = output_dir / f"ep{ep:03d}_predictions.npz"
        np.savez(npz_path, predicted=pred, ground_truth=gt, inference_times=inf_times)

        if mlflow_enable:
            import matplotlib
            import mlflow

            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            mlflow.log_metrics(
                {
                    f"ep{ep}_mse": mse,
                    f"ep{ep}_mae": mae,
                    f"ep{ep}_avg_inference_ms": avg_inf_ms,
                    f"ep{ep}_throughput_hz": throughput,
                }
            )

            for plot_fn, name in [
                (plot_action_deltas, "action_deltas"),
                (plot_cumulative_positions, "cumulative_positions"),
                (plot_error_heatmap, "error_heatmap"),
            ]:
                fig = plot_fn(pred, gt, ep, fps)
                mlflow.log_figure(fig, f"plots/episode_{ep:03d}/{name}.png")
                plt.close(fig)

            fig = plot_summary_panel(pred, gt, inf_times, ep, fps)
            mlflow.log_figure(fig, f"plots/episode_{ep:03d}/summary_panel.png")
            plt.close(fig)

            mlflow.log_artifact(str(npz_path), "predictions")
            print(f"  Logged 4 plots + metrics to MLflow for episode {ep}")

    # Aggregate metrics
    if all_episode_metrics:
        agg_mse = float(np.mean([m["mse"] for m in all_episode_metrics]))
        agg_mae = float(np.mean([m["mae"] for m in all_episode_metrics]))
        agg_inf_ms = float(np.mean([m["avg_inference_ms"] for m in all_episode_metrics]))
        agg_throughput = float(np.mean([m["throughput_hz"] for m in all_episode_metrics]))
    else:
        agg_mse = agg_mae = agg_inf_ms = agg_throughput = None

    evaluation_status, evaluation_reasons = _evaluation_status(
        eval_episodes,
        len(all_episode_metrics),
        coverage_reasons,
    )

    results = {
        "job_name": job_name,
        "policy_repo_id": policy_repo_id,
        "policy_type": policy_type,
        "dataset_repo_id": dataset_repo_id,
        "device": str(device),
        "episodes_evaluated": len(all_episode_metrics),
        "aggregate_mse": agg_mse,
        "aggregate_mae": agg_mae,
        "aggregate_avg_inference_ms": agg_inf_ms,
        "aggregate_throughput_hz": agg_throughput,
        "per_episode": all_episode_metrics,
        "status": evaluation_status,
        "reasons": evaluation_reasons,
    }

    results_path = output_dir / "eval_results.json"
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n[INFO] Results saved to: {results_path}")

    _write_vla_schema_v1(
        output_dir=output_dir,
        aggregate={
            "mse": agg_mse,
            "mae": agg_mae,
            "avg_inference_ms": agg_inf_ms,
            "throughput_hz": agg_throughput,
        },
        per_episode=all_episode_metrics,
        dataset_repo_id=dataset_repo_id,
        policy_repo_id=policy_repo_id,
        episodes_requested=eval_episodes,
        coverage_reasons=coverage_reasons,
    )

    if lifecycle_records is not None:
        candidate_record, training_record, dataset_record = lifecycle_records
        evaluation_id = os.environ.get("AZUREML_RUN_ID") or lifecycle_paths["evidence_job"]
        _write_evaluation_record(
            output_dir,
            candidate_record,
            training_record,
            dataset_record,
            lifecycle_paths["evidence_job"],
            evaluation_id,
            eval_episodes,
            len(all_episode_metrics),
            evaluation_reasons,
            {
                "mse": agg_mse,
                "mae": agg_mae,
                "avg_inference_ms": agg_inf_ms,
                "throughput_hz": agg_throughput,
            },
        )

    if mlflow_enable:
        import mlflow

        if agg_mse is not None:
            mlflow.log_metrics(
                {
                "aggregate_mse": agg_mse,
                "aggregate_mae": agg_mae,
                "aggregate_avg_inference_ms": agg_inf_ms,
                "aggregate_throughput_hz": agg_throughput,
                }
            )
        mlflow.log_artifact(str(results_path))

        if len(all_episode_metrics) >= 2:
            import matplotlib

            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            fig = plot_aggregate_summary(all_episode_metrics)
            mlflow.log_figure(fig, "plots/aggregate_summary.png")
            plt.close(fig)
            print("[INFO] Logged aggregate summary plot to MLflow")

        mlflow.end_run()
        print("[INFO] MLflow run completed with plots and metrics")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"[ERROR] Evaluation failed: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
