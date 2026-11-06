from __future__ import annotations

import io
import struct

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from physical_ai_curation.release.range_reader import (
    RangeReadError,
    RevisionRangeFile,
    read_footer,
    read_selected_rows,
    selected_row_groups,
)


def _footer_start(payload: bytes) -> int:
    (footer_length,) = struct.unpack("<I", payload[-8:-4])
    return len(payload) - 8 - footer_length


def _column_ranges(group: pq.RowGroupMetaData) -> list[tuple[int, int]]:
    ranges = []
    for position in range(group.num_columns):
        column = group.column(position)
        start = column.dictionary_page_offset or column.data_page_offset
        ranges.append((start, start + column.total_compressed_size))
    return ranges


def _parquet_bytes(episodes: list[int], rows_per_group: int) -> bytes:
    frames = list(range(len(episodes)))
    table = pa.table({"episode_index": pa.array(episodes, pa.int64()), "frame_index": pa.array(frames, pa.int64())})
    sink = io.BytesIO()
    pq.write_table(table, sink, row_group_size=rows_per_group)
    return sink.getvalue()


def _range_file(payload: bytes, *, counter: list[tuple[int, int]] | None = None) -> RevisionRangeFile:
    def fetch(offset: int, length: int) -> bytes:
        if counter is not None:
            counter.append((offset, length))
        return payload[offset : offset + length]

    return RevisionRangeFile(len(payload), fetch)


def test_given_packed_parquet_when_read_selected_rows_then_returns_only_wanted_episodes() -> None:
    payload = _parquet_bytes([0, 0, 1, 1, 2, 2, 3, 3], rows_per_group=2)
    file = _range_file(payload)

    table = read_selected_rows(file, "episode_index", {1, 3}, max_footer_bytes=4096, max_decoded_bytes=1 << 20)

    assert table["episode_index"].to_pylist() == [1, 1, 3, 3]


def test_given_unrelated_row_groups_when_read_selected_rows_then_requests_avoid_their_bytes() -> None:
    payload = _parquet_bytes([0, 0, 1, 1, 2, 2, 3, 3], rows_per_group=2)
    requests = []
    file = _range_file(payload, counter=requests)
    metadata = pq.ParquetFile(pa.BufferReader(payload)).metadata
    footer_start = _footer_start(payload)

    read_selected_rows(file, "episode_index", {0}, max_footer_bytes=4096, max_decoded_bytes=1 << 20)

    selected = _column_ranges(metadata.row_group(0))
    data_requests = [(offset, length) for offset, length in requests if offset < footer_start]
    assert data_requests
    for offset, length in data_requests:
        assert any(start <= offset and offset + length <= end for start, end in selected)


def test_given_missing_episode_when_read_selected_rows_then_refuses() -> None:
    payload = _parquet_bytes([0, 0, 1, 1], rows_per_group=2)
    file = _range_file(payload)

    with pytest.raises(RangeReadError):
        read_selected_rows(file, "episode_index", {7}, max_footer_bytes=4096, max_decoded_bytes=1 << 20)


def test_given_oversized_footer_when_read_footer_then_refuses_before_reading_footer() -> None:
    payload = _parquet_bytes([0, 1, 2, 3], rows_per_group=2)
    file = _range_file(payload)

    with pytest.raises(RangeReadError):
        read_footer(file, max_footer_bytes=1)


def test_given_footer_bound_zero_when_read_footer_then_refuses() -> None:
    payload = _parquet_bytes([0, 1], rows_per_group=1)
    file = _range_file(payload)

    with pytest.raises(RangeReadError):
        read_footer(file, max_footer_bytes=0)


def test_given_decoded_bound_too_small_when_read_selected_rows_then_refuses() -> None:
    payload = _parquet_bytes([0, 0, 1, 1], rows_per_group=2)
    file = _range_file(payload)

    with pytest.raises(RangeReadError):
        read_selected_rows(file, "episode_index", {0}, max_footer_bytes=4096, max_decoded_bytes=1)


def test_given_no_allowance_when_read_then_refuses() -> None:
    payload = _parquet_bytes([0, 1], rows_per_group=1)
    file = _range_file(payload)

    with pytest.raises(RangeReadError):
        file.read(8)


def test_given_negative_allowance_when_allow_then_refuses() -> None:
    file = _range_file(b"x" * 16)

    with pytest.raises(RangeReadError):
        file.allow(-1)


def test_given_read_beyond_allowance_when_readinto_then_refuses() -> None:
    payload = b"0123456789abcdef"
    file = _range_file(payload)
    file.allow(4)

    with pytest.raises(RangeReadError):
        file.read(8)


def test_given_short_fetch_when_read_then_refuses() -> None:
    def short(offset: int, length: int) -> bytes:
        return b"\x00"

    file = RevisionRangeFile(16, short)
    file.allow(16)

    with pytest.raises(RangeReadError):
        file.read(8)


def test_given_row_group_without_statistics_when_selected_row_groups_then_refuses() -> None:
    payload = _parquet_bytes([0, 1], rows_per_group=1)
    metadata = read_footer(_range_file(payload), max_footer_bytes=4096)

    with pytest.raises(RangeReadError):
        selected_row_groups(metadata, "missing_column", {0}, max_decoded_bytes=1 << 20)


def test_given_seek_before_start_when_seek_then_refuses() -> None:
    file = _range_file(b"abcdef")

    with pytest.raises(RangeReadError):
        file.seek(-1)


def test_given_seek_end_when_seek_then_position_relative_to_length() -> None:
    file = _range_file(b"abcdef")

    assert file.seek(-2, io.SEEK_END) == 4
    assert file.tell() == 4


def test_given_unsupported_origin_when_seek_then_refuses() -> None:
    file = _range_file(b"abcdef")

    with pytest.raises(RangeReadError):
        file.seek(0, 9)


def test_given_fetch_when_read_then_returns_fetched_bytes_only() -> None:
    file = RevisionRangeFile(8, lambda offset, length: b"z" * length)
    file.allow(8)

    assert file.read(8) == b"z" * 8
