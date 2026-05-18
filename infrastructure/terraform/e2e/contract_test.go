// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

package e2e

import (
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
	testutil.ValidateTerraformContract(t, "..", InfraOutputs{}.RequiredOutputKeys())
}
