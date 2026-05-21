// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

package e2e

import (
	"os/exec"
	"testing"

	"github.com/microsoft/physical-ai-toolchain/infrastructure/terraform/e2e/testutil"
)

// TestTerraformOutputsContract validates root module output declarations
// against InfraOutputs. Runs in < 1s, no Azure auth, no terraform init.
//
// Requirements:
//   - terraform-docs must be installed and on PATH
//   - Valid Terraform configuration at ../
func TestTerraformOutputsContract(t *testing.T) {
	if _, err := exec.LookPath("terraform-docs"); err != nil {
		t.Skip("terraform-docs not installed; skipping contract test")
	}
	testutil.ValidateTerraformContract(t, "..", InfraOutputs{}.RequiredOutputKeys())
}
