variable "project_id" { type = string }
variable "region" {
  type    = string
  default = "australia-southeast1"
}
variable "image" { type = string }
variable "billing_account" {
  type    = string
  default = null
}
variable "oidc_jwks_url" { type = string }
variable "oidc_issuer" { type = string }
variable "oidc_audience" { type = string }
variable "qdrant_url" { type = string }
variable "deploy_workloads" {
  type    = bool
  default = false
}

variable "login_url" {
  type = string
  description = "HTTPS organization gateway sign-in URL for both workspace UIs."
  validation {
    condition = can(regex("^https://[^/]+", var.login_url))
    error_message = "login_url must be an HTTPS organization sign-in URL."
  }
}
