/**
 * # Dependency Variables
 * Typed object dependencies discovered from other modules.
 */

variable "acr" {
  description = "Optional Azure Container Registry to grant AcrPush on. Set to null to skip ACR role assignment."
  type = object({
    id           = string
    name         = string
    login_server = string
  })
  default = null
}
