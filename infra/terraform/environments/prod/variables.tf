variable "project_id" { type = string }
variable "region" {
  type    = string
  default = "australia-southeast1"
}
variable "image" { type = string }
variable "billing_account" { type = string }
variable "oidc_jwks_url" { type = string }
variable "oidc_issuer" { type = string }
variable "oidc_audience" { type = string }
variable "qdrant_url" { type = string }
variable "deploy_workloads" {
  type    = bool
  default = false
}
