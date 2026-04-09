"""Generate initial seed corpus for the fuzz harness.

Creates binary seed files in ``tests/fuzz-corpus/`` that give Atheris
meaningful starting points for mutation-based fuzzing.  Each seed embeds
a routing byte (``data[0] % 9``) that selects the target, followed by
byte sequences representative of that target's input domain.

Run this script once to bootstrap the corpus, then pass the directory to
the harness::

    python tests/generate_fuzz_corpus.py
    python tests/fuzz_harness.py tests/fuzz-corpus/
"""

from __future__ import annotations

import struct
from pathlib import Path

_CORPUS_DIR = Path(__file__).parent / "fuzz-corpus"

# Routing bytes: data[0] % 9 selects the target index in FUZZ_TARGETS.
_T0 = b"\x00"  # fuzz_validate_blob_path
_T1 = b"\x01"  # fuzz_get_validation_error
_T2 = b"\x02"  # fuzz_extract_from_value
_T3 = b"\x03"  # fuzz_extract_from_tracking_data
_T4 = b"\x04"  # fuzz_sanitize_user_string
_T5 = b"\x05"  # fuzz_sanitize_nested_value
_T6 = b"\x06"  # fuzz_validate_safe_string
_T7 = b"\x07"  # fuzz_dataset_id_to_blob_prefix
_T8 = b"\x08"  # fuzz_datetime_encoder


def _double(val: float) -> bytes:
    return struct.pack("<d", val)


# (filename, content) pairs.  Filenames encode target + scenario.
_SEEDS: list[tuple[str, bytes]] = [
    # ----------------------------------------------------------------
    # Target 0: fuzz_validate_blob_path
    # ----------------------------------------------------------------
    ("t0_valid_raw", _T0 + b"raw/robot-01/2026-03-05/episode-001.mcap"),
    ("t0_valid_converted", _T0 + b"converted/pick-place/data/chunk-000.parquet"),
    ("t0_valid_reports", _T0 + b"reports/eval-run/2026-01-15/summary.json"),
    ("t0_valid_checkpoints", _T0 + b"checkpoints/policy-01/20260315_143022.pt"),
    ("t0_traversal", _T0 + b"../traversal/attack.mcap"),
    ("t0_uppercase", _T0 + b"INVALID/PATH"),
    ("t0_empty", _T0),
    # ----------------------------------------------------------------
    # Target 1: fuzz_get_validation_error
    # ----------------------------------------------------------------
    ("t1_valid", _T1 + b"raw/robot-01/2026-03-05/episode.mcap"),
    ("t1_uppercase", _T1 + b"RAW/ROBOT-01/2026/ep.mcap"),
    ("t1_spaces", _T1 + b"raw/robot 01/2026-03-05/ep.mcap"),
    ("t1_special_chars", _T1 + b"raw/robot\x00\x01\x02/episode.mcap"),
    # ----------------------------------------------------------------
    # Target 2: fuzz_extract_from_value
    # ----------------------------------------------------------------
    ("t2_float", _T2 + b"loss" + _double(3.14)),
    ("t2_int", _T2 + b"step\x00\x00\x00\x00\x00\x00\x00\x2a"),
    ("t2_string", _T2 + b"tag_not-a-number"),
    ("t2_none", _T2 + b"reward"),
    ("t2_nan", _T2 + b"metric" + _double(float("nan"))),
    ("t2_inf", _T2 + b"metric" + _double(float("inf"))),
    # ----------------------------------------------------------------
    # Target 3: fuzz_extract_from_tracking_data
    # ----------------------------------------------------------------
    ("t3_flat", _T3 + b"loss\x000.5reward\x001.0"),
    ("t3_nested", _T3 + b"train\x00loss\x000.1eval\x00acc\x000.95"),
    ("t3_empty", _T3),
    ("t3_deep_nesting", _T3 + b"a\x00b\x00c\x00d\x00e\x00f\x001.0"),
    # ----------------------------------------------------------------
    # Target 4: fuzz_sanitize_user_string
    # ----------------------------------------------------------------
    ("t4_clean", _T4 + b"hello-world_123"),
    ("t4_crlf", _T4 + b"hello\r\nworld"),
    ("t4_cr", _T4 + b"hello\rworld"),
    ("t4_lf", _T4 + b"hello\nworld"),
    ("t4_null_byte", _T4 + b"hello\x00world"),
    ("t4_unicode", _T4 + "café-über".encode()),  # cspell:ignore über
    ("t4_empty", _T4),
    ("t4_long", _T4 + b"a" * 512),
    # ----------------------------------------------------------------
    # Target 5: fuzz_sanitize_nested_value
    # ----------------------------------------------------------------
    ("t5_string", _T5 + b"hello\r\nworld"),
    ("t5_numeric", _T5 + _double(42.0)),
    ("t5_nested_crlf", _T5 + b"key\x00val\r\nue"),
    ("t5_deep", _T5 + b"outer\x00inner\x00leaf\r\n"),
    # ----------------------------------------------------------------
    # Target 6: fuzz_validate_safe_string
    # ----------------------------------------------------------------
    ("t6_valid_dataset_id", _T6 + b"robot-01.data_v2"),
    ("t6_valid_camera", _T6 + b"front_left.rgb"),
    ("t6_null_byte", _T6 + b"robot\x00evil"),
    ("t6_traversal", _T6 + b"../etc/passwd"),
    ("t6_special", _T6 + b"!@#$%^&*()"),
    ("t6_long", _T6 + b"a" * 256),
    # ----------------------------------------------------------------
    # Target 7: fuzz_dataset_id_to_blob_prefix
    # ----------------------------------------------------------------
    ("t7_double_dash", _T7 + b"group--dataset"),
    ("t7_multiple_sep", _T7 + b"a--b--c"),
    ("t7_no_separator", _T7 + b"simple-name"),
    ("t7_empty", _T7),
    ("t7_triple_dash", _T7 + b"a---b"),
    # ----------------------------------------------------------------
    # Target 8: fuzz_datetime_encoder
    # ----------------------------------------------------------------
    ("t8_normal", _T8 + struct.pack("<HBBBBBB", 2026, 3, 15, 14, 30, 22, 0)),  # cspell:ignore HBBBBBB
    ("t8_epoch", _T8 + struct.pack("<HBBBBBB", 1970, 1, 1, 0, 0, 0, 0)),
    ("t8_max_year", _T8 + struct.pack("<HBBBBBB", 9999, 12, 28, 23, 59, 59, 0)),
    ("t8_min_year", _T8 + struct.pack("<HBBBBBB", 1, 1, 1, 0, 0, 0, 0)),
]


def main() -> None:
    _CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    for name, content in _SEEDS:
        (_CORPUS_DIR / name).write_bytes(content)
    print(f"Generated {len(_SEEDS)} seed files in {_CORPUS_DIR}")


if __name__ == "__main__":
    main()
