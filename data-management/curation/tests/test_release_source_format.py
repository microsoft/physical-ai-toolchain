"""Native source metadata cannot broaden object paths or invent valid episodes."""

from __future__ import annotations

import json

import pytest
from physical_ai_curation.release.inputs import ReleaseInputError, SourceMetadataError
from physical_ai_curation.release.source_format import (
    V21_EPISODE_STATS_PATH,
    V21_EPISODES_PATH,
    object_path,
    v21_episode_rows,
    v21_tasks_table,
)


def _info() -> dict[str, object]:
    return {
        "codebase_version": "v2.1",
        "chunks_size": 1000,
        "fps": 10.0,
        "features": {"front": {"dtype": "video"}},
        "data_path": "data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.parquet",
        "video_path": "videos/chunk-{episode_chunk:03d}/{video_key}/episode_{episode_index:06d}.mp4",
    }


@pytest.mark.parametrize("version", [None, "v1.0", "v4.0"])
def test_native_object_paths_refuse_unsupported_source_layout(version: object) -> None:
    with pytest.raises(ReleaseInputError, match="only native"):
        object_path({"codebase_version": version}, {}, "data")


@pytest.mark.parametrize("field", ["data/chunk_index", "data/file_index"])
@pytest.mark.parametrize("value", [None, True, -1, 1.0])
def test_v3_object_paths_require_exact_nonnegative_integer_coordinates(field: str, value: object) -> None:
    row = {"data/chunk_index": 0, "data/file_index": 0, field: value}

    with pytest.raises(SourceMetadataError) as raised:
        object_path({"codebase_version": "v3.0"}, row, "data")

    assert raised.value.code == "source_object_reference_invalid"


@pytest.mark.parametrize("field", ["episode_index", "chunks_size"])
@pytest.mark.parametrize("value", [True, -1, 1.5])
def test_v21_object_paths_require_valid_episode_and_chunk_size(field: str, value: object) -> None:
    info = _info()
    row = {"episode_index": 1}
    if field == "episode_index":
        row[field] = value
    else:
        info[field] = value

    with pytest.raises(SourceMetadataError) as raised:
        object_path(info, row, "data")

    assert raised.value.code == "source_object_reference_invalid"


@pytest.mark.parametrize(
    "template",
    [
        None,
        "x" * 1025,
        "data/{unknown}.parquet",
        "data/{episode_index!r}.parquet",
        "data/{episode_index:100d}.parquet",
        "data/{episode_index.real}.parquet",
        "data/{episode_index",
        "videos/{video_key:03d}/episode.mp4",
    ],
)
def test_v21_path_templates_allow_only_bounded_native_format_fields(template: object) -> None:
    info = _info()
    info["data_path"] = template
    info["video_path"] = template

    with pytest.raises(SourceMetadataError) as raised:
        object_path(
            info, {"episode_index": 0}, "videos/front" if template == "videos/{video_key:03d}/episode.mp4" else "data"
        )

    assert raised.value.code == "source_metadata_invalid"


@pytest.mark.parametrize("fps", [True, 0, -1, "10", float("nan"), float("inf")])
def test_v21_episode_metadata_requires_positive_finite_fps(fps: object) -> None:
    info = _info()
    info["fps"] = fps
    with pytest.raises(SourceMetadataError) as raised:
        v21_episode_rows(info, lambda _path: b'{"episode_index":0,"length":2}\n', has_statistics=False)

    assert raised.value.code == "source_metadata_invalid"


@pytest.mark.parametrize(
    "records",
    [
        [],
        [{"episode_index": True, "length": 2}],
        [{"episode_index": 0, "length": 0}],
        [{"episode_index": 0, "length": True}],
        [{"episode_index": -1, "length": 2}],
        [{"episode_index": 0, "length": 2}] * 2,
    ],
)
def test_v21_episode_metadata_refuses_empty_ambiguous_or_invalid_rows(records: list[dict[str, object]]) -> None:
    raw = b"".join(json.dumps(record).encode() + b"\n" for record in records)

    with pytest.raises(SourceMetadataError) as raised:
        v21_episode_rows(_info(), lambda _path: raw, has_statistics=False)

    assert raised.value.code == "source_metadata_invalid"


@pytest.mark.parametrize(
    "statistics",
    [
        [{"episode_index": 0, "stats": []}],
        [{"episode_index": "0", "stats": {}}],
        [{"episode_index": 0, "stats": {}}] * 2,
        [{"episode_index": 0, "stats": {"front": "invalid"}}],
    ],
)
def test_v21_episode_statistics_refuse_unusable_or_duplicate_bindings(
    statistics: list[dict[str, object]],
) -> None:
    objects = {
        V21_EPISODES_PATH: b'{"episode_index":0,"length":2}\n',
        V21_EPISODE_STATS_PATH: b"".join(json.dumps(row).encode() + b"\n" for row in statistics),
    }

    with pytest.raises(ReleaseInputError, match=r"statistics are ambiguous|video statistics are invalid"):
        v21_episode_rows(_info(), objects.__getitem__)


@pytest.mark.parametrize(
    "content",
    [
        b"[]\n",
        b"not-json\n",
        b'{"task_index":"0","task":"pick"}\n',
        b'{"task_index":0,"task":1}\n',
    ],
)
def test_v21_task_decode_errors_keep_public_category_and_not_untrusted_payload(content: bytes) -> None:
    with pytest.raises(SourceMetadataError) as raised:
        v21_tasks_table(lambda _path: content)

    assert raised.value.code == "source_task_metadata_invalid"
    assert str(raised.value) == "v2.1 task metadata is invalid"


def test_v21_jsonl_ignores_blank_lines_without_charging_row_budget() -> None:
    table = v21_tasks_table(lambda _path: b'\n \n{"task_index":0,"task":"pick"}\n\n', max_rows=1)

    assert table.to_pylist() == [{"task_index": 0, "task": "pick"}]
