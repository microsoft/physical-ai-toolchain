# cspell:ignore describedby figcaption IDAT IEND IHDR IIBBBBB integ luma noopener noreferrer pbtn pmeta ptrack
# cspell:ignore regrasp regrasps roledescription rowmark tabindex tmax tspan valuenow
"""Render the interactive observability report from a metrics bundle and a media folder.

The report is one self-contained HTML file: charts are SVG, clips and frames are embedded as base64,
and the page makes no network requests.
"""

from __future__ import annotations

import argparse
import base64
import collections
import hashlib
import html
import itertools
import json
import logging
import struct
import sys
import zlib
from datetime import UTC, datetime
from pathlib import Path
from string import Template
from typing import Any
from urllib.parse import urlparse

import numpy as np
from observe import configure_logging, inputs_digest, sha256_file

logger = logging.getLogger(__name__)

EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXIT_ERROR = 2

SKILL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TEMPLATE = SKILL_ROOT / "assets" / "report.template.html"
DEFAULT_STYLES = SKILL_ROOT / "assets" / "report.css"
HEAT_STOPS = ((0.0, "#f4f6f5"), (0.35, "#99f6e4"), (0.65, "#14b8a6"), (1.0, "#0d1a31"))
MAX_HEAT_COLUMNS = 1200
LAG_NOTE_FRAMES = 3
IDLE_NOTE_S = 1.0
REPEAT_SHARE = 0.5
UPDATE_RATIO_LOW = 0.5
DASH = "\N{EN DASH}"
MINUS = "\N{MINUS SIGN}"
TIMES = "\N{MULTIPLICATION SIGN}"


def create_parser() -> argparse.ArgumentParser:
    """Create and configure the argument parser."""
    parser = argparse.ArgumentParser(description="Render the interactive dataset observability report")
    parser.add_argument("bundle", type=Path, help="Bundle folder written by observe.py")
    parser.add_argument("--media", type=Path, required=True, help="Media folder written by media.py")
    parser.add_argument("--profile", type=Path, help="Optional narrative profile JSON (trusted HTML)")
    parser.add_argument("--lineage", type=Path, help="Optional lineage JSON rendered in the method section")
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE, help="Page template")
    parser.add_argument("--styles", type=Path, default=DEFAULT_STYLES, help="Stylesheet inlined into the page")
    parser.add_argument("-o", "--output", type=Path, required=True, help="Report HTML path")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    return parser


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def png_uri(rgb: np.ndarray) -> str:
    """Encode an RGB array as a PNG data URI without an imaging library."""
    rows, cols, _ = rgb.shape
    raw = b"".join(b"\x00" + rgb[y].astype(np.uint8).tobytes() for y in range(rows))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", cols, rows, 8, 2, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    return "data:image/png;base64," + base64.b64encode(png).decode()


def hex_rgb(color: str) -> np.ndarray:
    return np.array([int(color[k : k + 2], 16) for k in (1, 3, 5)], dtype=np.float64)


def ramp(values: np.ndarray) -> np.ndarray:
    out = np.zeros((*values.shape, 3))
    for (a, ca), (b, cb) in itertools.pairwise(HEAT_STOPS):
        mask = (values >= a) & (values <= b)
        weight = ((values - a) / (b - a))[mask][:, None]
        out[mask] = hex_rgb(ca) * (1 - weight) + hex_rgb(cb) * weight
    return out


def fmt(value: float | None, digits: int = 2) -> str:
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return DASH
    text = f"{value:,.{digits}f}"
    return text.replace("-", MINUS)


def signed(value: float | None, digits: int = 1) -> str:
    if value is None:
        return DASH
    text = f"{value:+.{digits}f}"
    return (f"{0:.{digits}f}" if float(text) == 0 else text).replace("-", MINUS)


def size_text(value: int) -> str:
    return f"{value / 1e6:,.1f} MB" if value < 1e9 else f"{value / 1e9:,.2f} GB"


def plural(count: int, word: str, many: str | None = None) -> str:
    return f"{count:,} {word if count == 1 else many or word + 's'}"


def ranges(values: list[int]) -> str:
    runs: list[list[int]] = []
    for value in sorted(values):
        if runs and value == runs[-1][1] + 1:
            runs[-1][1] = value
        else:
            runs.append([value, value])
    return ", ".join(str(a) if a == b else f"{a}{DASH}{b}" for a, b in runs)


def short_camera(key: str) -> str:
    return key.removeprefix("observation.images.").removeprefix("observation.")


def joint_tracking(tracking: dict) -> dict | None:
    """Return the arm-joint tracking entry: the group without a side label, else the first sided group."""
    if "joints" in tracking:
        return tracking["joints"]
    return next((value for label, value in tracking.items() if label.endswith("joints")), None)


def watch(ep: int, t: float, text: str | None = None) -> str:
    label = text or f"Episode {ep}"
    return (
        f'<button type="button" class="play-btn" data-watch="{ep}:{max(0.0, t):.3f}" '
        f'aria-label="Play episode {ep} at {max(0.0, t):.1f} seconds">{esc(label)}</button>'
    )


def time_axis(x0: float, sx: float, tmax: float, top: float, bottom: float, label_y: float) -> list[str]:
    """Vertical grid lines with second labels for a chart whose x axis is episode time."""
    step = next((s for s in (1, 2, 5, 10, 15, 30, 60, 120, 300, 600) if tmax / s <= 12), 1200)
    out = []
    for s in np.arange(0, tmax + 1e-9, step):
        x = x0 + s * sx
        out.append(f'<line class="grid" x1="{x:.1f}" x2="{x:.1f}" y1="{top}" y2="{bottom:.1f}"/>')
        out.append(f'<text x="{x:.1f}" y="{label_y}" text-anchor="middle">{s:g} s</text>')
    return out


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


class Report:
    """Collect bundle, media, profile, and lineage inputs and build every template value."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.records = load_json(args.bundle / "episodes.json")
        self.by_ep = {r["episode"]: r for r in self.records}
        self.eps = sorted(self.by_ep)
        self.n = len(self.eps)
        self.metrics = load_json(args.bundle / "metrics.json")
        self.series = {int(k): v for k, v in load_json(args.bundle / "series.json").items()}
        self.manifest = load_json(args.bundle / "manifest.json")
        self.media_dir = args.media
        self.media = load_json(args.media / "manifest.json")
        if self.media.get("inputs_digest") != inputs_digest(self.manifest["inputs"]):
            raise ValueError("media was built from different dataset inputs than the bundle; rerun media.py")
        self.video = {v["episode"]: v for v in load_json(args.media / "video.json")}
        for ep, video in self.video.items():
            if ep not in self.by_ep:
                raise ValueError(f"media covers episode {ep}, which the bundle does not contain")
            if video["clip"] and video["clip"]["frames"] != self.by_ep[ep]["frames"]:
                raise ValueError(f"episode {ep}: clip frames differ from data rows")
        self.profile = load_json(args.profile) if args.profile else {}
        self.lineage = load_json(args.lineage) if args.lineage else None
        self.fps = float(self.metrics["dataset"]["fps"])
        self.cameras = self.media["clip"]["cameras"]
        self.primary = self.cameras[0]
        self.gripper = next(iter(self.metrics["grippers"]), None)
        self.derive()

    # ------------------------------------------------------------------ derived facts
    def derive(self) -> None:
        conditions = collections.Counter(r["start_condition"] for r in self.records)
        self.info: dict[int, dict] = {}
        for ep in self.eps:
            record, video = self.by_ep[ep], self.video.get(ep)
            kinds = []
            flags: list[tuple[str, str]] = []
            if record["outliers"]:
                kinds.append("outlier")
                flags += [("", f"{o['metric']} z {o['z']:+.1f}") for o in record["outliers"]]
            capture = record["capture"]
            if capture and capture["gaps"]:
                kinds.append("gap")
                flags.append(("high", plural(len(capture["gaps"]), "capture gap")))
            if record["stalls"]:
                kinds.append("stall")
                flags.append(("high", plural(len(record["stalls"]), "state stall")))
            video_issues = self.video_issues(ep)
            if video_issues:
                kinds.append("video")
                flags += [("", text) for _, text in video_issues]
            repeat = conditions[record["start_condition"]]
            if repeat >= 3:
                kinds.append("repeat")
            gripper = record["grippers"].get(self.gripper) if self.gripper else None
            if gripper and (gripper["cycles"] > 1 or gripper["close"] is None):
                kinds.append("gripper")
                flags.append(
                    ("", "gripper never closes" if gripper["close"] is None else f"{gripper['cycles']} grasps")
                )
            self.info[ep] = {
                "kinds": kinds,
                "flags": flags,
                "repeat": repeat,
                "clip": bool(video and video["clip"]),
                "gripper": gripper,
            }

    def video_issues(self, ep: int) -> list[tuple[str, str]]:
        """Return (kind, text) pairs; kind is missing, mismatch, repeat, or black."""
        record, video = self.by_ep[ep], self.video.get(ep)
        issues = []
        for camera in self.cameras:
            window = record["videos"].get(camera)
            label = short_camera(camera)
            if not window or not window["exists"]:
                issues.append(("missing", f"{label} video missing"))
                continue
            stats = video["cameras"].get(camera) if video else None
            if stats and stats.get("available"):
                if stats["shortfall"]:
                    issues.append(("mismatch", f"{label} {stats['shortfall']} frames short"))
                elif stats["extra"]:
                    issues.append(("mismatch", f"{label} +{stats['extra']} frames"))
                if stats["repeated_moving"]:
                    issues.append(("repeat", f"{label} repeats while moving"))
                if stats["black"]:
                    issues.append(("black", f"{label} black frames"))
            elif window["window_frames"] != record["frames"]:
                issues.append(("mismatch", f"{label} window {window['window_frames']} ≠ {record['frames']} rows"))
        return issues

    def issue_episodes(self, *kinds: str) -> list[int]:
        return [ep for ep in self.eps if any(kind in kinds for kind, _ in self.video_issues(ep))]

    def numbers(self) -> dict[str, str]:
        """Scalar values available to profile text as $name."""
        d = self.metrics["dataset"]
        capture = self.metrics["capture"] or {}
        joints = joint_tracking(self.metrics["tracking"]) or {}
        grip = self.metrics["grippers"].get(self.gripper, {}) if self.gripper else {}
        repeats = self.metrics["starts"]["repeats"]
        values = {
            "dataset_name": d["name"],
            "robot_type": d["robot_type"] or "robot",
            "episodes": f"{self.n:,}",
            "frames": f"{d['frames']:,}",
            "minutes": f"{d['duration_min']:.1f}",
            "fps": f"{self.fps:g}",
            "cameras": str(len(d["cameras"])),
            "tasks": str(len(d["tasks"])),
            "files": f"{d['files']:,}",
            "duration_median": f"{self.metrics['motion']['duration_median_s']:.1f}",
            "capture_gaps": str(capture.get("gaps", 0)),
            "missing_frames": str(capture.get("missing_frames", 0)),
            "gap_episodes": str(len(capture.get("gap_episodes", []))),
            "lag_frames": str(joints.get("lag_mode", DASH)),
            "lag_ms": f"{joints['lag_mode'] / self.fps * 1000:.0f}" if joints else DASH,
            "grip_close_s": fmt(grip.get("close", {}).get("median_s")),
            "grip_open_s": fmt(grip.get("open", {}).get("median_s")),
            "start_conditions": str(self.metrics["starts"]["conditions"]),
            "top_repeat": str(repeats[0]["size"]) if repeats else "0",
            "flagged": str(sum(1 for ep in self.eps if self.info[ep]["kinds"])),
        }
        return {key: esc(value) for key, value in values.items()}

    def profile_html(self, key: str, default: str, numbers: dict[str, str]) -> str:
        value = self.profile.get(key)
        return Template(value).safe_substitute(numbers) if isinstance(value, str) else default

    # ------------------------------------------------------------------ hero and cards
    def headline(self) -> str:
        tasks = list(self.metrics["dataset"]["tasks"])
        if len(tasks) == 1:
            return f"{self.n:,} episodes of <em>{esc(tasks[0])}</em>"
        if tasks:
            return f"{self.n:,} episodes across <em>{len(tasks)} tasks</em>"
        return f"{self.n:,} recorded <em>episodes</em>"

    def chips(self) -> str:
        d = self.metrics["dataset"]
        size = self.media["clip"]["sizes"][self.primary]
        state = len(d["state"]["names"])
        action = len(d["action"]["names"])
        items = [
            f"{self.n:,} episodes · {d['frames']:,} frames",
            plural(len(d["cameras"]), "camera") + f" · clips {size[0]} {TIMES} {size[1]}",
            f"{state}-D state" + (f" · {action}-D action" if action else ""),
            f"{self.fps:g} fps · {d['duration_min']:.1f} min",
            "Automated screens · confirm findings by eye",
        ]
        return "".join(f"<span>{esc(item)}</span>" for item in items)

    def mosaic_figure(self) -> str:
        mosaic = self.media.get("mosaic")
        if not mosaic:
            return ""
        data = base64.b64encode((self.media_dir / mosaic["path"]).read_bytes()).decode()
        camera = esc(short_camera(self.primary))
        return (
            f'<figure class="mosaic"><img src="data:image/jpeg;base64,{data}" alt="Grid of the last {camera} frame of '
            f'{len(mosaic["episodes"])} episodes in recording order"><figcaption>The last {camera} frame of every '
            "episode with video, in recording order.</figcaption></figure>"
        )

    def metric_cards(self) -> str:
        d = self.metrics["dataset"]
        integ = self.metrics["integrity"]
        capture = self.metrics["capture"]
        cards = [("", f"{self.n:,}", "Episodes", f"{d['frames']:,} frames · {d['duration_min']:.1f} min")]
        cards.append(("good", f"{d['files']:,}", "Files hashed", f"{size_text(d['bytes'])} · SHA-256 per file"))
        if capture:
            tone = "warn" if capture["gaps"] else "good"
            note = f"{capture['missing_frames']} missing frames in {len(capture['gap_episodes'])} episodes"
            cards.append((tone, str(capture["gaps"]), "Capture gaps", note))
        else:
            cards.append(
                ("", f"{integ['export_on_grid']}/{self.n}", "Export clock on grid", "no capture clock to check")
            )
        with_video = sum(1 for ep in self.eps if self.info[ep]["clip"])
        missing = sum(len(c["missing"]) for c in d["cameras"])
        tone = "warn" if missing or with_video < self.n else "good"
        cards.append((tone, f"{with_video}/{self.n}", "Episodes with video", f"{missing} missing camera streams"))
        grip = self.metrics["grippers"].get(self.gripper) if self.gripper else None
        if grip and grip["close"]["count"]:
            close = grip["close"]
            note = f"5th{DASH}95th {close['p5_s']:.2f}{DASH}{close['p95_s']:.2f} s · "
            note += plural(len(grip["multi_cycle"]), "regrasp episode")
            cards.append(("", f"{close['median_s']:.2f} s", "Gripper closes (median)", note))
        else:
            cards.append(("", DASH, "Gripper events", "no gripper channel identified"))
        starts = self.metrics["starts"]
        top = starts["repeats"][0]["size"] if starts["repeats"] else 1
        tone = "warn" if self.n >= 5 and top >= REPEAT_SHARE * self.n else ""
        cards.append((tone, str(starts["conditions"]), "Distinct start poses", f"largest group {top} episodes"))
        joints = joint_tracking(self.metrics["tracking"])
        if joints:
            note = f"{joints['lag_mode'] / self.fps * 1000:.0f} ms · error {joints['rmse0_median']:.3g} → "
            note += f"{joints['rmse_lag_median']:.3g}"
            cards.append(("", plural(joints["lag_mode"], "frame"), "Command to state lag", note))
        else:
            cards.append(("", DASH, "Command tracking", "state and action are not paired"))
        flagged = sum(1 for ep in self.eps if self.info[ep]["kinds"] and self.info[ep]["kinds"] != ["repeat"])
        cards.append(("warn" if flagged else "good", str(flagged), "Episodes flagged", "outliers, gaps, stalls, video"))
        return "".join(
            f'<div class="metric {tone}"><strong>{esc(value)}</strong><span>{esc(label)}</span>'
            f"<small>{esc(note)}</small></div>"
            for tone, value, label, note in cards
        )

    # ------------------------------------------------------------------ readiness
    def readiness(self) -> tuple[list[str], list[str]]:
        d = self.metrics["dataset"]
        integ = self.metrics["integrity"]
        capture = self.metrics["capture"]
        holds, watch_items = [], []
        if integ["frame_index_contiguous"] == self.n and integ["export_on_grid"] == self.n:
            holds.append(
                f"Frame indices are contiguous and export timestamps sit on the {self.fps:g} fps grid "
                f"in all {self.n:,} episodes."
            )
        else:
            broken = self.n - min(integ["frame_index_contiguous"], integ["export_on_grid"])
            watch_items.append(f"Frame indices or export timestamps break in {broken} episodes.")
        if integ["length_matches_meta"] == self.n:
            holds.append("Every episode's row count matches meta/episodes.")
        else:
            differ = self.n - integ["length_matches_meta"]
            watch_items.append(f"{differ} episodes have a row count that differs from meta/episodes.")
        if not integ["nonfinite"]:
            holds.append("No NaN or infinite values in any numeric feature.")
        else:
            watch_items.append(f"Non-finite values in {len(integ['nonfinite'])} episodes.")
        if capture:
            if capture["gaps"]:
                watch_items.append(
                    f"The capture clock skips {capture['missing_frames']} frames in {capture['gaps']} gaps across "
                    f"{len(capture['gap_episodes'])} episodes (largest {capture['max_gap_ms']:.0f} ms)."
                )
            else:
                holds.append(
                    f"The capture clock shows no dropped frames (period {fmt(capture['period_ms'])} ms, "
                    f"jitter {fmt(capture['jitter_ms'])} ms)."
                )
        else:
            watch_items.append("There is no capture clock, so dropped frames cannot be measured.")
        missing = [(short_camera(c["key"]), len(c["missing"])) for c in d["cameras"] if c["missing"]]
        if missing:
            listed = "; ".join(f"{name} in {count} episodes" for name, count in missing)
            watch_items.append(f"Video is missing for {listed}.")
        mismatch = self.issue_episodes("mismatch")
        if mismatch:
            watch_items.append(f"Video and data rows disagree in {len(mismatch)} episodes.")
        elif self.video:
            holds.append("Every clip has exactly one video frame per data row.")
        repeat_moving = self.issue_episodes("repeat")
        black = self.issue_episodes("black")
        if repeat_moving or black:
            watch_items.append(
                f"Repeated frames while moving in {len(repeat_moving)} episodes and black frames in {len(black)}."
            )
        elif self.video:
            holds.append("No repeated frames during motion and no black frames in the decoded video.")
        if integ["stall_episodes"]:
            stalled = len(integ["stall_episodes"])
            watch_items.append(f"The state stops updating while commands change in {stalled} episodes.")
        ratio = integ["state_update_median"]
        if ratio is not None and ratio < UPDATE_RATIO_LOW:
            watch_items.append(
                f"The state changes in only {ratio * 100:.0f} % of frames (median): it may update more slowly than "
                "the frame rate."
            )
        constant = integ["constant"]["everywhere"]
        if constant:
            shown = ", ".join(constant[:4]) + (f" and {len(constant) - 4} more" if len(constant) > 4 else "")
            watch_items.append(f"{plural(len(constant), 'channel')} never change: {shown}.")
        starts = self.metrics["starts"]
        if starts["repeats"] and self.n >= 5 and starts["repeats"][0]["size"] >= REPEAT_SHARE * self.n:
            watch_items.append(f"{starts['repeats'][0]['size']} of {self.n} episodes start from the same pose.")
        elif self.n >= 5:
            holds.append(f"Start poses vary: {starts['conditions']} distinct start conditions.")
        joints = joint_tracking(self.metrics["tracking"])
        if joints and joints["episodes"] and max(joints["lags"].values()) == joints["episodes"]:
            if joints["lag_mode"]:
                lag = plural(joints["lag_mode"], "frame")
                holds.append(f"Commands lead the measured joints by a consistent {lag}.")
            else:
                holds.append("The measured joints follow the commands within the same frame in every episode.")
        outliers = self.metrics["outliers"]
        if outliers:
            watch_items.append(
                f"{plural(len(outliers), 'episode')} stand out on duration, path, speed, idle time, or tracking."
            )
        return holds, watch_items

    # ------------------------------------------------------------------ overview heatmap
    def overview(self) -> tuple[str, str, str]:
        durations = [self.by_ep[ep]["frames"] for ep in self.eps]
        tmax = max(durations) / self.fps
        cols = min(MAX_HEAT_COLUMNS, max(durations))
        bins = np.linspace(0, max(durations), cols + 1)
        speeds = np.zeros((self.n, cols))
        valid = np.zeros((self.n, cols), dtype=bool)
        for row, ep in enumerate(self.eps):
            speed = np.asarray(self.series[ep]["speed"], dtype=np.float64)
            index = np.clip(np.searchsorted(bins, np.arange(len(speed)), side="right") - 1, 0, cols - 1)
            np.maximum.at(speeds[row], index, speed)
            valid[row, : int(np.ceil(len(speed) / max(durations) * cols))] = True
        cap = self.metrics["motion"]["speed_p99"] or float(speeds.max()) or 1.0
        rgb = ramp(np.sqrt(np.clip(speeds / cap, 0, 1)))
        rgb[~valid] = 255
        image = png_uri(rgb)
        rh = float(np.clip(560 / self.n, 2.2, 16))
        x0, x1, y0 = 70.0, 930.0, 34.0
        height = y0 + self.n * rh + 36
        sx = (x1 - x0) / tmax
        parts = [
            f'<svg id="overview-svg" viewBox="0 0 1000 {height:.0f}" role="group" '
            f'aria-roledescription="interactive chart" aria-label="All {self.n} episodes over time, colored by '
            f'joint speed" aria-describedby="overview-help" tabindex="0" data-x0="{x0}" data-x1="{x1}" '
            f'data-y0="{y0}" data-rh="{rh:.3f}" data-max="{tmax:.4f}">'
        ]
        parts += time_axis(x0, sx, tmax, y0 - 4, y0 + self.n * rh, y0 - 10)
        parts.append(f'<text class="axis-title" x="{x0}" y="12">Seconds from episode start</text>')
        parts.append(
            f'<image href="{image}" x="{x0}" y="{y0}" width="{x1 - x0}" height="{self.n * rh:.1f}" '
            'preserveAspectRatio="none" class="heat"/>'
        )
        label_every = max(1, int(np.ceil(14 / rh)))
        for row, ep in enumerate(self.eps):
            y = y0 + row * rh
            record, info = self.by_ep[ep], self.info[ep]
            if row % label_every == 0:
                parts.append(f'<text x="{x0 - 8}" y="{y + min(rh, 12) - 1:.1f}" text-anchor="end">{ep}</text>')
            gripper = info["gripper"]
            if gripper:
                for key, cls in (("close", "ev-close"), ("open", "ev-open")):
                    if gripper[key] is not None:
                        x = x0 + gripper[key] / self.fps * sx
                        parts.append(
                            f'<rect class="{cls}" x="{x - 0.8:.1f}" y="{y:.1f}" width="1.6" height="{rh:.2f}"/>'
                        )
            if record["capture"]:
                for frame, _, _ in record["capture"]["gaps"]:
                    x = x0 + frame / self.fps * sx
                    parts.append(f'<rect class="ev-gap" x="{x - 1:.1f}" y="{y:.1f}" width="2" height="{rh:.2f}"/>')
            for a, b in record["stalls"]:
                x = x0 + a / self.fps * sx
                width = max(2.0, (b - a + 1) / self.fps * sx)
                parts.append(f'<rect class="ev-stall" x="{x:.1f}" y="{y:.1f}" width="{width:.1f}" height="{rh:.2f}"/>')
            if info["flags"]:
                tone = " high" if any(t == "high" for t, _ in info["flags"]) else ""
                radius = min(rh / 2, 4.5)
                cy = y + rh / 2
                parts.append(f'<circle class="flag-dot{tone}" cx="{x1 + 14}" cy="{cy:.1f}" r="{radius:.1f}"/>')
        parts.append(f'<text class="axis-title" x="{x1 + 14}" y="{y0 - 10}" text-anchor="middle">Flag</text>')
        parts.append('<line class="playhead" visibility="hidden" x1="0" x2="0" y1="0" y2="0"/>')
        parts.append(f'<rect class="rowmark" visibility="hidden" x="{x0}" y="0" width="{x1 - x0}" height="{rh:.2f}"/>')
        parts.append("</svg>")
        legend = ['<span>Joint speed<i class="sw ramp"></i></span>']
        if self.gripper:
            legend.append('<span><i class="sw bar" style="background:#b45309"></i>Gripper closes</span>')
            legend.append('<span><i class="sw bar" style="background:#0d1a31"></i>Gripper opens</span>')
        if self.metrics["capture"]:
            legend.append('<span><i class="sw bar" style="background:#e11d48"></i>Capture gap</span>')
        legend.append('<span><i class="sw bar" style="background:#7c3aed"></i>State stall</span>')
        legend.append('<span><i class="dot" style="background:#d97706"></i>Flagged episode</span>')
        motion = ", ".join(self.metrics["dataset"]["motion_channels"][:6])
        caption = (
            f"Speed is the norm of the frame-to-frame change of {esc(motion)}"
            + (" and more" if len(self.metrics["dataset"]["motion_channels"]) > 6 else "")
            + " in state units per second, smoothed over five frames and square-root scaled to the dataset's 99th "
            "percentile. Blank cells are after an episode ends."
        )
        return "".join(parts), "".join(legend), caption

    # ------------------------------------------------------------------ starts and events
    def starts_chart(self) -> tuple[str, str]:
        points = [(ep, *self.by_ep[ep]["start_pc"]) for ep in self.eps]
        xs, ys = np.array([p[1] for p in points]), np.array([p[2] for p in points])
        pad = 0.08

        def span(values: np.ndarray) -> tuple[float, float]:
            lo, hi = float(values.min()), float(values.max())
            if hi - lo < 1e-9:
                return lo - 1, hi + 1
            return lo - pad * (hi - lo), hi + pad * (hi - lo)

        (xl, xh), (yl, yh) = span(xs), span(ys)
        width, ml, mr, mt, mb = 500.0, 56.0, 16.0, 16.0, 44.0
        pw, ph = width - ml - mr, 300.0
        height = mt + ph + mb

        def px(v: float) -> float:
            return ml + (v - xl) / (xh - xl) * pw

        def py(v: float) -> float:
            return mt + (1 - (v - yl) / (yh - yl)) * ph

        variance = self.metrics["starts"]["pc_variance"]
        parts = [
            f'<svg viewBox="0 0 {width:.0f} {height:.0f}" role="group" aria-roledescription="interactive chart" '
            'aria-label="Episode start poses on two principal components" aria-describedby="starts-chart-help" '
            'tabindex="0">'
        ]
        for k in range(5):
            gx, gy = ml + k * pw / 4, mt + k * ph / 4
            parts.append(f'<line class="grid" x1="{gx:.1f}" x2="{gx:.1f}" y1="{mt}" y2="{mt + ph}"/>')
            parts.append(f'<line class="grid" x1="{ml}" x2="{ml + pw}" y1="{gy:.1f}" y2="{gy:.1f}"/>')
        parts.append(
            f'<text class="axis-title" x="{ml + pw / 2:.1f}" y="{height - 10:.1f}" text-anchor="middle">'
            f"Start-pose component 1 ({variance[0] * 100:.0f} % of variance)</text>"
        )
        parts.append(
            f'<text class="axis-title" transform="translate(16 {mt + ph / 2:.1f}) rotate(-90)" '
            f'text-anchor="middle">Component 2 ({variance[1] * 100:.0f} %)</text>'
        )
        for ep, x, y in points:
            info = self.info[ep]
            cx, cy = px(x), py(y)
            others = info["repeat"] - 1
            caption = f"start condition S{self.by_ep[ep]['start_condition']}"
            if others:
                caption += f" · shared with {plural(others, 'other episode')}"
            cls = "mk pt repeat" if info["repeat"] >= 3 else "mk pt"
            parts.append(
                f'<circle class="{cls}" r="4" cx="{cx:.1f}" cy="{cy:.1f}" data-ep="{ep}" data-role="start" '
                f'data-t="0" data-x="{cx:.1f}" data-y="{cy:.1f}" data-cap="{esc(caption)}"/>'
            )
        parts.append("</svg>")
        caption = (
            "Each state dimension is scaled by its range across the dataset before projection, so joints with large "
            "travel do not dominate. Amber points share a start condition with at least two other episodes."
        )
        return "".join(parts), caption

    def repeat_rows(self) -> str:
        rows = []
        names = self.metrics["dataset"]["state"]["names"]
        for group in self.metrics["starts"]["repeats"][:6]:
            eps = group["episodes"]
            starts = np.array([self.by_ep[ep]["start"] for ep in eps])
            spread = np.ptp(starts, axis=0)
            k = int(np.argmax(spread))
            rows.append(
                f"<tr><td>{group['size']}</td><td>{esc(ranges(eps))}</td><td>{fmt(float(spread[k]), 4)} "
                f'<span class="small muted">{esc(names[k])}</span></td><td>{watch(eps[0], 0, str(eps[0]))}'
                f"{watch(eps[-1], 0, str(eps[-1]))}</td></tr>"
            )
        return (
            "".join(rows)
            or '<tr><td colspan="4" class="muted">No start condition repeats three or more times.</td></tr>'
        )

    def events_figure(self) -> str:
        if not self.gripper:
            return '<p class="note row-gap">No gripper channel was identified, so gripper timing is not shown.</p>'
        labels = list(self.metrics["grippers"])
        rows = [(label, key) for label in labels for key in ("close", "open")]
        tmax = max(self.by_ep[ep]["frames"] for ep in self.eps) / self.fps
        x0, x1, row_h, top = 230.0, 980.0, 34.0, 26.0
        height = top + len(rows) * row_h + 32
        sx = (x1 - x0) / tmax
        parts = [
            f'<svg viewBox="0 0 1000 {height:.0f}" role="group" aria-roledescription="interactive chart" '
            'aria-label="Gripper event times across episodes" aria-describedby="events-help" tabindex="0">'
        ]
        parts += time_axis(x0, sx, tmax, top - 6, top + len(rows) * row_h, top - 10)
        for k, (label, key) in enumerate(rows):
            yc = top + k * row_h + row_h / 2
            verb = "closes" if key == "close" else "opens"
            channel = self.metrics["grippers"][label]["channel"]
            parts.append(
                f'<text x="{x0 - 12}" y="{yc + 4:.1f}" text-anchor="end" class="row-label">{esc(label)} {verb} '
                f'<tspan class="muted-t">{esc(channel)}</tspan></text>'
            )
            for ep in self.eps:
                frame = self.by_ep[ep]["grippers"].get(label, {}).get(key)
                if frame is None:
                    continue
                t = frame / self.fps
                jitter = ((ep * 2654435761) % 1000) / 1000 - 0.5
                cx, cy = x0 + t * sx, yc + jitter * (row_h - 12)
                parts.append(
                    f'<circle class="mk ev {key}" r="3" cx="{cx:.1f}" cy="{cy:.1f}" data-ep="{ep}" '
                    f'data-role="{esc(label)} {verb}" data-t="{t:.4f}" data-x="{cx:.1f}" data-y="{cy:.1f}"/>'
                )
        parts.append(
            f'<text class="axis-title" x="{(x0 + x1) / 2:.0f}" y="{height - 6:.0f}" text-anchor="middle">'
            "Seconds from episode start</text></svg>"
        )
        threshold = self.metrics["thresholds"]["event_fraction"] * 100
        return (
            '<figure class="chart-card row-gap" id="events-chart"><div class="legend-row">'
            '<span><i class="dot" style="background:#b45309"></i>Closes</span>'
            '<span><i class="dot" style="background:#0d1a31"></i>Opens</span>'
            '<span class="hint" id="events-help">One point per episode. Hover a point to preview that moment; click '
            "to open the player. Keyboard: ← → step, Enter open.</span></div>"
            + "".join(parts)
            + '<div class="preview" hidden aria-hidden="true"><video muted playsinline preload="auto"></video>'
            '<p class="no-video" hidden>No video for this episode</p><div class="pmeta"><strong class="pv-title">'
            '</strong><span class="pv-phase"></span><span class="pv-time mono"></span></div><div class="mini">'
            '<i class="cursor"></i></div></div><p class="sr-only" aria-live="polite"></p>'
            f"<figcaption>A gripper closes when its channel moves more than {threshold:.0f} % of its range away from "
            "its value at the start of the episode for at least three frames, and opens when it returns. The start "
            "value is assumed to be open.</figcaption></figure>"
        )

    # ------------------------------------------------------------------ timing
    def capture_panel(self) -> str:
        capture = self.metrics["capture"]
        integ = self.metrics["integrity"]
        if not capture:
            return (
                '<div class="note">This dataset has no capture-clock column, so dropped frames cannot be measured. '
                f"The export timestamps are on the {self.fps:g} fps grid in {integ['export_on_grid']} of {self.n} "
                "episodes, which they are by construction.</div>"
            )
        period = f"{fmt(capture['period_ms'], 3)} ms measured · {1000 / self.fps:.3f} ms nominal"
        gap_count = f"{capture['gaps']} in {len(capture['gap_episodes'])} episodes"
        rows = [
            ("Clock", esc(capture["clock"]), ""),
            ("Period", period, f"jitter {fmt(capture['jitter_ms'], 3)} ms"),
            ("Gaps", gap_count, f"largest {fmt(capture['max_gap_ms'], 1)} ms"),
            ("Missing frames", f"{capture['missing_frames']} ({capture['missing_pct']:.2f} %)", ""),
            ("Bursts", esc(ranges(capture["bursts"])) or "none", "episodes with three or more gaps"),
            ("Catch-up intervals", str(capture["catch_up"]), "shorter than half a period after a delay"),
            ("Non-increasing steps", str(capture["non_increasing"]), "the clock repeats or runs backwards"),
        ]
        table = "".join(f'<tr><th scope="row">{a}</th><td>{b}</td><td class="small">{c}</td></tr>' for a, b, c in rows)
        gaps = sorted(
            ((ep, g) for ep in self.eps if self.by_ep[ep]["capture"] for g in self.by_ep[ep]["capture"]["gaps"]),
            key=lambda item: -item[1][1],
        )[:12]
        gap_rows = "".join(
            f"<tr><td>{ep}</td><td>{frame / self.fps:.2f} s</td><td>{dt:.1f} ms</td><td>{missing}</td>"
            f"<td>{watch(ep, frame / self.fps - 0.5, 'Watch')}</td></tr>"
            for ep, (frame, dt, missing) in gaps
        )
        largest = (
            '<h3 class="row-gap">Largest gaps</h3><div class="table-wrap"><table><thead><tr>'
            '<th scope="col">Episode</th><th scope="col">At</th><th scope="col">Interval</th>'
            '<th scope="col">Missing frames</th><th scope="col">Watch</th></tr></thead>'
            f"<tbody>{gap_rows}</tbody></table></div>"
            if gaps
            else ""
        )
        return (
            f'<div class="panel"><h3>Capture clock</h3><div class="table-wrap"><table><tbody>{table}</tbody></table>'
            f"</div>{largest}</div>"
        )

    def tracking_rows(self) -> str:
        rows = []
        for label, t in self.metrics["tracking"].items():
            share = t["lags"].get(str(t["lag_mode"]), 0)
            rows.append(
                f'<tr><th scope="row">{esc(label)}</th><td>{plural(t["lag_mode"], "frame")} · '
                f"{t['lag_mode'] / self.fps * 1000:.0f} ms</td><td>{share} of {t['episodes']}</td>"
                f"<td>{t['rmse0_median']:.4g}</td><td>{t['rmse_lag_median']:.4g}</td></tr>"
            )
        return "".join(rows) or '<tr><td colspan="5" class="muted">State and action are not paired.</td></tr>'

    def camera_lags(self, camera: str) -> list[tuple[int, float]]:
        out = []
        for ep in self.eps:
            stats = (self.video.get(ep) or {}).get("cameras", {}).get(camera) or {}
            if stats.get("available") and stats.get("lag_vs_state") is not None:
                out.append((ep, float(stats["lag_vs_state"])))
        return out

    def camera_chart(self) -> str:
        cameras = [c for c in self.cameras if self.camera_lags(c)]
        if not cameras:
            return '<p class="small muted">No camera offset could be estimated: too little motion or no video.</p>'
        x0, x1, top, row_h = 190.0, 980.0, 20.0, 38.0
        lo, hi = -8.0, 8.0
        height = top + len(cameras) * row_h + 36

        def px(v: float) -> float:
            return x0 + (min(max(v, lo), hi) - lo) / (hi - lo) * (x1 - x0)

        bottom = top + len(cameras) * row_h
        parts = [
            f'<svg viewBox="0 0 1000 {height:.0f}" role="img" '
            'aria-label="Camera motion offset against joint motion, per camera">'
        ]
        for v in range(int(lo), int(hi) + 1, 2):
            zero = " zero" if v == 0 else ""
            x = px(v)
            parts.append(f'<line class="grid{zero}" x1="{x:.1f}" x2="{x:.1f}" y1="{top - 6}" y2="{bottom}"/>')
            parts.append(f'<text x="{x:.1f}" y="{bottom + 16}" text-anchor="middle">{signed(v, 0)}</text>')
        for k, camera in enumerate(cameras):
            yc = top + k * row_h + row_h / 2
            lags = self.camera_lags(camera)
            median = float(np.median([lag for _, lag in lags]))
            parts.append(
                f'<text x="{x0 - 12}" y="{yc + 4:.1f}" text-anchor="end" class="row-label">'
                f'{esc(short_camera(camera))} <tspan class="muted-t">{signed(median)}</tspan></text>'
            )
            for ep, lag in lags:
                jitter = ((ep * 2654435761 + k * 97) % 1000) / 1000 - 0.5
                parts.append(
                    f'<circle class="cam-dot" r="2.2" cx="{px(lag):.1f}" cy="{yc + jitter * (row_h - 12):.1f}"/>'
                )
            x = px(median)
            y1, y2 = yc - row_h / 2 + 4, yc + row_h / 2 - 4
            parts.append(f'<line class="median" x1="{x:.1f}" x2="{x:.1f}" y1="{y1:.1f}" y2="{y2:.1f}"/>')
        parts.append(
            f'<text class="axis-title" x="{(x0 + x1) / 2:.0f}" y="{height - 4:.0f}" text-anchor="middle">'
            "Frames the camera trails the joint state (positive: video later)</text></svg>"
        )
        return "".join(parts)

    def camera_rows(self) -> str:
        rows = []
        for camera in self.metrics["dataset"]["cameras"]:
            key = camera["key"]
            stats = [(self.video.get(ep) or {}).get("cameras", {}).get(key) or {} for ep in self.eps]
            available = [s for s in stats if s.get("available")]
            mismatch = sum(1 for s in available if s["shortfall"] or s["extra"])
            mismatch = mismatch or len(camera["window_mismatch"])
            moving = sum(1 for s in available if s["repeated_moving"])
            black = sum(1 for s in available if s["black"])
            luma = [s["luma_mean"] for s in available if s.get("luma_mean") is not None]
            brightness = f"{min(luma):.0f}{DASH}{max(luma):.0f}" if luma else DASH
            used = "" if key in self.cameras else ' <span class="small muted">not in clips</span>'
            rows.append(
                f'<tr><th scope="row">{esc(short_camera(key))}{used}</th><td>{camera["episodes_with_video"]} of '
                f"{self.n}</td><td>{mismatch}</td><td>{moving}</td><td>{black}</td><td>{brightness}</td></tr>"
            )
        return "".join(rows)

    def integrity_rows(self) -> str:
        integ = self.metrics["integrity"]
        constant = integ["constant"]
        decoded = sum(
            s.get("decoded", 0) for v in self.video.values() for s in v["cameras"].values() if s.get("available")
        )
        shortfall = sum(
            s.get("shortfall", 0) for v in self.video.values() for s in v["cameras"].values() if s.get("available")
        )
        ratio = integ["state_update_median"]
        good = integ["frame_index_contiguous"] == self.n and integ["export_on_grid"] == self.n
        clocks = f"{integ['frame_index_contiguous']}/{self.n} contiguous · {integ['export_on_grid']}/{self.n} on grid"
        updates = DASH if ratio is None else f"{ratio * 100:.1f} % of frames (median)"
        fixed = (
            f"{len(constant['everywhere'])} never change · {len(constant['within_episodes'])} fixed within each episode"
        )
        fixed_names = esc(", ".join((constant["everywhere"] + constant["within_episodes"])[:8]))
        rows = [
            (
                "Frame index and timestamps",
                clocks,
                f"largest export-clock error {integ['export_error_max_ms']:.3f} ms",
                "good" if good else "warn",
            ),
            ("Rows against meta/episodes", f"{integ['length_matches_meta']}/{self.n} match", "", ""),
            (
                "First state row",
                f"repeated in {len(integ['first_row_repeated'])} episodes",
                "rows 0 and 1 identical: the state starts one tick late",
                "warn" if integ["first_row_repeated"] else "",
            ),
            (
                "State updates",
                updates,
                "share of frames where any state value changes",
                "warn" if ratio is not None and ratio < UPDATE_RATIO_LOW else "",
            ),
            (
                "State stalls",
                f"{len(integ['stall_episodes'])} episodes",
                "state frozen for half a second while commands change",
                "warn" if integ["stall_episodes"] else "",
            ),
            ("Non-finite values", f"{len(integ['nonfinite'])} episodes", "", "warn" if integ["nonfinite"] else ""),
            ("Constant channels", fixed, fixed_names, "warn" if constant["everywhere"] else ""),
            ("Video decode", f"{decoded:,} frames decoded · {shortfall} short", "every frame of every clip camera", ""),
        ]
        return "".join(
            f'<tr class="{tone}"><th scope="row">{esc(a)}</th><td>{esc(b)}</td><td class="small">{c}</td></tr>'
            for a, b, c, tone in rows
        )

    # ------------------------------------------------------------------ explorer
    def filter_buttons(self) -> str:
        labels = {
            "outlier": "Outlier",
            "gap": "Capture gap",
            "stall": "State stall",
            "video": "Video issue",
            "repeat": "Repeated start",
            "gripper": "Gripper",
        }
        present = {kind for ep in self.eps for kind in self.info[ep]["kinds"]}
        buttons = ['<button type="button" data-filter="all" aria-pressed="true">All</button>']
        buttons += [
            f'<button type="button" data-filter="{kind}" aria-pressed="false">{label}</button>'
            for kind, label in labels.items()
            if kind in present
        ]
        buttons.append('<button type="button" data-filter="clean" aria-pressed="false">None of these</button>')
        return "".join(buttons)

    def episode_rows(self) -> str:
        rows = []
        for ep in self.eps:
            record, info, video = self.by_ep[ep], self.info[ep], self.video.get(ep)
            motion = record["motion"]
            gripper = info["gripper"]
            close = gripper["close"] / self.fps if gripper and gripper["close"] is not None else float("nan")
            chips = "".join(f'<span class="chip {tone}">{esc(text)}</span>' for tone, text in info["flags"]) or (
                '<span class="ok">clean</span>'
            )
            end_t = (record["frames"] - 1) / self.fps
            thumb = DASH
            if video and video.get("thumbs"):
                thumb = (
                    f'<button type="button" class="thumb" data-watch="{ep}:{end_t:.3f}" aria-label="Episode {ep} last '
                    f'frame"><img loading="lazy" data-ep="{ep}" data-role="last" '
                    f'src="data:image/jpeg;base64,{video["thumbs"]["last"]}" alt="Episode {ep} last frame"></button>'
                )
            rows.append(
                f'<tr data-episode="{ep}" data-duration="{record["duration_s"]}" data-path="{motion["path"]}" '
                f'data-peak="{motion["peak_speed"]}" data-idle="{motion["idle_fraction"]}" data-close="{close:.3f}" '
                f'data-repeat="{info["repeat"]}" data-kind="{" ".join(info["kinds"]) or "clean"}">'
                f'<td class="ep"><button type="button" class="ep-play" data-watch="{ep}:0" aria-label="Play episode '
                f'{ep}">{ep}</button></td><td>{record["duration_s"]:.2f}</td><td>{fmt(motion["path"], 3)}</td>'
                f"<td>{fmt(motion['peak_speed'], 3)}</td><td>{motion['idle_fraction'] * 100:.0f}</td>"
                f'<td>{fmt(close)}</td><td>{info["repeat"]}</td><td class="flags">{chips}</td><td>{thumb}</td></tr>'
            )
        return "".join(rows)

    # ------------------------------------------------------------------ findings
    def findings(self) -> list[tuple[str, str, str]]:
        d = self.metrics["dataset"]
        integ = self.metrics["integrity"]
        capture = self.metrics["capture"]
        thresholds = self.metrics["thresholds"]
        cards: list[tuple[str, str, str]] = []

        def add(severity: str, title: str, *paragraphs: str) -> None:
            cards.append((severity, title, "".join(f"<p>{text}</p>" for text in paragraphs if text)))

        for camera in d["cameras"]:
            if camera["missing"]:
                share = len(camera["missing"]) / self.n
                add(
                    "high" if share >= 0.5 else "medium",
                    f"Video for {short_camera(camera['key'])} is missing in {len(camera['missing'])} episodes",
                    "meta/info.json declares the camera, but its video file is absent for episodes "
                    f"{esc(ranges(camera['missing']))}. Training that loads this camera will fail or silently skip "
                    "these episodes.",
                    "".join(watch(ep, 0) for ep in camera["missing"][:4]),
                )
        if capture and capture["gaps"]:
            gaps = [(ep, g) for ep in self.eps if self.by_ep[ep]["capture"] for g in self.by_ep[ep]["capture"]["gaps"]]
            ep, (frame, dt, missing) = max(gaps, key=lambda item: item[1][1])
            bursts = ""
            if capture["bursts"]:
                bursts = f" Bursts of three or more gaps: episodes {esc(ranges(capture['bursts']))}."
            moment = watch(ep, frame / self.fps - 0.5, f"episode {ep} at {frame / self.fps:.1f} s")
            add(
                "high" if capture["missing_pct"] >= 0.5 or capture["bursts"] else "medium",
                f"The capture clock skips {capture['missing_frames']} frames",
                f"{capture['gaps']} intervals exceed {thresholds['gap_factor']:g} frame periods across "
                f"{len(capture['gap_episodes'])} episodes ({capture['missing_pct']:.2f} % of frames).{bursts} "
                "The export timestamps stay on a perfect grid, so a pipeline that reads only "
                "<code>timestamp</code> will not see these drops.",
                f"The largest, {dt:.0f} ms ({plural(missing, 'frame')}), is in {moment}.",
            )
        if integ["stall_episodes"]:
            eps = integ["stall_episodes"]
            start = self.by_ep[eps[0]]["stalls"][0][0] / self.fps
            add(
                "high",
                f"The state freezes while commands change in {plural(len(eps), 'episode')}",
                "For at least half a second the measured state repeats exactly while the action keeps changing. "
                "That points to a sensor or driver that stopped updating rather than a robot at rest. "
                f"Episodes {esc(ranges(eps))}.",
                watch(eps[0], start, f"Episode {eps[0]} at {start:.1f} s"),
            )
        mismatch = [(ep, text) for ep in self.eps for kind, text in self.video_issues(ep) if kind == "mismatch"]
        if mismatch:
            episodes = sorted({ep for ep, _ in mismatch})
            add(
                "medium",
                f"Video and data rows disagree in {len(episodes)} episodes",
                f"{esc('; '.join(text for _, text in mismatch[:4]))}. Clips are padded with the last frame or "
                "trimmed to the data rows, so the player stays frame-accurate at the start but not necessarily at "
                "the end.",
                "".join(watch(ep, 0) for ep in episodes[:4]),
            )
        starts = self.metrics["starts"]
        if starts["repeats"] and self.n >= 5 and starts["repeats"][0]["size"] >= REPEAT_SHARE * self.n:
            group = starts["repeats"][0]
            add(
                "medium",
                f"{group['size']} of {self.n} episodes start from the same pose",
                f"Their first states agree within {starts['tolerance_fraction'] * 100:.0f} % of each joint's range "
                f"(episodes {esc(ranges(group['episodes']))}). A policy trained on them sees one start condition; "
                "split training and validation by start condition so near-identical episodes cannot fall on both "
                "sides.",
                watch(group["episodes"][0], 0) + watch(group["episodes"][-1], 0),
            )
        joints = joint_tracking(self.metrics["tracking"])
        if joints and joints["lag_mode"] >= LAG_NOTE_FRAMES:
            at_mode = joints["lags"].get(str(joints["lag_mode"]), 0)
            add(
                "medium",
                f"Commands lead the measured joints by {joints['lag_mode'] / self.fps * 1000:.0f} ms",
                f"The state follows the action {plural(joints['lag_mode'], 'frame')} later in {at_mode} of "
                f"{joints['episodes']} episodes; shifting by that amount cuts the median error from "
                f"{joints['rmse0_median']:.4g} to {joints['rmse_lag_median']:.4g} state units. Pair observations "
                "with actions at that offset, or predict action chunks that cover it.",
            )
        outliers = self.metrics["outliers"]
        if outliers:
            items = []
            for ep_text, flags in list(outliers.items())[:5]:
                text = ", ".join(f"{f['metric']} {f['value']:.4g} (z {f['z']:+.1f})" for f in flags)
                items.append(f"<li>{watch(int(ep_text), 0)} {esc(text)}</li>")
            cards.append(
                (
                    "medium",
                    f"{plural(len(outliers), 'episode')} stand out from the rest",
                    f"<p>Robust z-score above {thresholds['robust_z_limit']:g} and at least "
                    f"{thresholds['outlier_min_effect'] * 100:.0f} % from the median on one metric:</p>"
                    f'<ul class="small">{"".join(items)}</ul>',
                )
            )
        if self.gripper:
            grip = self.metrics["grippers"][self.gripper]
            if grip["never_closed"]:
                add(
                    "medium",
                    f"The gripper never closes in {plural(len(grip['never_closed']), 'episode')}",
                    f"Episodes {esc(ranges(grip['never_closed']))}: no grasp is visible in {esc(grip['channel'])}. "
                    "Check whether these are failed attempts, and label them.",
                    "".join(watch(ep, 0) for ep in grip["never_closed"][:4]),
                )
            if grip["multi_cycle"]:
                add(
                    "low",
                    f"Regrasps in {plural(len(grip['multi_cycle']), 'episode')}",
                    f"The gripper closes more than once in episodes {esc(ranges(grip['multi_cycle']))}.",
                )
        constant = integ["constant"]["everywhere"]
        if constant:
            more = " and more" if len(constant) > 12 else ""
            add(
                "low",
                f"{plural(len(constant), 'channel')} never change",
                f"{esc(', '.join(constant[:12]))}{more} hold one value in every row of every episode. They are "
                "placeholders or fixed configuration, not measurements.",
            )
        ratio = integ["state_update_median"]
        slow = ratio is not None and ratio < UPDATE_RATIO_LOW
        if integ["first_row_repeated"] or slow:
            parts = []
            if integ["first_row_repeated"]:
                parts.append(f"rows 0 and 1 of the state are identical in {len(integ['first_row_repeated'])} episodes")
            if slow:
                parts.append(f"the state changes in only {ratio * 100:.0f} % of frames")
            add(
                "low",
                "The state stream updates late or slowly",
                f"{esc('; '.join(parts)).capitalize()}. Check the state publisher's rate against the recording rate.",
            )
        motion = self.metrics["motion"]
        idle_start = np.median([r["motion"]["idle_start_s"] or 0 for r in self.records])
        idle_end = np.median([r["motion"]["idle_end_s"] or 0 for r in self.records])
        if idle_start > IDLE_NOTE_S or idle_end > IDLE_NOTE_S:
            add(
                "low",
                "Episodes start and end with idle time",
                f"The median episode waits {idle_start:.1f} s before the joints move and {idle_end:.1f} s after "
                f"they stop, and is idle for {motion['idle_fraction_median'] * 100:.0f} % of its frames. Trim idle "
                "heads and tails, or keep them deliberately for start and stop behavior.",
            )
        frozen = self.issue_episodes("repeat", "black")
        if frozen:
            add(
                "low",
                f"Frozen or black video frames in {plural(len(frozen), 'episode')}",
                "Consecutive frames are identical while the joints move, or frames are almost black. "
                + "".join(watch(ep, 0) for ep in frozen[:4]),
            )
        return cards

    def anomaly_cards(self) -> str:
        numbers = self.numbers()
        cards = [
            (
                str(item.get("severity", "medium")),
                esc(item.get("title", "")),
                Template(str(item.get("body", ""))).safe_substitute(numbers),
            )
            for item in self.profile.get("findings", [])
        ]
        cards += [(sev, esc(title), body) for sev, title, body in self.findings()]
        order = {"high": 0, "medium": 1, "low": 2}
        cards.sort(key=lambda card: order.get(card[0], 3))
        if not cards:
            cards = [("low", "No issues found by the automated checks", "<p>Confirm by watching a few episodes.</p>")]
        return "".join(
            f'<article class="finding {esc(sev)}"><div class="finding-head">'
            f'<span class="sev {esc(sev)}">{esc(sev)}</span><h4>{title}</h4></div>{body}</article>'
            for sev, title, body in cards
        )

    def improvements(self) -> str:
        d = self.metrics["dataset"]
        integ = self.metrics["integrity"]
        capture = self.metrics["capture"]
        items = []

        def add(title: str, text: str) -> None:
            items.append(f"<strong>{title}</strong> {text}")

        if any(c["missing"] for c in d["cameras"]):
            add(
                "Restore the missing video.",
                "Re-export the absent camera files, or remove the camera from meta/info.json, before releasing the "
                "dataset.",
            )
        if capture and capture["gaps"]:
            add(
                "Find why the recorder drops frames.",
                "Check the recording host's load and disk throughput at the gap times, and keep the capture clock "
                "so consumers can mask the gaps.",
            )
        if not capture:
            add(
                "Record a capture clock.",
                "Store the time each row was captured so dropped frames and stream offsets can be measured instead "
                "of estimated.",
            )
        if integ["stall_episodes"]:
            add(
                "Check the state publisher.",
                "Drop or repair windows where the state stops updating while commands change.",
            )
        if self.issue_episodes("mismatch"):
            add(
                "Make video and data agree.",
                "Each episode should have exactly one video frame per data row in every camera.",
            )
        joints = joint_tracking(self.metrics["tracking"])
        if joints and joints["lag_mode"] >= LAG_NOTE_FRAMES:
            add(
                f"Account for the {joints['lag_mode'] / self.fps * 1000:.0f} ms command delay.",
                "Pair observations with actions at the measured offset, or predict action chunks.",
            )
        starts = self.metrics["starts"]
        if starts["repeats"] and self.n >= 5 and starts["repeats"][0]["size"] >= REPEAT_SHARE * self.n:
            add(
                "Vary the start pose.",
                "Record from more start conditions, and split training and validation by start condition.",
            )
        if integ["constant"]["everywhere"]:
            add(
                "Drop or document placeholder channels.",
                "Constant channels add no information and can mislead normalization statistics.",
            )
        if self.metrics["outliers"]:
            add("Review the flagged episodes.", "Watch each one and keep, relabel, or exclude it before release.")
        numbers = self.numbers()
        items += [Template(str(item)).safe_substitute(numbers) for item in self.profile.get("recommendations", [])]
        if not items:
            items.append("No changes are needed based on the automated checks.")
        return "".join(f"<li>{item}</li>" for item in items)

    # ------------------------------------------------------------------ method and lineage
    def method_rows(self) -> str:
        d = self.metrics["dataset"]
        clip = self.media["clip"]
        thresholds = self.metrics["thresholds"]
        grippers = ", ".join(f"{label}: {g['channel']}" for label, g in self.metrics["grippers"].items()) or "none"
        clock = self.metrics["capture"]["clock"] if self.metrics["capture"] else "none"
        inputs = f"{d['files']:,} files · {size_text(d['bytes'])} · digest {self.manifest['inputs_digest'][:16]}"
        action = f"{d['action']['key'] or 'none'}" + (" · paired with the state" if d["action"]["paired"] else "")
        cameras = ", ".join(short_camera(c) for c in clip["cameras"])
        canvas = f"{clip['canvas'][0]} {TIMES} {clip['canvas'][1]}"
        clips = f"{cameras} · {canvas} · {clip['codec']} CRF {clip['crf']} · {size_text(self.media['clips_bytes'])}"
        screens = (
            f"gap > {thresholds['gap_factor']:g} periods · idle < {thresholds['idle_speed_fraction'] * 100:.0f} % of "
            f"the 95th-percentile speed · stall ≥ {thresholds['stall_min_s']:g} s · outlier |z| > "
            f"{thresholds['robust_z_limit']:g}"
        )
        rows = [
            ("Dataset", d["name"]),
            ("Format", f"LeRobot {d['codebase_version']} · {d['robot_type'] or 'robot type not recorded'}"),
            ("Inputs", inputs),
            ("State", f"{d['state']['key']} · {len(d['state']['names'])} dimensions"),
            ("Action", action),
            ("Grippers", grippers),
            ("Capture clock", clock),
            ("Clips", clips),
            ("Thresholds", screens),
            ("Bundle", f"generated {self.manifest['generated']}"),
        ]
        rows += [("Layout note", note) for note in self.metrics["layout_notes"]]
        return "".join(f"<dt>{esc(a)}</dt><dd>{esc(b)}</dd>" for a, b in rows)

    def limits(self) -> str:
        items = [
            "State and action values are used as recorded. Units are not known, so speeds and paths are in "
            "state units.",
            "Gripper events assume the gripper is open at the start of each episode.",
            "Camera offsets are cross-correlation estimates. Without per-frame capture times they cannot separate "
            "recorder latency from exposure timing.",
            "The screens do not detect people or objects in view. Watch the flagged and a sample of clean episodes.",
            "Nothing here measures task success.",
        ]
        numbers = self.numbers()
        extra = [Template(str(item)).safe_substitute(numbers) for item in self.profile.get("limits", [])]
        return "".join(f"<li>{esc(item)}</li>" for item in items) + "".join(f"<li>{item}</li>" for item in extra)

    def lineage_panel(self) -> str:
        if not self.lineage:
            return ""
        nodes = []
        for node in self.lineage.get("nodes", []):
            lines = "".join(f"<p>{esc(line)}</p>" for line in node.get("lines", []))
            mono = f'<p class="mono">{esc(node["mono"])}</p>' if node.get("mono") else ""
            nodes.append(
                f'<div class="node"><div class="kind">{esc(node.get("kind", ""))}</div>'
                f"<h3>{esc(node.get('title', ''))}</h3>{mono}{lines}</div>"
            )
        links = []
        for link in self.lineage.get("links", []):
            url = str(link.get("url", ""))
            if urlparse(url).scheme != "https":
                logger.warning("skipping lineage link without https: %s", link.get("label"))
                continue
            links.append(
                f'<a class="btn alt" href="{esc(url)}" target="_blank" rel="noopener noreferrer">'
                f"{esc(link.get('label', url))}</a>"
            )
        summary = f'<p class="small">{esc(self.lineage["summary"])}</p>' if self.lineage.get("summary") else ""
        return (
            f'<div class="panel row-gap"><h3>{esc(self.lineage.get("title", "Versions and lineage"))}</h3>{summary}'
            f'<div class="lineage row-gap">{"".join(nodes)}</div><p class="row-gap">{"".join(links)}</p></div>'
        )

    # ------------------------------------------------------------------ player
    def phases(self, ep: int) -> list[list]:
        record, gripper = self.by_ep[ep], self.info[ep]["gripper"]
        duration = record["frames"] / self.fps
        if gripper and gripper["close"] is not None:
            phases = [["Before grasp", 0.0, 0], ["Gripper closed", round(gripper["close"] / self.fps, 4), 1]]
            if gripper["open"] is not None:
                phases.append(["After release", round(gripper["open"] / self.fps, 4), 2])
            return phases
        motion = record["motion"]
        if motion["idle_start_s"] is not None and motion["idle_end_s"] is not None:
            end = max(motion["idle_start_s"], duration - motion["idle_end_s"])
            return [["Idle", 0.0, 0], ["Moving", motion["idle_start_s"], 1], ["Idle", round(end, 4), 2]]
        return [["Episode", 0.0, 1]]

    def payload(self) -> dict:
        episodes = []
        durations = {ep: self.by_ep[ep]["duration_s"] for ep in self.eps if self.info[ep]["clip"]}
        median = float(np.median(list(durations.values()))) if durations else 0.0
        typical = min(durations, key=lambda ep: abs(durations[ep] - median)) if durations else self.eps[0]
        profile_notes = self.profile.get("episode_notes", {})
        for ep in self.eps:
            record, info = self.by_ep[ep], self.info[ep]
            duration = record["frames"] / self.fps
            ticks, notes, moments = [], [], [["start", 0.0]]
            gripper = info["gripper"]
            if gripper:
                for key, verb in (("close", "closes"), ("open", "opens")):
                    if gripper[key] is not None:
                        t = round(gripper[key] / self.fps, 4)
                        ticks.append(["event", t, f"Gripper {verb} at {t:.2f} s"])
                        moments.append([f"gripper {verb}", t])
            moments.append(["end", round((record["frames"] - 1) / self.fps, 4)])
            if record["capture"]:
                for frame, dt, missing in record["capture"]["gaps"]:
                    t = round(frame / self.fps, 4)
                    ticks.append(["gap", t, f"Capture gap {dt:.0f} ms"])
                    notes.append([t, f"Capture gap of {dt:.0f} ms ({plural(missing, 'frame')} missing)"])
            for a, b in record["stalls"]:
                t = round(a / self.fps, 4)
                ticks.append(["stall", t, "State stalled"])
                notes.append([t, f"State frozen for {(b - a + 1) / self.fps:.1f} s while commands change"])
            for flag in record["outliers"]:
                notes.append([0.0, f"Outlier on {flag['metric']}: {flag['value']:.4g} (z {flag['z']:+.1f})"])
            notes += [[0.0, text] for _, text in self.video_issues(ep)]
            if info["repeat"] >= 3:
                notes.append([0.0, f"Start condition shared with {plural(info['repeat'] - 1, 'other episode')}"])
            for t, text in profile_notes.get(str(ep), []):
                notes.append([float(t), str(text)])
            stats = (self.video.get(ep) or {}).get("cameras", {}).get(self.primary) or {}
            tracking = joint_tracking(record["tracking"])
            offset = stats.get("lag_vs_state")
            facts = [
                ["Task", "; ".join(record["tasks"]) or DASH],
                ["Duration", f"{duration:.2f} s · {record['frames']:,} frames"],
                ["Joint path", fmt(record["motion"]["path"], 3)],
                ["Peak speed", fmt(record["motion"]["peak_speed"], 3)],
                ["Idle share", f"{record['motion']['idle_fraction'] * 100:.0f} %"],
                ["Command lag", DASH if not tracking else plural(tracking["lag"], "frame")],
                ["Capture gaps", DASH if not record["capture"] else str(len(record["capture"]["gaps"]))],
                ["Camera offset", DASH if offset is None else f"{signed(offset)} frames"],
                ["Start condition", f"S{record['start_condition']} · {info['repeat']} episodes"],
            ]
            if gripper:
                close = DASH if gripper["close"] is None else f"{gripper['close'] / self.fps:.2f} s"
                opened = DASH if gripper["open"] is None else f"{gripper['open'] / self.fps:.2f} s"
                facts.insert(5, ["Gripper", f"closes {close} · opens {opened}"])
            tags = [text for _, text in info["flags"]][:3]
            episodes.append(
                {
                    "i": ep,
                    "n": record["frames"],
                    "dur": round(duration, 4),
                    "label": f"Episode {ep}" + (" · " + ", ".join(tags) if tags else ""),
                    "clip": info["clip"],
                    "phases": self.phases(ep),
                    "ticks": ticks,
                    "moments": moments,
                    "facts": facts,
                    "notes": sorted(notes, key=lambda note: note[0]),
                }
            )
        return {"fps": self.fps, "typical": typical, "episodes": episodes}

    def player_markup(self, prefix: str, labels: list[tuple[int, str]]) -> str:
        options = "".join(f'<option value="{ep}">{esc(label)}</option>' for ep, label in labels)
        rates = "".join(
            f'<button type="button" class="pbtn" data-rate="{v}" aria-pressed="{str(v == 1).lower()}">{v:g}{TIMES}'
            "</button>"
            for v in (0.25, 0.5, 1, 2)
        )
        cameras = ", ".join(short_camera(c) for c in self.cameras)
        return (
            '<div class="player" data-player>'
            f'<div class="player-bar"><label class="select-label" for="{prefix}-select">Episode</label>'
            f'<select id="{prefix}-select" data-select>{options}</select>'
            '<button type="button" class="pbtn" data-episode-step="-1" aria-label="Previous episode">◀</button>'
            '<button type="button" class="pbtn" data-episode-step="1" aria-label="Next episode">▶</button>'
            f'<span class="rates" role="group" aria-label="Playback speed">{rates}</span></div>'
            '<div class="player-grid"><div class="player-main">'
            f'<video controls muted playsinline preload="auto" aria-label="{esc(cameras)}"></video>'
            '<p class="no-video" hidden>No video for this episode</p>'
            '<div class="ptrack" role="slider" tabindex="0" aria-label="Episode timeline" aria-valuemin="0" '
            'aria-valuemax="0" aria-valuenow="0"><div class="bar"><i class="cursor"></i></div><div class="ticks"></div>'
            "</div>"
            '<p class="phase-line"><span class="phase-label" aria-live="polite"></span>'
            '<span class="readout mono small"></span></p>'
            f'<p class="frame-steps"><button type="button" class="pbtn" data-frame-step="-1">{MINUS}1 frame</button>'
            '<button type="button" class="pbtn" data-frame-step="1">+1 frame</button>'
            '<span class="small muted">Amber ticks: gripper events · red: capture gaps · purple: state stalls · '
            "click the timeline to seek.</span></p>"
            '</div><aside class="player-side"><h3 class="player-title"></h3><div class="moments"></div>'
            '<table class="facts stat"><tbody></tbody></table><h4 class="notes-title">In this episode</h4>'
            '<ul class="notes"></ul></aside></div></div>'
        )

    def clip_scripts(self) -> str:
        clips = (self.media_dir / "clips").resolve()
        out = []
        for ep in self.eps:
            video = self.video.get(ep)
            if not video or not video["clip"]:
                continue
            path = (clips / video["clip"]["path"]).resolve()
            if not path.is_relative_to(clips):
                raise ValueError(f"episode {ep}: clip path leaves the media clips folder")
            data = path.read_bytes()
            if sha256_bytes(data) != video["clip"]["sha256"]:
                raise ValueError(f"episode {ep}: clip does not match its recorded SHA-256")
            out.append(f'<script type="text/plain" id="clip-{ep:06d}">{base64.b64encode(data).decode()}</script>')
        return "\n".join(out)

    def thumb_store(self) -> str:
        images = []
        for ep in self.eps:
            thumbs = (self.video.get(ep) or {}).get("thumbs")
            if thumbs:
                uri = f"data:image/jpeg;base64,{thumbs['first']}"
                images.append(f'<img data-ep="{ep}" data-role="first" src="{uri}" alt="">')
        return "".join(images)

    # ------------------------------------------------------------------ assemble
    def context(self, styles: str) -> dict[str, str]:
        numbers = self.numbers()
        overview_chart, overview_legend, overview_caption = self.overview()
        starts_chart, starts_caption = self.starts_chart()
        holds, watch_items = self.readiness()
        payload = self.payload()
        labels = [(e["i"], e["label"]) for e in payload["episodes"]]
        canvas = self.media["clip"]["canvas"]
        primary = self.media["clip"]["sizes"][self.primary]
        tools = [
            ("observe.py", self.manifest["tool_sha256"]),
            ("media.py", self.media["tool_sha256"]),
            ("render.py", sha256_file(Path(__file__))),
        ]
        d = self.metrics["dataset"]
        default_eyebrow = " · ".join(
            part
            for part in (
                d["robot_type"],
                f"LeRobot {d['codebase_version']}",
                f"{self.fps:g} fps",
                plural(len(d["cameras"]), "camera"),
            )
            if part
        )
        tolerance = self.metrics["starts"]["tolerance_fraction"] * 100
        return {
            "report_title": esc(self.profile.get("title") or f"{d['name']} · observability review"),
            "styles": styles,
            "clip_aspect": f"{canvas[0]}/{canvas[1]}",
            "thumb_aspect": f"{primary[0]}/{primary[1]}",
            "brand": self.profile_html("brand", esc(d["name"]), numbers),
            "generated": esc(datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")),
            "eyebrow": self.profile_html("eyebrow", esc(default_eyebrow), numbers),
            "headline": self.profile_html("headline", self.headline(), numbers),
            "lead": self.profile_html(
                "lead",
                "Every data row and every decoded video frame was checked: file identity, clocks, joint motion, "
                "gripper events, command tracking, and video health. Section 07 lists what needs attention before "
                "training.",
                numbers,
            ),
            "chips": self.chips(),
            "mosaic_figure": self.mosaic_figure(),
            "metric_cards": self.metric_cards(),
            "readiness_intro": self.profile_html(
                "readiness_intro",
                "What the automated checks confirm, and what to resolve or keep in mind before training on this data.",
                numbers,
            ),
            "holds_items": "".join(f"<li>{esc(item)}</li>" for item in holds) or "<li>Nothing confirmed.</li>",
            "watch_items": "".join(f"<li>{esc(item)}</li>" for item in watch_items) or "<li>Nothing to watch.</li>",
            "overview_legend": overview_legend,
            "overview_chart": overview_chart,
            "overview_caption": overview_caption,
            "starts_intro": esc(
                f"{self.metrics['starts']['conditions']} distinct start conditions across {self.n} episodes. Hover a "
                "point to see where an episode began."
            ),
            "starts_chart": starts_chart,
            "starts_caption": starts_caption,
            "repeat_note": esc(
                f"Episodes whose first state is within {tolerance:.0f} % of each joint's range of the first episode in "
                "the group."
            ),
            "repeat_rows": self.repeat_rows(),
            "events_figure": self.events_figure(),
            "timing_intro": esc(
                "How the recording clock, the commands, and the cameras line up with the measured state."
            ),
            "capture_panel": self.capture_panel(),
            "tracking_rows": self.tracking_rows(),
            "max_lag": str(self.metrics["thresholds"]["max_lag_frames"]),
            "camera_chart": self.camera_chart(),
            "camera_rows": self.camera_rows(),
            "integrity_rows": self.integrity_rows(),
            "filter_buttons": self.filter_buttons(),
            "episode_rows": self.episode_rows(),
            "watch_intro": esc(
                f"{', '.join(short_camera(c) for c in self.cameras)}, cut frame for frame to match the data rows. The "
                "timeline shows the episode's phases; ticks mark gripper events, capture gaps, and stalls."
            ),
            "player_main": self.player_markup("main", labels),
            "player_dialog": self.player_markup("dialog", labels),
            "anomaly_cards": self.anomaly_cards(),
            "improvements": self.improvements(),
            "method_rows": self.method_rows(),
            "limits_items": self.limits(),
            "lineage_panel": self.lineage_panel(),
            "footer_note": self.profile_html(
                "footer_note",
                "Generated from the dataset bytes by observe.py, media.py, and render.py. The dataset was read, never "
                "written.",
                numbers,
            ),
            "tool_hashes": esc(" · ".join(f"{name} {digest[:16]}" for name, digest in tools)),
            "thumb_store": self.thumb_store(),
            "report_data": json.dumps(payload, separators=(",", ":"), allow_nan=False).replace("</", "<\\/"),
            "clip_scripts": self.clip_scripts(),
        }


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run(args: argparse.Namespace) -> int:
    for path in (args.bundle / "metrics.json", args.media / "video.json", args.template, args.styles):
        if not path.is_file():
            logger.error("missing input %s", path)
            return EXIT_ERROR
    report = Report(args)
    context = report.context(args.styles.read_text(encoding="utf-8"))
    try:
        page = Template(args.template.read_text(encoding="utf-8")).substitute(context)
    except (KeyError, ValueError) as exc:
        logger.error("template placeholder problem: %s", exc)
        return EXIT_FAILURE
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(page, encoding="utf-8")
    size_mb = args.output.stat().st_size / 1e6
    logger.info("wrote %s (%.1f MB, sha256 %s)", args.output, size_mb, sha256_file(args.output)[:16])
    return EXIT_SUCCESS


def main() -> int:
    """Main entry point."""
    args = create_parser().parse_args()
    configure_logging(args.verbose)
    try:
        return run(args)
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, KeyError) as exc:
        logger.error("%s", exc)
        return EXIT_FAILURE


if __name__ == "__main__":
    sys.exit(main())
