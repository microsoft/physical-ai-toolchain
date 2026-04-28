// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

// Package testutil provides reusable testing utilities for Terraform output
// contract validation.
package testutil

import (
	"encoding/json"
	"os/exec"
	"reflect"
	"testing"

	"github.com/stretchr/testify/require"
)

// GetTerraformDeclaredOutputs extracts declared output names from Terraform
// configuration using `terraform-docs json`. No init/plan required, no Azure
// credentials needed.
func GetTerraformDeclaredOutputs(t *testing.T, terraformDir string) []string {
	t.Helper()

	cmd := exec.Command("terraform-docs", "json", terraformDir)
	output, err := cmd.CombinedOutput()
	require.NoError(t, err, "terraform-docs failed: %s\n%s", err, string(output))

	var parsed struct {
		Outputs []struct {
			Name string `json:"name"`
		} `json:"outputs"`
	}
	require.NoError(t, json.Unmarshal(output, &parsed),
		"failed to parse terraform-docs JSON: %s", string(output))

	keys := make([]string, 0, len(parsed.Outputs))
	for _, o := range parsed.Outputs {
		keys = append(keys, o.Name)
	}
	return keys
}

// ValidateOutputContract fails the test if any required output is missing from
// the declared set.
func ValidateOutputContract(t *testing.T, declared, required []string) {
	t.Helper()

	t.Logf("Declared outputs: %v", declared)
	t.Logf("Required outputs: %v", required)

	missing := findMissingOutputs(declared, required)

	if len(missing) > 0 {
		t.Errorf("Missing %d required outputs: %v", len(missing), missing)
		t.Errorf("Declare these in infrastructure/terraform/outputs.tf or remove from InfraOutputs struct")
	}
	require.Empty(t, missing, "output contract validation failed")
}

// findMissingOutputs returns the required keys that are absent from the
// declared set. Pure helper exposed for failure-path testing.
func findMissingOutputs(declared, required []string) []string {
	declaredSet := make(map[string]struct{}, len(declared))
	for _, d := range declared {
		declaredSet[d] = struct{}{}
	}

	var missing []string
	for _, r := range required {
		if _, ok := declaredSet[r]; !ok {
			missing = append(missing, r)
		}
	}
	return missing
}

// ValidateTerraformContract is the convenience wrapper root modules should
// call from their contract test.
func ValidateTerraformContract(t *testing.T, terraformDir string, required []string) {
	t.Helper()
	ValidateOutputContract(t, GetTerraformDeclaredOutputs(t, terraformDir), required)
}

// GetOutputKeysFromStruct extracts all `output:"..."` struct tag values using
// reflection. Accepts either a struct value or a pointer to a struct.
func GetOutputKeysFromStruct(v any) []string {
	rv := reflect.ValueOf(v)
	if rv.Kind() == reflect.Ptr {
		rv = rv.Elem()
	}
	typ := rv.Type()

	keys := make([]string, 0, typ.NumField())
	for i := 0; i < typ.NumField(); i++ {
		if tag := typ.Field(i).Tag.Get("output"); tag != "" {
			keys = append(keys, tag)
		}
	}
	return keys
}
