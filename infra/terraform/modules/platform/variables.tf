variable "project_id" { type = string }
variable "region" { type = string }
variable "environment" { type = string }
variable "image" { type = string }
variable "billing_account" {
  type    = string
  default = null
}
variable "monthly_budget_aud" {
  type    = number
  default = 500
}
variable "api_min_instances" {
  type    = number
  default = 0
}
variable "api_max_instances" {
  type    = number
  default = 10
}
variable "oidc_jwks_url" { type = string }
variable "oidc_issuer" { type = string }
variable "oidc_audience" { type = string }
variable "model_provider" {
  type    = string
  default = "openai"
  validation {
    condition     = contains(["ollama", "openai", "anthropic", "router"], var.model_provider)
    error_message = "model_provider must be ollama, openai, anthropic, or router."
  }
}
variable "qdrant_url" { type = string }
variable "deploy_workloads" {
  type        = bool
  default     = false
  description = "Enable only after required Secret Manager versions have been seeded."
}

variable "login_url" {
  type = string
  description = "HTTPS organization gateway sign-in URL for both workspace UIs."
  validation {
    condition = can(regex("^https://[^/]+", var.login_url))
    error_message = "login_url must be an HTTPS organization sign-in URL."
  }
}
