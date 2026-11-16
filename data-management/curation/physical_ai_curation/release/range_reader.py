"""Bounded seekable reads of selected Parquet row groups from one revision-bound object.

The adapter performs no snapshot or payload staging. It refuses before allocating an
oversized footer or row group, and it never falls back to reading the whole object.
"""

from __future__ import annotations

import io
import struct
from collections.abc import Callable, Iterable

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from physical_ai_curation.release.errors import ReleaseVerificationError

PARQUET_MAGIC = b"PAR1"
_TRAILER_BYTES = 8


class RangeReadError(ReleaseVerificationError):
    """A bounded range read cannot prove the selected Parquet rows."""


class RevisionRangeFile(io.RawIOBase):
    """Read-only seekable view whose every range is delegated to a revision-conditioned fetch.

    Reads are charged against an allowance that callers grow only from validated metadata.
    """

    def __init__(self, length: int, fetch: Callable[[int, int], bytes]) -> None:
        super().__init__()
        self._length = length
        self._fetch = fetch
        self._position = 0
        self._allowance = 0
        self.transferred = 0

    @property
    def length(self) -> int:
        return self._length

    def allow(self, additional: int) -> None:
        """Raise the transfer allowance by a validated, nonnegative amount."""
        if type(additional) is not int or additional < 0:
            raise RangeReadError("range allowance must be a nonnegative integer")
        self._allowance += additional

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._position

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            target = offset
        elif whence == io.SEEK_CUR:
            target = self._position + offset
        elif whence == io.SEEK_END:
            target = self._length + offset
        else:
            raise RangeReadError("unsupported seek origin")
        if target < 0:
            raise RangeReadError("seek before the start of the object")
        self._position = target
        return target

    def readinto(self, buffer: bytearray | memoryview) -> int:  # type: ignore[override]
        view = memoryview(buffer).cast("B")
        wanted = min(len(view), max(0, self._length - self._position))
        if wanted == 0:
            return 0
        if self.transferred + wanted > self._allowance:
            raise RangeReadError("range reads exceed their admitted byte bound")
        data = self._fetch(self._position, wanted)
        if len(data) != wanted:
            raise RangeReadError("range read returned an unexpected length")
        view[:wanted] = data
        self._position += wanted
        self.transferred += wanted
        return wanted


def _positive_int(value: object, message: str) -> int:
    if type(value) is not int or value <= 0:
        raise RangeReadError(message)
    return value


def read_footer(file: RevisionRangeFile, *, max_footer_bytes: int) -> pq.FileMetaData:
    """Parse the Parquet footer after refusing an oversized declared footer."""
    _positive_int(max_footer_bytes, "footer bound must be positive")
    if file.length < _TRAILER_BYTES + len(PARQUET_MAGIC) * 2:
        raise RangeReadError("object is too small to be a Parquet file")
    file.allow(_TRAILER_BYTES)
    file.seek(file.length - _TRAILER_BYTES)
    trailer = file.read(_TRAILER_BYTES)
    if len(trailer) != _TRAILER_BYTES or trailer[4:] != PARQUET_MAGIC:
        raise RangeReadError("Parquet footer trailer is missing")
    (footer_length,) = struct.unpack("<I", trailer[:4])
    if footer_length > max_footer_bytes or footer_length + _TRAILER_BYTES > file.length:
        raise RangeReadError("Parquet footer exceeds its byte bound")
    file.allow(footer_length + _TRAILER_BYTES)
    file.seek(file.length - _TRAILER_BYTES - footer_length)
    region = file.read(footer_length + _TRAILER_BYTES)
    if len(region) != footer_length + _TRAILER_BYTES:
        raise RangeReadError("Parquet footer is truncated")
    try:
        parquet = pq.ParquetFile(
            pa.BufferReader(region),
            thrift_string_size_limit=max_footer_bytes,
            thrift_container_size_limit=max_footer_bytes,
        )
    except (OSError, pa.ArrowException, ValueError) as exc:
        raise RangeReadError("Parquet footer is malformed or oversized") from exc
    return parquet.metadata


def _episode_position(group: pq.RowGroupMetaData, column: str) -> int:
    for position in range(group.num_columns):
        if group.column(position).path_in_schema == column:
            return position
    raise RangeReadError("row group lacks the episode column")


def selected_row_groups(
    metadata: pq.FileMetaData,
    column: str,
    episodes: Iterable[int],
    *,
    max_decoded_bytes: int,
) -> tuple[int, ...]:
    """Choose row groups whose integer column statistics can contain a wanted episode."""
    wanted = set(episodes)
    chosen = []
    decoded = 0
    for index in range(metadata.num_row_groups):
        group = metadata.row_group(index)
        statistics = group.column(_episode_position(group, column)).statistics
        if statistics is None or not statistics.has_min_max:
            raise RangeReadError("row group lacks episode statistics; refusing a whole-object read")
        low, high = statistics.min, statistics.max
        if not isinstance(low, int) or not isinstance(high, int):
            raise RangeReadError("episode statistics must be integers")
        if not any(low <= episode <= high for episode in wanted):
            continue
        decoded += group.total_byte_size
        if decoded > max_decoded_bytes:
            raise RangeReadError("selected row groups exceed their decoded byte bound")
        chosen.append(index)
    return tuple(chosen)


def _compressed_bytes(metadata: pq.FileMetaData, groups: Iterable[int]) -> int:
    total = 0
    for index in groups:
        group = metadata.row_group(index)
        total += sum(group.column(position).total_compressed_size for position in range(group.num_columns))
    return total


def read_selected_rows(
    file: RevisionRangeFile,
    column: str,
    episodes: Iterable[int],
    *,
    max_footer_bytes: int,
    max_decoded_bytes: int,
) -> pa.Table:
    """Decode only row groups that can contain the wanted episodes, then keep exactly those rows."""
    wanted = frozenset(episodes)
    if not wanted:
        raise RangeReadError("select at least one episode")
    _positive_int(max_footer_bytes, "footer bound must be positive")
    _positive_int(max_decoded_bytes, "decoded bound must be positive")
    metadata = read_footer(file, max_footer_bytes=max_footer_bytes)
    groups = selected_row_groups(metadata, column, wanted, max_decoded_bytes=max_decoded_bytes)
    if not groups:
        raise RangeReadError("no row group contains the selected episodes")
    compressed_bytes = _compressed_bytes(metadata, groups)
    if compressed_bytes > max_decoded_bytes:
        raise RangeReadError("selected row groups exceed their transfer byte bound")
    file.allow(compressed_bytes)
    parquet = pq.ParquetFile(file, metadata=metadata, pre_buffer=False)
    table = parquet.read_row_groups(list(groups))
    if table.nbytes > max_decoded_bytes:
        raise RangeReadError("decoded table exceeds its byte bound")
    values = pa.array(sorted(wanted), type=table.schema.field(column).type)
    selected = table.filter(pc.is_in(table[column], value_set=values))
    if not wanted <= set(selected[column].to_pylist()):
        raise RangeReadError("selected episode rows are missing from the object")
    return selected
