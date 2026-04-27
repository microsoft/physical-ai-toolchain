#!/usr/bin/env bash
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
#
# Developer helper for running Terraform output contract tests.
# Contract tests validate IaC outputs without deployment (fast, $0 cost).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

verbose=""
[[ "${1:-}" == "-v" || "${1:-}" == "--verbose" ]] && verbose="-v"

command -v go >/dev/null 2>&1 || { echo "go not found; install Go 1.26+"; exit 1; }
command -v terraform-docs >/dev/null 2>&1 || {
	echo "terraform-docs not found; install via 'brew install terraform-docs' or"
	echo "from https://github.com/terraform-docs/terraform-docs/releases"
	exit 1
}

echo "go:             $(go version | awk '{print $3}')"
echo "terraform-docs: $(terraform-docs --version)"
echo

go test $verbose -run TestTerraformOutputsContract ./...
