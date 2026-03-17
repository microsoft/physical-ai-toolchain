/**
 * # SiL Module Outputs
 *
 * This file exports AKS and ML extension resources created by the SiL module.
 * Shared infrastructure outputs (networking, security, observability, etc.) are
 * provided by the platform module.
 */

// ============================================================
// AKS Networking Outputs
// ============================================================

output "aks_subnets" {
  description = "AKS subnets created by the module. Note: Pod subnets are not used with Azure CNI Overlay mode."
  value = {
    aks = {
      id   = azurerm_subnet.aks.id
      name = azurerm_subnet.aks.name
    }
  }
}

// ============================================================
// AKS Cluster Outputs
// ============================================================

output "aks_cluster" {
  description = "The AKS Cluster resource."
  value = {
    id                  = azurerm_kubernetes_cluster.main.id
    name                = azurerm_kubernetes_cluster.main.name
    fqdn                = azurerm_kubernetes_cluster.main.fqdn
    kubelet_identity    = azurerm_kubernetes_cluster.main.kubelet_identity[0]
    node_resource_group = azurerm_kubernetes_cluster.main.node_resource_group
  }
}

output "aks_oidc_issuer_url" {
  description = "The OIDC issuer URL for the AKS cluster."
  value       = azurerm_kubernetes_cluster.main.oidc_issuer_url
}

output "gpu_node_pool_subnets" {
  description = "GPU node pool subnets created by the module."
  value = {
    for key, subnet in azurerm_subnet.gpu_node_pool : key => {
      id   = subnet.id
      name = subnet.name
    }
  }
}

output "node_pools" {
  description = "GPU node pool configurations for OSMO pool and pod template generation"
  value = {
    for key, pool in var.node_pools : key => {
      vm_size     = pool.vm_size
      node_taints = pool.node_taints
      priority    = pool.priority
      node_labels = pool.node_labels
    }
  }
}
