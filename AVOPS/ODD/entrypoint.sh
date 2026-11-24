#!/bin/bash
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
set -e

# If no arguments or first argument is a flag (starts with -), pass to run_odd_pipeline.py
if [ "$#" -eq 0 ] || [ "${1:0:1}" = "-" ]; then
    exec python3 /app/run_odd_pipeline.py "$@"
else
    # Allow running custom command
    exec "$@"
fi
