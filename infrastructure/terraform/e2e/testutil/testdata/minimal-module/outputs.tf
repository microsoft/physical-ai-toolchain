// Minimal Terraform module fixture used by GetTerraformDeclaredOutputs and
// ValidateTerraformContract tests. Two declared outputs are sufficient to
// exercise terraform-docs JSON parsing without requiring init or providers.

terraform {
  required_version = ">= 1.9.8, < 2.0"
}

output "alpha" {
  value       = "alpha"
  description = "First fixture output."
}

output "beta" {
  value       = "beta"
  description = "Second fixture output."
}
