#!/usr/bin/env bash
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
#
# Start the requested LeRobot workflow or an interactive shell.

set -euo pipefail

main() {
  exec "$@"
}

main "$@"
