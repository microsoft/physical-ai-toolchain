#!/usr/bin/env python3
"""Stage 1: run CARLA capture and write a portable capture bundle."""

from __future__ import annotations

from ..utils.carla import carla_capture


def main() -> None:
    carla_capture.main()


if __name__ == "__main__":
    main()
