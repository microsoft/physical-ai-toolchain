# cspell:ignore wsid
"""Link an observability bundle to a published viewer release, its Azure ML data asset, and an MLflow run.

The Dataset Analysis Tool release workflow publishes the release and registers its data asset. This
script reads a local copy of that release, resolves the registered asset, and logs, attaches to,
verifies, and describes the observability run. It never creates, changes, or archives data assets.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import mlflow
from azure.identity import DefaultAzureCredential
from mlflow.entities import Metric, Param
from mlflow.exceptions import MlflowException
from mlflow.store.artifact.artifact_repository_registry import get_artifact_repository
from mlflow.tracking import MlflowClient
from mlflow.tracking.context.registry import resolve_tags
from observe import configure_logging, sha256_file, write_json

logger = logging.getLogger(__name__)

EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXIT_ERROR = 2

RECORD_SCHEMA = "1.0.0"
ARM_ENDPOINT = "https://management.azure.com"
ARM_API_VERSION = "2024-04-01"
MARKER_PATH = ".published.json"
MANIFEST_PATH = "metadata/release-manifest.json"
STATISTICS_PATH = "metadata/release-statistics.json"
CHECKSUMS_PATH = "checksums.sha256"
ASSET_NAME_MAX_LENGTH = 255
ASSET_NAME_SUFFIX_LENGTH = 12
ASSET_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,29}$")
UNSAFE_ASSET_NAME_CHARS = re.compile(r"[^a-z0-9]+")
UNSAFE_METRIC_CHARS = re.compile(r"[^A-Za-z0-9_\-./ ]")
METRIC_KEY_MAX = 250
TAG_VALUE_MAX = 500
RUN_KIND = "dataset-observability"
DEFAULT_EXPERIMENT = "dataset-observability"
FINISHED_STATES = {"FINISHED", "COMPLETED"}
BATCH_SIZE = 1000
PACE_S = 2.0
THROTTLE_WAIT_S = 60.0
CONFLICT_WAIT_S = 5.0
RETRY_ATTEMPTS = 5
RETRYABLE_MARKERS = ("429", "Etag conflict", "500", "502", "503", "TEMPORARILY_UNAVAILABLE")
FRAME_STRIDE = 6
METRIC_SKIP = frozenset({"thresholds", "layout_notes", "dataset/tasks", "dataset/name"})
EPISODE_SKIP = frozenset({"episode", "tasks", "start", "end", "start_pc"})


def create_parser() -> argparse.ArgumentParser:
    """Create and configure the argument parser."""
    parser = argparse.ArgumentParser(description="Link an observability bundle to a viewer release in Azure ML")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    release = sub.add_parser("release", help="Verify a release copy against the bundle and resolve its data asset")
    release.add_argument("release_root", type=Path, help="Local copy of a published viewer release")
    release.add_argument("--bundle", type=Path, required=True, help="Bundle written by observe.py for release_root")
    release.add_argument(
        "--subscription", default=os.environ.get("AZURE_SUBSCRIPTION_ID"), help="Azure subscription ID"
    )
    release.add_argument("--resource-group", default=os.environ.get("AZURE_RESOURCE_GROUP"), help="Resource group")
    release.add_argument("--workspace", default=os.environ.get("AZUREML_WORKSPACE_NAME"), help="Azure ML workspace")
    release.add_argument("--data-asset", help="Asset name when it differs from the viewer's derived name")
    release.add_argument("--no-asset", action="store_true", help="Skip asset resolution for unregistered releases")
    release.add_argument("-o", "--output", type=Path, required=True, help="Release record JSON")

    log = sub.add_parser("log", help="Create or resume the observability run and one child run per episode")
    log.add_argument("bundle", type=Path, help="Bundle written by observe.py")
    log.add_argument("--release", type=Path, required=True, help="Release record from the release command")
    log.add_argument("--media", type=Path, help="Media folder written by media.py")
    log.add_argument("--experiment", default=DEFAULT_EXPERIMENT, help="MLflow experiment name")
    log.add_argument("--run-name", help="Run name (default: dataset ID and release ID)")
    log.add_argument("--frame-stride", type=int, default=FRAME_STRIDE, help="Log every Nth frame of each series")
    log.add_argument("--tag", action="append", default=[], metavar="KEY=VALUE", help="Extra run tag")
    log.add_argument("--resume", metavar="RUN_ID", help="Finish an interrupted run instead of creating one")
    log.add_argument("--allow-duplicate", action="store_true", help="Log again when a run already covers the release")
    log.add_argument("--dry-run", action="store_true", help="Build every payload and print counts only")
    log.add_argument("-o", "--output", type=Path, required=True, help="Run record JSON")

    attach = sub.add_parser("attach", help="Log files to the finished run and record their digests")
    attach.add_argument("record", type=Path, help="Run record from the log command; updated in place")
    attach.add_argument("--file", action="append", default=[], metavar="PATH[=DIR]", help="File and artifact folder")
    attach.add_argument("--tag", action="append", default=[], metavar="KEY=VALUE", help="Tag to set on the run")

    verify = sub.add_parser("verify", help="Check the asset, run, child runs, and artifacts against the release")
    verify.add_argument("--release", type=Path, required=True, help="Release record from the release command")
    verify.add_argument("--run", type=Path, required=True, help="Run record from the log command")
    verify.add_argument("-o", "--output", type=Path, required=True, help="Verification result JSON")

    lineage = sub.add_parser("lineage", help="Write the lineage panel input for render.py --lineage")
    lineage.add_argument("--release", type=Path, required=True, help="Release record from the release command")
    lineage.add_argument("--run", type=Path, help="Run record from the log command")
    lineage.add_argument("-o", "--output", type=Path, required=True, help="Lineage JSON")
    return parser


class Checks:
    """Ordered pass or fail results mirrored to the log."""

    def __init__(self) -> None:
        self.items: list[dict] = []

    def add(self, name: str, ok: bool, detail: object = None) -> bool:
        self.items.append({"check": name, "ok": bool(ok), "detail": detail})
        (logger.info if ok else logger.error)(
            "%s %s%s", "PASS" if ok else "FAIL", name, "" if detail is None else f": {detail}"
        )
        return bool(ok)

    @property
    def failed(self) -> list[str]:
        return [item["check"] for item in self.items if not item["ok"]]


def read_json(path: Path) -> dict | list:
    """Parse a UTF-8 JSON file."""
    return json.loads(path.read_text(encoding="utf-8"))


def parse_tags(pairs: list[str]) -> dict[str, str]:
    """KEY=VALUE arguments as a mapping."""
    tags = {}
    for pair in pairs:
        key, separator, value = pair.partition("=")
        if not separator or not key:
            raise ValueError(f"tag must be KEY=VALUE: {pair}")
        tags[key] = value
    return tags


def data_asset_name(dataset_id: str) -> str:
    """Asset name the viewer registers for a dataset; must match azureml_data_asset_name in the viewer backend."""
    digest = hashlib.sha256(dataset_id.encode("utf-8")).hexdigest()[:ASSET_NAME_SUFFIX_LENGTH]
    slug = UNSAFE_ASSET_NAME_CHARS.sub("-", dataset_id.lower()).strip("-")
    return f"{slug[: ASSET_NAME_MAX_LENGTH - len(digest) - 1].rstrip('-')}-{digest}"


def workspace_path(subscription: str, resource_group: str, workspace: str) -> str:
    """Azure Resource Manager ID of a workspace."""
    return (
        f"/subscriptions/{subscription}/resourceGroups/{resource_group}"
        f"/providers/Microsoft.MachineLearningServices/workspaces/{workspace}"
    )


def arm_get(credential: DefaultAzureCredential, resource: str) -> dict | None:
    """GET an Azure Resource Manager resource; None when it does not exist."""
    token = credential.get_token(f"{ARM_ENDPOINT}/.default").token
    request = urllib.request.Request(
        f"{ARM_ENDPOINT}{resource}?api-version={ARM_API_VERSION}", headers={"Authorization": f"Bearer {token}"}
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def studio_url(workspace: dict, path: str) -> str:
    """Azure ML studio link to a workspace-scoped page."""
    wsid = (
        f"/subscriptions/{workspace['subscription']}/resourcegroups/{workspace['resource_group']}"
        f"/workspaces/{workspace['name']}"
    )
    return f"https://ml.azure.com/{path}?wsid={wsid}&tid={workspace['tenant']}"


def read_release(root: Path) -> tuple[dict, dict, bytes, bytes]:
    """Marker, manifest, manifest bytes, and checksums bytes of a published release copy."""
    marker = json.loads((root / MARKER_PATH).read_text(encoding="utf-8"))
    manifest_bytes = (root / MANIFEST_PATH).read_bytes()
    return marker, json.loads(manifest_bytes), manifest_bytes, (root / CHECKSUMS_PATH).read_bytes()


def parse_checksums(payload: bytes) -> dict[str, str]:
    """Relative path to SHA-256 from checksums.sha256; rejects malformed or repeated entries."""
    checksums: dict[str, str] = {}
    for line in payload.decode("utf-8").splitlines():
        digest, separator, relative = line.partition("  ")
        if separator != "  " or not re.fullmatch(r"[0-9a-f]{64}", digest) or relative in checksums:
            raise ValueError(f"invalid {CHECKSUMS_PATH} entry: {line[:80]}")
        checksums[relative] = digest
    return checksums


def run_release(args: argparse.Namespace) -> int:
    """Verify the bundle was computed from the published release bytes and resolve the registered asset."""
    missing = [
        flag
        for flag, value in (
            ("--subscription", args.subscription),
            ("--resource-group", args.resource_group),
            ("--workspace", args.workspace),
        )
        if not value
    ]
    if missing:
        logger.error("set %s or the matching environment variables", ", ".join(missing))
        return EXIT_ERROR
    bundle = read_json(args.bundle / "manifest.json")
    episodes = read_json(args.bundle / "episodes.json")
    try:
        marker, manifest, manifest_bytes, checksums_bytes = read_release(args.release_root)
        checksums = parse_checksums(checksums_bytes)
    except (OSError, ValueError, KeyError) as exc:
        logger.error("%s is not a published release copy: %s", args.release_root, exc)
        return EXIT_ERROR

    dataset_id, release_id = marker.get("dataset_id"), marker.get("release_id")
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    files = {entry["path"]: entry["sha256"] for entry in manifest["files"]}
    released = {**files, MANIFEST_PATH: manifest_sha256, CHECKSUMS_PATH: hashlib.sha256(checksums_bytes).hexdigest()}
    inputs = bundle["inputs"]
    sources = {source["dataset_id"] for source in manifest["source_provenance"]}
    checks = Checks()
    checks.add(
        "marker matches manifest",
        release_id == manifest["release_id"] and sources == {dataset_id},
        f"{dataset_id}/{release_id}",
    )
    checks.add("release ID is a valid asset version", ASSET_VERSION.fullmatch(release_id or "") is not None, release_id)
    checks.add(
        "checksums cover the manifest inventory", set(checksums) == {MANIFEST_PATH, *files}, f"{len(files)} files"
    )
    checks.add("manifest matches checksums", checksums.get(MANIFEST_PATH) == manifest_sha256, manifest_sha256[:16])
    unknown = sorted(path for path in inputs if path not in released)
    changed = sorted(path for path, digest in inputs.items() if path in released and released[path] != digest)
    unread = sorted(path for path in files if path not in inputs)
    checks.add("bundle read only release files", not unknown, unknown[:3] or f"{len(inputs)} files")
    checks.add("bundle inputs match release digests", not changed, changed[:3] or "all match")
    checks.add("bundle read every release file", not unread, unread[:3] or "complete")
    checks.add(
        "bundle episode count",
        len(episodes) == manifest["episode_count"],
        f"{len(episodes)} of {manifest['episode_count']}",
    )

    credential = DefaultAzureCredential()
    ws_resource = workspace_path(args.subscription, args.resource_group, args.workspace)
    ws = arm_get(credential, ws_resource)
    if ws is None:
        logger.error("workspace %s not found in %s", args.workspace, args.resource_group)
        return EXIT_ERROR
    workspace = {
        "subscription": args.subscription,
        "resource_group": args.resource_group,
        "name": ws["name"],
        "tenant": ws["properties"]["tenantId"],
        "tracking_uri": ws["properties"]["mlFlowTrackingUri"],
    }
    asset = None
    if not args.no_asset:
        name = args.data_asset or data_asset_name(dataset_id)
        body = arm_get(credential, f"{ws_resource}/data/{name}/versions/{release_id}")
        if checks.add("data asset registered for the release", body is not None, f"{name}:{release_id}"):
            properties = body["properties"]
            stored = properties.get("properties") or {}
            checks.add(
                "asset identity", (stored.get("dataset_id"), stored.get("release_id")) == (dataset_id, release_id)
            )
            checks.add(
                "asset manifest digest",
                stored.get("manifest_sha256") == manifest_sha256,
                (stored.get("manifest_sha256") or "")[:16],
            )
            checks.add("asset statistics digest", stored.get("statistics_sha256") == files.get(STATISTICS_PATH))
            checks.add("asset not archived", not properties.get("isArchived"))
            asset = {"name": name, "version": release_id, "data_uri": properties.get("dataUri"), "properties": stored}

    target = manifest["target_format"]
    record = {
        "schema_version": RECORD_SCHEMA,
        "verified": not checks.failed,
        "dataset_id": dataset_id,
        "release_id": release_id,
        "manifest_sha256": manifest_sha256,
        "statistics_sha256": files.get(STATISTICS_PATH),
        "target_format": f"{target['name']}:{target['version']}",
        "episode_count": manifest["episode_count"],
        "frame_count": manifest["frame_count"],
        "source_episode_index": {
            str(release): int(source) for source, release in manifest["episode_index_mapping"].items()
        },
        "bundle": {
            "inputs_digest": bundle["inputs_digest"],
            "tool_sha256": bundle["tool_sha256"],
            "episodes": len(episodes),
        },
        "workspace": workspace,
        "data_asset": asset,
        "checks": checks.items,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output, record)
    logger.info(
        "%d of %d checks passed; wrote %s", len(checks.items) - len(checks.failed), len(checks.items), args.output
    )
    return EXIT_FAILURE if checks.failed else EXIT_SUCCESS


def metric_key(key: str) -> str:
    """MLflow-safe metric key."""
    return UNSAFE_METRIC_CHARS.sub("_", key)[:METRIC_KEY_MAX]


def flatten(prefix: str, value: object, out: dict[str, float], skip: frozenset[str] = frozenset()) -> None:
    """Numeric leaves become values; lists and episode-keyed maps become counts."""
    if prefix in skip:
        return
    if isinstance(value, bool):
        out[metric_key(prefix)] = float(value)
    elif isinstance(value, int | float):
        if math.isfinite(value):
            out[metric_key(prefix)] = float(value)
    elif isinstance(value, list):
        out[metric_key(f"{prefix}/count")] = float(len(value))
    elif isinstance(value, dict):
        if value and all(str(key).isdigit() for key in value):
            out[metric_key(f"{prefix}/episodes")] = float(len(value))
            return
        for key, item in value.items():
            flatten(f"{prefix}/{key}" if prefix else str(key), item, out, skip)


def episode_scalars(record: dict) -> dict[str, float]:
    """Numeric values of one episode record."""
    values: dict[str, float] = {}
    flatten("", {k: v for k, v in record.items() if k not in EPISODE_SKIP}, values)
    return values


def frame_metrics(prefix: str, value: object, stride: int, now: int, out: list[Metric]) -> None:
    """Per-frame series every stride-th frame, keyed by frame index."""
    if isinstance(value, dict):
        for key, item in value.items():
            frame_metrics(f"{prefix}/{key}", item, stride, now, out)
    elif isinstance(value, list):
        key = metric_key(prefix)
        for index in range(0, len(value), stride):
            item = value[index]
            if isinstance(item, int | float) and not isinstance(item, bool) and math.isfinite(item):
                out.append(Metric(key, float(item), now, index))


def child_tags(record: dict, release: dict, media: dict | None) -> dict[str, str]:
    """Filterable tags for one episode run."""
    episode = record["episode"]
    outliers = sorted({item["metric"] for item in record.get("outliers") or []})
    tags = {
        "episode_index": str(episode),
        "source_episode_index": str(release["source_episode_index"].get(str(episode), "")),
        "dataset_release_id": release["release_id"],
        "outliers": ",".join(outliers)[:TAG_VALUE_MAX] or "none",
        "stalls": str(len(record.get("stalls") or [])),
        "start_condition": "" if record.get("start_condition") is None else str(record["start_condition"]),
        "clip_sha256": (media or {}).get("clip", {}).get("sha256", ""),
    }
    return {key: value for key, value in tags.items() if value}


def unlogged(client: MlflowClient, run_id: str, metrics: list[Metric]) -> list[Metric]:
    """Points whose key and step are not stored yet."""
    stored = {(key, point.step) for key in {m.key for m in metrics} for point in client.get_metric_history(run_id, key)}
    return [m for m in metrics if (m.key, m.step) not in stored]


def log_metrics(client: MlflowClient, run_id: str, metrics: list[Metric]) -> None:
    """Log paced batches; after a transient error, resend only the points that did not land."""
    for start in range(0, len(metrics), BATCH_SIZE):
        batch = metrics[start : start + BATCH_SIZE]
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                client.log_batch(run_id, metrics=batch)
                break
            except MlflowException as exc:
                text = str(exc)
                if not any(marker in text for marker in RETRYABLE_MARKERS) or attempt == RETRY_ATTEMPTS:
                    raise
                wait = THROTTLE_WAIT_S if "429" in text else CONFLICT_WAIT_S
                logger.warning(
                    "transient error on batch %d (attempt %d): %s; waiting %.0f s",
                    start // BATCH_SIZE,
                    attempt,
                    text.splitlines()[0][:120],
                    wait,
                )
                time.sleep(wait)
                batch = unlogged(client, run_id, batch)
                if not batch:
                    break
        time.sleep(PACE_S)


def search_all(client: MlflowClient, experiment_id: str, filter_string: str) -> list:
    """Every matching run; Azure ML returns at most 100 runs per page."""
    runs, token = [], None
    while True:
        page = client.search_runs([experiment_id], filter_string=filter_string, max_results=1000, page_token=token)
        runs += list(page)
        token = page.token
        if not token:
            return runs


def episode_runs(client: MlflowClient, experiment_id: str, parent_id: str) -> tuple[dict[int, str], dict[int, str]]:
    """Finished and unfinished child runs of a parent, keyed by episode index."""
    finished: dict[int, str] = {}
    unfinished: dict[int, str] = {}
    for run in search_all(client, experiment_id, f"tags.mlflow.parentRunId = '{parent_id}'"):
        if "episode_index" in run.data.tags:
            target = finished if run.info.status.upper() in FINISHED_STATES else unfinished
            target[int(run.data.tags["episode_index"])] = run.info.run_id
    return finished, unfinished


def run_log(args: argparse.Namespace) -> int:
    """Create or resume the observability run for a verified release."""
    release = read_json(args.release)
    if not release.get("verified"):
        logger.error("%s records failed release checks; resolve them and rerun the release command", args.release)
        return EXIT_ERROR
    bundle_manifest = read_json(args.bundle / "manifest.json")
    if bundle_manifest["inputs_digest"] != release["bundle"]["inputs_digest"]:
        logger.error("bundle %s is not the bundle checked by %s", args.bundle, args.release)
        return EXIT_ERROR
    metrics = read_json(args.bundle / "metrics.json")
    episodes = read_json(args.bundle / "episodes.json")
    series = read_json(args.bundle / "series.json")
    media = {item["episode"]: item for item in read_json(args.media / "video.json")} if args.media else {}
    media_manifest = read_json(args.media / "manifest.json") if args.media else {}
    try:
        extra_tags = parse_tags(args.tag)
    except ValueError as exc:
        logger.error("%s", exc)
        return EXIT_ERROR

    asset = release["data_asset"]
    dataset = metrics.get("dataset", {})
    layout = bundle_manifest.get("layout", {})
    tags = {
        "run_kind": RUN_KIND,
        "dataset_id": release["dataset_id"],
        "dataset_release_id": release["release_id"],
        "dataset_manifest_digest": release["manifest_sha256"],
        "dataset_trust": "verified",
        "dataset_target_format": release["target_format"],
        "observability_inputs_digest": bundle_manifest["inputs_digest"],
        "observability_tool_sha256": bundle_manifest["tool_sha256"],
        **(
            {"azureml_data_asset": f"{asset['name']}:{asset['version']}", "data_uri": asset["data_uri"]}
            if asset
            else {}
        ),
        **extra_tags,
    }
    params = {
        "dataset.codebase_version": dataset.get("codebase_version"),
        "dataset.robot_type": dataset.get("robot_type"),
        "dataset.fps": dataset.get("fps"),
        "dataset.cameras": ",".join(c["key"] if isinstance(c, dict) else str(c) for c in dataset.get("cameras", [])),
        "release.statistics_sha256": release["statistics_sha256"],
        "layout.state": layout.get("state"),
        "layout.action": layout.get("action"),
        "layout.clock": layout.get("clock"),
        "analysis.frame_stride": args.frame_stride,
        "analysis.media_tool_sha256": media_manifest.get("tool_sha256"),
        **{f"threshold.{key}": value for key, value in (metrics.get("thresholds") or {}).items()},
    }
    params = {key: str(value)[:TAG_VALUE_MAX] for key, value in params.items() if value not in (None, "")}
    run_name = args.run_name or f"{release['dataset_id']} · {release['release_id']}"
    link = f", and its digest matches data asset `{tags['azureml_data_asset']}`" if asset else ""
    description = (
        f"**Dataset observability for release `{release['release_id']}` of `{release['dataset_id']}`**\n\n"
        f"{dataset.get('robot_type', 'unknown robot')} · {len(episodes)} episodes · "
        f"{dataset.get('frames', 0):,} frames "
        f"at {dataset.get('fps', '?')} fps. Every analyzed file matches the release manifest{link}.\n\n"
        "Metrics tab: `episode/*` series use step = episode index. Child runs hold one run per episode with "
        f"`frame/*` series every {args.frame_stride}th frame (step = frame)."
    )

    now = int(time.time() * 1000)
    summary: dict[str, float] = {}
    flatten("", metrics, summary, METRIC_SKIP)
    parent_metrics = [Metric(key, value, now, 0) for key, value in summary.items()]
    for record in episodes:
        parent_metrics += [
            Metric(f"episode/{k}", v, now, record["episode"]) for k, v in episode_scalars(record).items()
        ]

    def child_metrics(record: dict) -> list[Metric]:
        values = [Metric(key, value, now, 0) for key, value in episode_scalars(record).items()]
        frame_metrics("frame", series.get(str(record["episode"]), {}), args.frame_stride, now, values)
        return values

    if args.dry_run:
        counts = [len(child_metrics(record)) for record in episodes]
        print(
            json.dumps(
                {
                    "parent_keys": len({m.key for m in parent_metrics}),
                    "parent_values": len(parent_metrics),
                    "child_runs": len(counts),
                    "child_values_min": min(counts, default=0),
                    "child_values_max": max(counts, default=0),
                    "child_values_total": sum(counts),
                    "params": len(params),
                    "tags": tags,
                    "sample_child_tags": child_tags(episodes[0], release, media.get(episodes[0]["episode"]))
                    if episodes
                    else {},
                },
                indent=1,
            )
        )
        return EXIT_SUCCESS

    os.environ.setdefault("MLFLOW_HTTP_REQUEST_MAX_RETRIES", "1")
    mlflow.set_tracking_uri(release["workspace"]["tracking_uri"])
    client = MlflowClient()
    experiment = mlflow.set_experiment(args.experiment)
    if args.resume:
        status = client.get_run(args.resume).info.status
        if status != "RUNNING":
            logger.error("run %s is %s; Azure ML cannot reopen a terminal run, so log a new run", args.resume, status)
            return EXIT_ERROR
        parent_id = args.resume
        pending = unlogged(client, parent_id, parent_metrics)
        logger.info("resuming %s: %d of %d parent values pending", parent_id, len(pending), len(parent_metrics))
    else:
        covering = [
            run
            for run in search_all(
                client, experiment.experiment_id, f"tags.dataset_manifest_digest = '{release['manifest_sha256']}'"
            )
            if run.info.status.upper() in FINISHED_STATES
            and not run.data.tags.get("mlflow.parentRunId")
            and run.data.tags.get("observability_inputs_digest") == bundle_manifest["inputs_digest"]
        ]
        if covering and not args.allow_duplicate:
            logger.error(
                "run %s already covers this release and bundle; pass --allow-duplicate to log another",
                covering[0].info.run_id,
            )
            return EXIT_ERROR
        tags_with_note = resolve_tags({**tags, "mlflow.note.content": description})
        parent_id = client.create_run(experiment.experiment_id, run_name=run_name, tags=tags_with_note).info.run_id
        client.log_batch(parent_id, params=[Param(key, value) for key, value in params.items()])
        pending = parent_metrics

    children: dict[int, str] = {}
    try:
        log_metrics(client, parent_id, pending)
        unfinished: dict[int, str] = {}
        if args.resume:
            children, unfinished = episode_runs(client, experiment.experiment_id, parent_id)
            logger.info("resuming with %d finished and %d unfinished child runs", len(children), len(unfinished))
        for record in episodes:
            index = record["episode"]
            if index in children:
                continue
            values = child_metrics(record)
            if index in unfinished:
                child_id = unfinished[index]
                values = unlogged(client, child_id, values)
            else:
                child_tags_all = {**child_tags(record, release, media.get(index)), "mlflow.parentRunId": parent_id}
                child_id = client.create_run(
                    experiment.experiment_id, run_name=f"episode-{index:03d}", tags=resolve_tags(child_tags_all)
                ).info.run_id
            log_metrics(client, child_id, values)
            client.set_terminated(child_id, "FINISHED")
            children[index] = child_id
            if len(children) % 25 == 0:
                logger.info("%d of %d child runs", len(children), len(episodes))
    except BaseException:
        logger.error(
            "run %s stopped after %d child runs; it stays RUNNING, rerun with --resume %s",
            parent_id,
            len(children),
            parent_id,
        )
        raise
    client.set_terminated(parent_id, "FINISHED")

    workspace = release["workspace"]
    record_out = {
        "schema_version": RECORD_SCHEMA,
        "experiment": args.experiment,
        "experiment_id": experiment.experiment_id,
        "run_id": parent_id,
        "run_name": run_name,
        "tracking_uri": workspace["tracking_uri"],
        "run_url": studio_url(workspace, f"runs/{parent_id}"),
        "experiment_url": studio_url(workspace, f"experiments/id/{experiment.experiment_id}"),
        "child_runs": {str(key): value for key, value in sorted(children.items())},
        "parent_metric_values": len(parent_metrics),
        "tags": tags,
        "artifacts": {},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output, record_out)
    logger.info("run %s with %d child runs: %s", parent_id, len(children), record_out["run_url"])
    return EXIT_SUCCESS


def run_attach(args: argparse.Namespace) -> int:
    """Log files to the finished run, set tags, and record each artifact digest in the run record."""
    record = read_json(args.record)
    try:
        tags = parse_tags(args.tag)
    except ValueError as exc:
        logger.error("%s", exc)
        return EXIT_ERROR
    os.environ.setdefault("MLFLOW_HTTP_REQUEST_MAX_RETRIES", "1")
    mlflow.set_tracking_uri(record["tracking_uri"])
    client = MlflowClient()
    for spec in args.file:
        path_text, _, folder = spec.partition("=")
        path = Path(path_text)
        client.log_artifact(record["run_id"], str(path), artifact_path=folder or None)
        artifact = f"{folder.strip('/')}/{path.name}" if folder else path.name
        record["artifacts"][artifact] = sha256_file(path)
        logger.info("logged %s (%.1f MB)", artifact, path.stat().st_size / 1e6)
    for key, value in tags.items():
        client.set_tag(record["run_id"], key, value)
    write_json(args.record, record)
    return EXIT_SUCCESS


def run_verify(args: argparse.Namespace) -> int:
    """Check that the asset, run, child runs, and artifacts still match the release record."""
    release, run = read_json(args.release), read_json(args.run)
    checks = Checks()
    asset = release["data_asset"]
    if asset:
        workspace = release["workspace"]
        resource = workspace_path(workspace["subscription"], workspace["resource_group"], workspace["name"])
        body = arm_get(DefaultAzureCredential(), f"{resource}/data/{asset['name']}/versions/{asset['version']}")
        if checks.add("data asset exists", body is not None, f"{asset['name']}:{asset['version']}"):
            stored = body["properties"].get("properties") or {}
            checks.add("asset manifest digest", stored.get("manifest_sha256") == release["manifest_sha256"])
            checks.add("asset not archived", not body["properties"].get("isArchived"))

    os.environ.setdefault("MLFLOW_HTTP_REQUEST_MAX_RETRIES", "1")
    mlflow.set_tracking_uri(run["tracking_uri"])
    client = MlflowClient()
    parent = client.get_run(run["run_id"])
    checks.add("run finished", parent.info.status in FINISHED_STATES, parent.info.status)
    expected = {
        "dataset_id": release["dataset_id"],
        "dataset_release_id": release["release_id"],
        "dataset_manifest_digest": release["manifest_sha256"],
        "dataset_trust": "verified",
        **({"azureml_data_asset": f"{asset['name']}:{asset['version']}"} if asset else {}),
    }
    wrong = {key: parent.data.tags.get(key) for key, value in expected.items() if parent.data.tags.get(key) != value}
    checks.add("run lineage tags", not wrong, wrong or "match")
    finished, unfinished = episode_runs(client, parent.info.experiment_id, parent.info.run_id)
    expected_episodes = release["bundle"]["episodes"]
    checks.add(
        "episode child runs",
        sorted(finished) == list(range(expected_episodes)) and not unfinished,
        f"{len(finished)} finished, {len(unfinished)} unfinished, {expected_episodes} expected",
    )
    if run["artifacts"]:
        repository = get_artifact_repository(parent.info.artifact_uri)
        with tempfile.TemporaryDirectory() as scratch:
            for artifact, digest in run["artifacts"].items():
                folder = artifact.rsplit("/", 1)[0] if "/" in artifact else ""
                listed = {item.path for item in repository.list_artifacts(folder or None)}
                if checks.add(f"artifact {artifact} listed", artifact in listed):
                    downloaded = Path(client.download_artifacts(parent.info.run_id, artifact, scratch))
                    checks.add(f"artifact {artifact} digest", sha256_file(downloaded) == digest, digest[:16])

    result = {
        "release": str(args.release),
        "run": str(args.run),
        "checks": checks.items,
        "passed": len(checks.items) - len(checks.failed),
        "failed": checks.failed,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output, result)
    logger.info("%d of %d checks passed", result["passed"], len(checks.items))
    return EXIT_FAILURE if checks.failed else EXIT_SUCCESS


def run_lineage(args: argparse.Namespace) -> int:
    """Write the lineage panel input for render.py from the release and run records."""
    release = read_json(args.release)
    run = read_json(args.run) if args.run else None
    asset = release["data_asset"]
    nodes = [
        {
            "kind": "Viewer release",
            "title": f"{release['dataset_id']} · {release['release_id']}",
            "mono": f"manifest sha256 {release['manifest_sha256'][:16]}",
            "lines": [
                f"{release['episode_count']} episodes · {release['frame_count']:,} frames · {release['target_format']}",
                "Published and verified by the Dataset Analysis Tool",
            ],
        }
    ]
    if asset:
        nodes.append(
            {
                "kind": "Azure ML data asset",
                "title": f"{asset['name']}:{asset['version']}",
                "mono": "uri_folder",
                "lines": [
                    "Registered by the viewer release workflow",
                    "Properties carry the manifest and statistics digests",
                ],
            }
        )
    nodes.append(
        {
            "kind": "Observability bundle",
            "title": f"inputs digest {release['bundle']['inputs_digest'][:16]}",
            "mono": f"observe.py sha256 {release['bundle']['tool_sha256'][:16]}",
            "lines": [
                "Every analyzed file matches the release manifest",
                f"{release['bundle']['episodes']} episodes analyzed",
            ],
        }
    )
    links = []
    if run:
        nodes.append(
            {
                "kind": "MLflow run",
                "title": run["run_name"],
                "mono": f"run {run['run_id']}",
                "lines": [
                    f"{len(run['child_runs'])} episode child runs in {run['experiment']}",
                    "Tagged with the release ID and manifest digest",
                ],
            }
        )
        links = [
            {"label": "Open the run in Azure ML studio", "url": run["run_url"]},
            {"label": "Open the experiment", "url": run["experiment_url"]},
        ]
    lineage = {
        "title": "Versions and lineage",
        "summary": (
            "Each stage records the identity of the one before it, "
            "so the observability run traces back to the released bytes."
        ),
        "nodes": nodes,
        "links": links,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output, lineage)
    logger.info("wrote %s", args.output)
    return EXIT_SUCCESS


def main() -> int:
    """Dispatch the subcommand."""
    args = create_parser().parse_args()
    configure_logging(args.verbose)
    for noisy in ("azure", "azureml", "urllib3", "mlflow"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    commands = {
        "release": run_release,
        "log": run_log,
        "attach": run_attach,
        "verify": run_verify,
        "lineage": run_lineage,
    }
    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
