/**
 * # Role Assignments
 *
 * This file consolidates all role assignments for the SiL module including:
 * - AKS identity Private DNS Zone Contributor for custom DNS zone management
 * - AKS kubelet identity AcrPull role for container registry access
 * - AKS Cluster Admin role for current user (optional)
 */

// ============================================================
// AKS Cluster Admin Role Assignments
// ============================================================

// Grant current user Azure Kubernetes Service Cluster Admin Role
// This role allows managing the AKS resource in Azure (e.g., scaling, upgrades)
resource "azurerm_role_assignment" "aks_cluster_admin" {
  count = var.should_assign_cluster_admin ? 1 : 0

  scope                = azurerm_kubernetes_cluster.main.id
  role_definition_name = "Azure Kubernetes Service Cluster Admin Role"
  principal_id         = var.current_user_oid
}

// Grant current user Azure Kubernetes Service RBAC Cluster Admin Role
// This role is required when azure_rbac_enabled=true to access Kubernetes resources via kubectl
// Without this role, users cannot run kubectl commands even with az aks get-credentials
resource "azurerm_role_assignment" "aks_rbac_cluster_admin" {
  count = var.should_assign_cluster_admin ? 1 : 0

  scope                = azurerm_kubernetes_cluster.main.id
  role_definition_name = "Azure Kubernetes Service RBAC Cluster Admin"
  principal_id         = var.current_user_oid
}

// ============================================================
// Private DNS Zone Role Assignments
// ============================================================

// Grant AKS identity Private DNS Zone Contributor role for custom DNS zone management
// This must be created BEFORE the AKS cluster so the identity can manage DNS records
resource "azurerm_role_assignment" "aks_dns_zone_contributor" {
  count = var.aks_config.is_private_cluster && local.pe_enabled ? 1 : 0

  scope                            = var.private_dns_zones["aks"].id
  role_definition_name             = "Private DNS Zone Contributor"
  principal_id                     = azurerm_user_assigned_identity.aks.principal_id
  skip_service_principal_aad_check = true
}

// ============================================================
// Container Registry Role Assignments
// ============================================================

// Grant AKS kubelet identity AcrPull role
resource "azurerm_role_assignment" "aks_acr_pull" {
  scope                = var.container_registry.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_kubernetes_cluster.main.kubelet_identity[0].object_id
}

// ============================================================
// Network Role Assignments for Load Balancer
// ============================================================

// Grant AKS control plane identity Network Contributor on resource group
// Required for creating load balancers and managing network configurations in custom VNETs
resource "azurerm_role_assignment" "aks_control_plane_network_contributor" {
  scope                            = var.resource_group.id
  role_definition_name             = "Network Contributor"
  principal_id                     = azurerm_user_assigned_identity.aks.principal_id
  skip_service_principal_aad_check = true
}

// Grant kubelet identity Network Contributor on resource group
// Required for kubelet to join VMs to load balancer backend pools
resource "azurerm_role_assignment" "aks_kubelet_network_contributor" {
  scope                            = var.resource_group.id
  role_definition_name             = "Network Contributor"
  principal_id                     = azurerm_kubernetes_cluster.main.kubelet_identity[0].object_id
  skip_service_principal_aad_check = true
}
