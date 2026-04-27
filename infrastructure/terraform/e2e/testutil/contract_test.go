// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

package testutil

import (
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

	t.Run("empty required set", func(t *testing.T) {
		ValidateOutputContract(t, []string{"a", "b"}, nil)
	})
}
