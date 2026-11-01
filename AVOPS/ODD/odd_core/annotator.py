# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

"""VLM Annotation Pipeline for ODD Video Datasets.

Sends each driving clip to an OpenAI-compatible VLM endpoint (e.g., Cosmos-Reason2-2B,
Cosmos-Reason1-7B, or vLLM-OMNI) as a ``video_url`` and parses structured answers against an ODD QA
template. The first frame can be sent instead (``media="frame"``) for endpoints without video input.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MEDIA_MODES = ("video", "frame")
DEFAULT_MAX_VIDEO_BYTES = 32 * 1024 * 1024


def extract_first_frame_b64(video_path: str | Path, max_dim: int = 768) -> str:
    """Extract first frame from an MP4/video file, resize to max_dim, and return base64 JPEG string."""
    v_path = Path(video_path)
    if not v_path.exists():
        raise FileNotFoundError(f"Video file does not exist: {v_path}")

    # 1. Try PyAV first
    try:
        import io

        import av

        with av.open(str(v_path)) as container:
            if container.streams.video:
                stream = container.streams.video[0]
                for frame in container.decode(stream):
                    img = frame.to_image()
                    img.thumbnail((max_dim, max_dim))
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=85)
                    return base64.b64encode(buf.getvalue()).decode("utf-8")
    except Exception as e:
        logger.debug("PyAV frame extraction failed for %s (%s), trying OpenCV fallback.", v_path.name, e)

    # 2. Fallback to OpenCV
    try:
        import cv2

        cap = cv2.VideoCapture(str(v_path))
        success, frame = cap.read()
        cap.release()
        if success and frame is not None:
            h, w = frame.shape[:2]
            if max(h, w) > max_dim:
                scale = max_dim / max(h, w)
                new_w, new_h = int(w * scale), int(h * scale)
                frame = cv2.resize(frame, (new_w, new_h))
            _, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            return base64.b64encode(buffer).decode("utf-8")
    except Exception as e:
        logger.debug("OpenCV frame extraction failed for %s (%s), trying ffmpeg fallback.", v_path.name, e)

    # 3. Fallback to ffmpeg CLI
    try:
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(v_path),
            "-vframes",
            "1",
            "-vf",
            f"scale='min({max_dim},iw)':-2",
            "-f",
            "image2pipe",
            "-vcodec",
            "mjpeg",
            "-q:v",
            "3",
            "pipe:1",
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True)
        if result.stdout:
            return base64.b64encode(result.stdout).decode("utf-8")
    except Exception as e:
        logger.debug("ffmpeg CLI extraction failed for %s: %s", v_path.name, e)

    raise ValueError(f"Could not extract keyframe from video: {v_path}")


def encode_video_b64(video_path: str | Path, max_bytes: int = DEFAULT_MAX_VIDEO_BYTES, max_dim: int = 768) -> str:
    """Return the whole clip as a base64 MP4.

    The file is sent as it is. It is re-encoded with ffmpeg (no audio, long side at most ``max_dim``)
    only when it is not an MP4 or is larger than ``max_bytes``.
    """
    v_path = Path(video_path)
    if not v_path.exists():
        raise FileNotFoundError(f"Video file does not exist: {v_path}")

    if v_path.suffix.lower() == ".mp4" and v_path.stat().st_size <= max_bytes:
        return base64.b64encode(v_path.read_bytes()).decode("utf-8")

    scale = f"scale='if(gt(iw,ih),min({max_dim},iw),-2)':'if(gt(iw,ih),-2,min({max_dim},ih))'"
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "clip.mp4"
        cmd = ["ffmpeg", "-y", "-i", str(v_path), "-an", "-vf", scale, "-c:v", "libx264"]
        cmd += ["-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(target)]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            detail = getattr(exc, "stderr", b"") or b""
            raise ValueError(
                f"Could not re-encode {v_path.name}: {exc} {detail.decode(errors='replace')[-300:]}"
            ) from exc
        if target.stat().st_size > max_bytes:
            raise ValueError(
                f"{v_path.name} is {target.stat().st_size / 1e6:.1f} MB after re-encoding; "
                f"the limit is {max_bytes / 1e6:.0f} MB"
            )
        return base64.b64encode(target.read_bytes()).decode("utf-8")


def clean_reasoning_tags(text: str) -> str:
    """Strip <think>...</think> and <thought>...</thought> reasoning blocks from text."""
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    cleaned = re.sub(r"<thought>.*?</thought>", "", cleaned, flags=re.DOTALL)
    return cleaned.strip()


class VLMAnnotator:
    """Annotates video datasets with ODD attributes using an OpenAI-compatible VLM endpoint."""

    def __init__(
        self,
        endpoint_url: str = "http://127.0.0.1:8010/v1",
        model_name: str = "nvidia/Cosmos-Reason2-2B",
        api_key: str | None = None,
        timeout: float = 120.0,
        media: str = "video",
        max_video_bytes: int = DEFAULT_MAX_VIDEO_BYTES,
    ) -> None:
        if media not in MEDIA_MODES:
            raise ValueError(f"media must be one of {MEDIA_MODES}, got {media!r}")
        self.media = media
        self.max_video_bytes = max_video_bytes
        self.endpoint_url = endpoint_url.rstrip("/")
        self.model_name = model_name
        self.api_key = api_key or os.environ.get("VLM_API_KEY", "not-used")
        self.timeout = timeout
        self._openai_client: Any = None

    def initialize(self) -> None:
        """Initialize the OpenAI client pointing to the remote VLM."""
        if self._openai_client is None:
            from openai import OpenAI

            self._openai_client = OpenAI(
                base_url=self.endpoint_url,
                api_key=self.api_key,
                timeout=self.timeout,
            )

    def check_health(self) -> tuple[bool, str]:
        """Verify if the VLM endpoint is reachable."""
        import urllib.error
        import urllib.request

        # Try base URL health check or /models
        base_origin = self.endpoint_url.split("/v1")[0]
        check_urls = [f"{base_origin}/health", f"{self.endpoint_url}/models"]

        for url in check_urls:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "ODD-Annotator/1.0"})
                with urllib.request.urlopen(req, timeout=3.0) as resp:
                    if resp.status in (200, 204):
                        return True, f"Healthy ({url} returned {resp.status})"
            except urllib.error.HTTPError as he:
                if he.code in (200, 401, 403, 404):
                    # Server is alive even if 401/404 on health
                    return True, f"Reachable ({url} returned {he.code})"
            except Exception:
                # Unreachable URL; try the next health endpoint.
                pass

        return False, f"Could not reach {self.endpoint_url}"

    def _media_part(self, video_path: Path) -> dict[str, Any]:
        """Chat content part carrying the clip (or its first frame) for the VLM."""
        if self.media == "video":
            b64 = encode_video_b64(video_path, max_bytes=self.max_video_bytes)
            return {"type": "video_url", "video_url": {"url": f"data:video/mp4;base64,{b64}"}}
        return {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{extract_first_frame_b64(video_path)}"},
        }

    def annotate_video(
        self,
        video_path: str | Path,
        qa_template: dict[str, dict[str, str]],
        fallback_mock: bool = False,
    ) -> dict[str, Any]:
        """Run ODD attribute questions against a single video, returning structured answers."""
        v_path = Path(video_path)

        flat_questions: list[dict[str, str]] = []
        for category, questions in qa_template.items():
            for key, question_text in questions.items():
                flat_questions.append(
                    {
                        "category": category,
                        "key": key,
                        "flat_key": f"{category}.{key}",
                        "question": question_text,
                    }
                )

        annotation_data: dict[str, Any] = {}

        # Check if remote VLM can be used
        media_part: dict[str, Any] | None = None
        if not fallback_mock:
            try:
                media_part = self._media_part(v_path)
            except Exception as e:
                raise RuntimeError(f"Failed to prepare {self.media} input from {v_path.name}: {e}") from e

        if media_part and not fallback_mock:
            self.initialize()
            scene = "driving video clip" if self.media == "video" else "driving scene image"
            questions_text = "\n".join([f"- {fq['flat_key']}: {fq['question']}" for fq in flat_questions])
            batch_prompt = (
                f"""Analyze the {scene} and answer the following questions.
Return your output ONLY as a JSON object where the keys are the question IDs and the values are the answers.

Questions:
{questions_text}

Output format:
{{
"""
                + ",\n".join([f'  "{fq["flat_key"]}": "answer"' for fq in flat_questions])
                + """
}
Respond with ONLY the valid JSON object."""
            )

            parsed_data: dict[str, Any] = {}
            try:
                batch_response = self._openai_client.chat.completions.create(
                    model=self.model_name,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are an autonomous driving perception classifier. "
                                "Output only the final JSON object."
                            ),
                        },
                        {
                            "role": "user",
                            "content": [media_part, {"type": "text", "text": batch_prompt}],
                        },
                    ],
                    temperature=0.1,
                    max_tokens=512,
                )
                raw_batch = batch_response.choices[0].message.content or ""
                cleaned_batch = clean_reasoning_tags(raw_batch)
                json_match = re.search(r"\{.*\}", cleaned_batch, re.DOTALL)
                if json_match:
                    try:
                        parsed_data = json.loads(json_match.group(0))
                    except Exception as parse_err:
                        logger.debug("Batch JSON parse error: %s", parse_err)
            except Exception as req_err:
                logger.warning("Batch VLM query failed on %s: %s", v_path.name, req_err)

            # Populate answers, falling back key-by-key for missing entries
            for fq in flat_questions:
                cat = fq["category"]
                k = fq["key"]
                fk = fq["flat_key"]
                ans = None

                if fk in parsed_data:
                    ans = str(parsed_data[fk]).strip()
                elif f"{cat}_{k}" in parsed_data:
                    ans = str(parsed_data[f"{cat}_{k}"]).strip()
                elif k in parsed_data:
                    ans = str(parsed_data[k]).strip()

                if not ans or ans.lower() in ["error", "null", "none", "unknown"]:
                    # Key-by-key fallback
                    try:
                        fb_resp = self._openai_client.chat.completions.create(
                            model=self.model_name,
                            messages=[
                                {
                                    "role": "user",
                                    "content": [media_part, {"type": "text", "text": fq["question"]}],
                                }
                            ],
                            temperature=0.1,
                            max_tokens=30,
                        )
                        raw_fb = fb_resp.choices[0].message.content or ""
                        ans = clean_reasoning_tags(raw_fb)
                    except Exception as fb_err:
                        logger.warning("Key fallback failed for %s.%s: %s", cat, k, fb_err)
                        ans = "Unknown"

                if cat not in annotation_data:
                    annotation_data[cat] = {}
                annotation_data[cat][k] = {"question": fq["question"], "answer": ans or "Unknown"}

            return annotation_data

        # Fallback Mock / Rule-Based Annotator
        logger.info("Generating mock perception annotation for %s", v_path.name)
        # Deterministic pseudo-random seed based on filename for reproducible testing
        seed_val = sum(ord(c) for c in v_path.stem)
        weather_options = ["Sunny", "Rainy", "Cloudy", "Snowy", "Foggy"]
        time_options = ["Morning", "Noon", "Evening", "Night"]
        road_width_options = ["Wide", "Medium", "Narrow"]
        traffic_options = ["None", "Straight", "Left", "Right"]

        mock_defaults = {
            "sceneDetails": {
                "weatherType": weather_options[seed_val % len(weather_options)],
                "timeOfDay": time_options[seed_val % len(time_options)],
            },
            "roadDetails": {
                "roadParameters": road_width_options[seed_val % len(road_width_options)],
                "trafficLights": traffic_options[seed_val % len(traffic_options)],
                "roadSurface": "Asphalt",
            },
            "dynamicAgentDetails": {
                "any_vehicle_lane_change": "Yes" if (seed_val % 2 == 0) else "No",
                "any_vehicle_cut_in": "Yes" if (seed_val % 3 == 0) else "No",
            },
            "otherAgentDetails": {
                "pedestrianDetails": "Yes" if (seed_val % 4 == 0) else "No",
            },
        }

        for fq in flat_questions:
            cat = fq["category"]
            k = fq["key"]
            ans = mock_defaults.get(cat, {}).get(k, "Sunny")
            if cat not in annotation_data:
                annotation_data[cat] = {}
            annotation_data[cat][k] = {"question": fq["question"], "answer": ans}

        return annotation_data
