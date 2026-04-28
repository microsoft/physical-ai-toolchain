// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

package testutil

import (
	"os/exec"
	"testing"

	"github.com/stretchr/testify/require"
)

type sampleOutputs struct {
	First   string `output:"first"`
	Second  int    `output:"second"`
	Skipped string
	Third   any `output:"third"`
}

func TestGetOutputKeysFromStruct(t *testing.T) {
	t.Run("value receiver", func(t *testing.T) {
		keys := GetOutputKeysFromStruct(sampleOutputs{})
		require.Equal(t, []string{"first", "second", "third"}, keys)
	})

	t.Run("pointer receiver", func(t *testing.T) {
		keys := GetOutputKeysFromStruct(&sampleOutputs{})
		require.Equal(t, []string{"first", "second", "third"}, keys)
	})

	t.Run("empty struct", func(t *testing.T) {
		keys := GetOutputKeysFromStruct(struct{}{})
		require.Empty(t, keys)
	})

	t.Run("struct with no tags", func(t *testing.T) {
		type untagged struct {
			A string
			B int
		}
		keys := GetOutputKeysFromStruct(untagged{})
		require.Empty(t, keys)
	})
}

func TestValidateOutputContract(t *testing.T) {
	t.Run("all required declared", func(t *testing.T) {
		declared := []string{"a", "b", "c", "d"}
		required := []string{"a", "c"}
		ValidateOutputContract(t, declared, required)
	})

	t.Run("extra declared outputs are allowed", func(t *testing.T) {
		declared := []string{"a", "b", "c", "extra"}
		required := []string{"a", "b", "c"}
		ValidateOutputContract(t, declared, required)
	})

	t.Run("exact match", func(t *testing.T) {
		declared := []string{"a", "b", "c"}
		required := []string{"a", "b", "c"}
		ValidateOutputContract(t, declared, required)
	})

	t.Run("empty required set", func(t *testing.T) {
		ValidateOutputContract(t, []string{"a", "b"}, nil)
	})
}

func TestFindMissingOutputs(t *testing.T) {
	t.Run("returns missing required outputs", func(t *testing.T) {
		declared := []string{"a", "c"}
		required := []string{"a", "b", "c", "d"}
		missing := findMissingOutputs(declared, required)
		require.Equal(t, []string{"b", "d"}, missing)
	})

	t.Run("returns empty when all required declared", func(t *testing.T) {
		declared := []string{"a", "b", "c"}
		required := []string{"a", "b"}
		require.Empty(t, findMissingOutputs(declared, required))
	})

	t.Run("returns all required when declared is empty", func(t *testing.T) {
		missing := findMissingOutputs(nil, []string{"a", "b"})
		require.Equal(t, []string{"a", "b"}, missing)
	})
}

func TestGetTerraformDeclaredOutputs(t *testing.T) {
	if _, err := exec.LookPath("terraform-docs"); err != nil {
		t.Skip("terraform-docs not installed; skipping fixture-based test")
	}

	declared := GetTerraformDeclaredOutputs(t, "testdata/minimal-module")
	require.ElementsMatch(t, []string{"alpha", "beta"}, declared)
}

func TestValidateTerraformContract(t *testing.T) {
	if _, err := exec.LookPath("terraform-docs"); err != nil {
		t.Skip("terraform-docs not installed; skipping fixture-based test")
	}

	t.Run("required subset of declared", func(t *testing.T) {
		ValidateTerraformContract(t, "testdata/minimal-module", []string{"alpha"})
	})

	t.Run("required equals declared", func(t *testing.T) {
		ValidateTerraformContract(t, "testdata/minimal-module", []string{"alpha", "beta"})
	})
}
