/**
 * # SiL Module (Software-in-the-Loop)
 *
 * Deploys AKS-specific infrastructure for robotics ML workloads with GPU node pools, AzureML integration, and observability.
 *
 * Resources deployed:
 *
 * - AKS Cluster with GPU node pools
 * - Azure Machine Learning extension and compute targets
 * - Data Collection Rule associations for observability
 *
 * Shared services (networking, DNS, security, observability, ACR, storage, ML workspace)
 * are created in the platform module and passed as dependencies.
 */

locals {
  // Naming convention components
  resource_name_suffix = "${var.resource_prefix}-${var.environment}-${var.instance}"

  // Private endpoint configuration
  pe_enabled = var.should_enable_private_endpoint

  // Node pools that own a subnet; entries with subnet_pool_key use their owner's subnet
  subnet_owner_node_pools = { for key, pool in var.node_pools : key => pool if pool.subnet_pool_key == null }
}
