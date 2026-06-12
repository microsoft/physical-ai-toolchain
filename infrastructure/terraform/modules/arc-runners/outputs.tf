/**
 * # ARC Runners Outputs
 */

output "namespace" {
  description = "Kubernetes namespace hosting the ARC controller and runner scale set."
  value       = kubernetes_namespace.arc_runners.metadata[0].name
}

output "service_account_name" {
  description = "Service account bound to the runner scale set with workload-identity federation."
  value       = kubernetes_service_account.runner.metadata[0].name
}

output "controller_release_name" {
  description = "Helm release name of the gha-runner-scale-set-controller chart."
  value       = helm_release.arc_controller.name
}

output "runner_set_release_name" {
  description = "Helm release name of the gha-runner-scale-set chart."
  value       = helm_release.runner_set.name
}

output "uami_client_id" {
  description = "Client ID of the runner workload-identity managed identity used for ACR push and Key Vault access."
  value       = azurerm_user_assigned_identity.runner.client_id
}
