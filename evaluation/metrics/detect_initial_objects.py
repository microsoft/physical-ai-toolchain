"""Detect initial pixel positions of the white gear and blue bin in each episode.

Reads the first frame of `observation.images.color` (the top-down workspace
view) for every episode in one or more LeRobot v3.0 datasets, then runs HSV
color segmentation to locate:

* the **white gear** (low saturation, high value)
* the **blue bin** (hue around the OpenCV blue band, high saturation)

For each detection we report the centroid (cx, cy in pixels), area, and
oriented bounding-box angle when available. Aggregate distribution stats
(mean, std, min, p25, p50, p75, max) are computed across episodes.

The script intentionally stays simple — no neural detector is required for
clean-background workspace footage like this. It is robust as long as there
are no other large white or blue objects in the scene.

Outputs:
* `<output_dir>/initial-object-detections.json` — full per-episode rows + aggregate
* `<output_dir>/preview/<dataset>/episode_<N>.png` — annotated first frames
  for visual sanity-checking (only when `--save-previews` is set)
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import av
import cv2
import numpy as np
import pandas as pd

CAMERA_KEY = "observation.images.color"

# HSV color ranges, tuned on episode 0 of hybrid-hack-vla-train-full.
# OpenCV uses H ∈ [0, 179], S/V ∈ [0, 255].
WHITE_HSV_LOWER = np.array([0, 0, 180], dtype=np.uint8)
WHITE_HSV_UPPER = np.array([179, 60, 255], dtype=np.uint8)
BLUE_HSV_LOWER = np.array([90, 90, 60], dtype=np.uint8)
BLUE_HSV_UPPER = np.array([130, 255, 255], dtype=np.uint8)

# Pixel-area gates exclude small noise / off-camera regions.
# Gear can range from ~500 px when partially occluded to ~6000 px when fully
# visible (78x62 bounding box on the workspace).
WHITE_MIN_AREA = 300
WHITE_MAX_AREA = 8000
BLUE_MIN_AREA = 5000
BLUE_MAX_AREA = 200000


@dataclass(frozen=True)
class Detection:
    """Single object detection result in pixel coordinates."""

    found: bool
    cx: float
    cy: float
    area: float
    bbox: tuple[int, int, int, int]  # x, y, w, h
    angle_deg: float


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--datasets",
        nargs="+",
        required=True,
        help="One or more LeRobot dataset roots to analyze.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory for the JSON summary and optional previews.",
    )
    parser.add_argument(
        "--save-previews",
        action="store_true",
        help="Save annotated first-frame previews under <output-dir>/preview/<dataset>/.",
    )
    parser.add_argument(
        "--max-episodes",
        type=int,
        default=None,
        help="Cap episodes per dataset for quick validation (default: all).",
    )
    return parser.parse_args()


def _load_episode_meta(root: Path) -> pd.DataFrame:
    files = sorted((root / "meta" / "episodes").rglob("*.parquet"))
    if not files:
        raise SystemExit(f"[ERROR] No episode metadata under {root}/meta/episodes")
    return pd.concat([pd.read_parquet(p) for p in files], ignore_index=True).sort_values("episode_index")


def _read_first_frame(root: Path, ep_meta: pd.Series) -> np.ndarray:
    """Decode the first frame of `CAMERA_KEY` for the given episode."""
    chunk = int(ep_meta[f"videos/{CAMERA_KEY}/chunk_index"])
    file_idx = int(ep_meta[f"videos/{CAMERA_KEY}/file_index"])
    from_ts = float(ep_meta[f"videos/{CAMERA_KEY}/from_timestamp"])
    video_path = root / "videos" / CAMERA_KEY / f"chunk-{chunk:03d}" / f"file-{file_idx:03d}.mp4"
    container = av.open(str(video_path))
    try:
        stream = container.streams.video[0]
        # `seek` jumps to the nearest keyframe before `from_ts`; we then decode
        # frames forward until we land at-or-past the requested timestamp.
        target_pts = int(from_ts / stream.time_base) if stream.time_base else 0
        container.seek(target_pts, stream=stream, any_frame=False, backward=True)
        frame = None
        for candidate in container.decode(stream):
            frame = candidate
            if candidate.pts is None or candidate.pts >= target_pts:
                break
        if frame is None:
            raise SystemExit(f"[ERROR] No decodable frame in {video_path}")
        return frame.to_ndarray(format="rgb24")
    finally:
        container.close()


def _largest_blob_centroid(
    mask: np.ndarray,
    min_area: float,
    max_area: float,
    *,
    max_aspect_ratio: float = float("inf"),
) -> Detection:
    """Return the largest connected component within the area + aspect gates.

    `max_aspect_ratio` rejects long thin rectangles (e.g., the keyboard rim
    that intrudes into the top of camera 1) when looking for round objects.
    """
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best: Detection | None = None
    for contour in contours:
        area = float(cv2.contourArea(contour))
        if area < min_area or area > max_area:
            continue
        x, y, w, h = cv2.boundingRect(contour)
        if w == 0 or h == 0:
            continue
        aspect = max(w, h) / max(min(w, h), 1)
        if aspect > max_aspect_ratio:
            continue
        moments = cv2.moments(contour)
        if moments["m00"] <= 0:
            continue
        cx = float(moments["m10"] / moments["m00"])
        cy = float(moments["m01"] / moments["m00"])
        rect = cv2.minAreaRect(contour)
        angle = float(rect[-1])
        if best is None or area > best.area:
            best = Detection(
                found=True,
                cx=cx,
                cy=cy,
                area=area,
                bbox=(int(x), int(y), int(w), int(h)),
                angle_deg=angle,
            )
    if best is None:
        return Detection(
            found=False,
            cx=float("nan"),
            cy=float("nan"),
            area=0.0,
            bbox=(0, 0, 0, 0),
            angle_deg=float("nan"),
        )
    return best


def _detect_white_gear(rgb: np.ndarray) -> Detection:
    """White gear is a small, low-saturation, high-value, roughly-circular blob.

    The keyboard at the top edge of the camera 1 frame is also white but is
    a long thin strip; we reject it via aspect ratio and a top-of-frame mask.
    """
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    mask = cv2.inRange(hsv, WHITE_HSV_LOWER, WHITE_HSV_UPPER)
    h = mask.shape[0]
    # Mask the keyboard band at the top (~6% of the frame) and the table rim
    # at the bottom (~15%).
    mask[: int(h * 0.06)] = 0
    mask[int(h * 0.85) :] = 0
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    return _largest_blob_centroid(mask, WHITE_MIN_AREA, WHITE_MAX_AREA, max_aspect_ratio=2.5)


def _detect_blue_bin(rgb: np.ndarray) -> Detection:
    """Blue bin is the dominant blue blob on the workspace."""
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    mask = cv2.inRange(hsv, BLUE_HSV_LOWER, BLUE_HSV_UPPER)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    return _largest_blob_centroid(mask, BLUE_MIN_AREA, BLUE_MAX_AREA)


def _annotate(rgb: np.ndarray, gear: Detection, bin_: Detection) -> np.ndarray:
    """Draw bounding boxes and centroids for visual sanity checks."""
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    if gear.found:
        x, y, w, h = gear.bbox
        cv2.rectangle(bgr, (x, y), (x + w, y + h), (0, 255, 0), 2)
        cv2.circle(bgr, (int(gear.cx), int(gear.cy)), 4, (0, 255, 0), -1)
        cv2.putText(bgr, "gear", (x, max(y - 4, 12)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
    if bin_.found:
        x, y, w, h = bin_.bbox
        cv2.rectangle(bgr, (x, y), (x + w, y + h), (255, 0, 0), 2)
        cv2.circle(bgr, (int(bin_.cx), int(bin_.cy)), 4, (255, 0, 0), -1)
        cv2.putText(bgr, "bin", (x, max(y - 4, 12)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 0), 1)
    return bgr


def _aggregate_centroids(rows: list[dict[str, Any]], object_key: str) -> dict[str, Any]:
    df = pd.DataFrame([r for r in rows if r[object_key]["found"]])
    if df.empty:
        return {"detections": 0}
    cx = df[object_key].apply(lambda d: d["cx"])
    cy = df[object_key].apply(lambda d: d["cy"])
    area = df[object_key].apply(lambda d: d["area"])
    return {
        "detections": int(df.shape[0]),
        "missing": int(len(rows) - df.shape[0]),
        "cx": {
            "mean": float(cx.mean()),
            "std": float(cx.std()),
            "min": float(cx.min()),
            "p25": float(cx.quantile(0.25)),
            "p50": float(cx.quantile(0.50)),
            "p75": float(cx.quantile(0.75)),
            "max": float(cx.max()),
        },
        "cy": {
            "mean": float(cy.mean()),
            "std": float(cy.std()),
            "min": float(cy.min()),
            "p25": float(cy.quantile(0.25)),
            "p50": float(cy.quantile(0.50)),
            "p75": float(cy.quantile(0.75)),
            "max": float(cy.max()),
        },
        "area": {
            "mean": float(area.mean()),
            "std": float(area.std()),
            "min": float(area.min()),
            "p50": float(area.quantile(0.50)),
            "max": float(area.max()),
        },
    }


def _analyze_dataset(root: Path, max_episodes: int | None, preview_dir: Path | None) -> dict[str, Any]:
    episodes = _load_episode_meta(root)
    if max_episodes is not None:
        episodes = episodes.head(max_episodes)
    print(f"[INFO] {root.name}: detecting on {len(episodes)} episodes")

    rows: list[dict[str, Any]] = []
    for _, ep_meta in episodes.iterrows():
        episode_index = int(ep_meta["episode_index"])
        try:
            rgb = _read_first_frame(root, ep_meta)
        except Exception as exc:
            print(f"[WARN] episode {episode_index}: failed to read first frame ({exc})")
            continue
        gear = _detect_white_gear(rgb)
        bin_ = _detect_blue_bin(rgb)
        rows.append(
            {
                "episode_index": episode_index,
                "image_size": [int(rgb.shape[1]), int(rgb.shape[0])],
                "white_gear": {
                    "found": gear.found,
                    "cx": gear.cx,
                    "cy": gear.cy,
                    "area": gear.area,
                    "bbox": list(gear.bbox),
                    "angle_deg": gear.angle_deg,
                },
                "blue_bin": {
                    "found": bin_.found,
                    "cx": bin_.cx,
                    "cy": bin_.cy,
                    "area": bin_.area,
                    "bbox": list(bin_.bbox),
                    "angle_deg": bin_.angle_deg,
                },
            }
        )
        if preview_dir is not None:
            preview_dir.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(preview_dir / f"episode_{episode_index:03d}.png"), _annotate(rgb, gear, bin_))

    return {
        "dataset": root.name,
        "dataset_path": str(root),
        "episode_count": len(rows),
        "image_size": rows[0]["image_size"] if rows else None,
        "episodes": rows,
        "white_gear_aggregate": _aggregate_centroids(rows, "white_gear"),
        "blue_bin_aggregate": _aggregate_centroids(rows, "blue_bin"),
    }


def main() -> int:
    args = _parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = args.output_dir / "initial-object-detections.json"

    results: dict[str, Any] = {"datasets": []}
    for raw in args.datasets:
        root = Path(raw)
        if not root.exists():
            print(f"[ERROR] Dataset not found: {root}", file=sys.stderr)
            return 2
        preview_dir = args.output_dir / "preview" / root.name if args.save_previews else None
        results["datasets"].append(_analyze_dataset(root, args.max_episodes, preview_dir))

    summary_path.write_text(json.dumps(results, indent=2))
    print(f"[INFO] Wrote summary to {summary_path}")

    print("\n=== Aggregate distributions ===")
    for entry in results["datasets"]:
        print(f"\n[{entry['dataset']}] image={entry['image_size']} episodes={entry['episode_count']}")
        for key, label in [("white_gear_aggregate", "white_gear"), ("blue_bin_aggregate", "blue_bin")]:
            agg = entry[key]
            if agg.get("detections"):
                cx, cy = agg["cx"], agg["cy"]
                print(
                    f"  {label:10s} found={agg['detections']:3d}/{entry['episode_count']:3d}  "
                    f"cx mean={cx['mean']:6.1f}±{cx['std']:5.1f} (range {cx['min']:.0f}..{cx['max']:.0f})  "
                    f"cy mean={cy['mean']:6.1f}±{cy['std']:5.1f} (range {cy['min']:.0f}..{cy['max']:.0f})"
                )
            else:
                print(f"  {label:10s} NO DETECTIONS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
