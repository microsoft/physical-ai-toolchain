// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

// Package e2e defines the output contract between the root Terraform module and
// end-to-end tests. Every field must have an `output:"<name>"` tag whose value
// matches an output declared in ../outputs.tf.
package e2e

import "github.com/microsoft/physical-ai-toolchain/infrastructure/terraform/e2e/testutil"

// InfraOutputs mirrors the root module's output declarations. Adding a new
// output to outputs.tf requires adding a corresponding field here; contract
// tests will fail otherwise.
type InfraOutputs struct {
	ResourceGroup              map[string]any `output:"resource_group"`
	KeyVault                   map[string]any `output:"key_vault"`
	KeyVaultName               any            `output:"key_vault_name"`
	AksCluster                 map[string]any `output:"aks_cluster"`
	AksOidcIssuerURL           any            `output:"aks_oidc_issuer_url"`
	GpuNodePoolSubnets         any            `output:"gpu_node_pool_subnets"`
	NodePools                  any            `output:"node_pools"`
	AzuremlWorkspace           map[string]any `output:"azureml_workspace"`
	MlWorkloadIdentity         map[string]any `output:"ml_workload_identity"`
	PostgresqlConnectionInfo   map[string]any `output:"postgresql_connection_info"`
	ManagedRedisConnectionInfo map[string]any `output:"managed_redis_connection_info"`
	VirtualNetwork             map[string]any `output:"virtual_network"`
	Subnets                    any            `output:"subnets"`
	VMSubnet                   any            `output:"vm_subnet"`
	NetworkSecurityGroup       map[string]any `output:"network_security_group"`
	PrivateDNSResolver         any            `output:"private_dns_resolver"`
	DNSServerIP                any            `output:"dns_server_ip"`
	ContainerRegistry          map[string]any `output:"container_registry"`
	StorageAccount             map[string]any `output:"storage_account"`
	DataLakeStorageAccount     any            `output:"data_lake_storage_account"`
	AmlComputeCluster          any            `output:"aml_compute_cluster"`
	LogAnalyticsWorkspace      map[string]any `output:"log_analytics_workspace"`
	ApplicationInsights        map[string]any `output:"application_insights"`
	Grafana                    map[string]any `output:"grafana"`
	PostgreSQL                 any            `output:"postgresql"`
	Redis                      any            `output:"redis"`
	OsmoWorkloadIdentity       map[string]any `output:"osmo_workload_identity"`
}

// RequiredOutputKeys returns every `output` tag defined on InfraOutputs. Used
// by contract tests to validate root module output declarations.
func (InfraOutputs) RequiredOutputKeys() []string {
	return testutil.GetOutputKeysFromStruct(InfraOutputs{})
}
