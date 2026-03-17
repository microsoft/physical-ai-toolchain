/**
 * # SiL Module (Software-in-the-Loop)
 *
 * This module deploys AKS-specific infrastructure for robotics ML workloads including:
 * - AKS Cluster with GPU node pools
 * - Azure Machine Learning extension and compute targets
 * - Data Collection Rule associations for observability
 *
 * Note: Shared services (networking, DNS, security, observability, ACR, storage, ML workspace)
 * are created in the platform module and passed as dependencies.
 */

locals {
  // Naming convention components
  resource_name_suffix = "${var.resource_prefix}-${var.environment}-${var.instance}"

  // Private endpoint configuration
  pe_enabled = var.should_enable_private_endpoint
}
