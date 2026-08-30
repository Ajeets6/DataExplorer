module "platform" {
  source             = "../../modules/platform"
  project_id         = var.project_id
  region             = var.region
  environment        = "production"
  image              = var.image
  billing_account    = var.billing_account
  monthly_budget_aud = 5000
  api_min_instances  = 2
  api_max_instances  = 50
  oidc_jwks_url      = var.oidc_jwks_url
  oidc_issuer        = var.oidc_issuer
  oidc_audience      = var.oidc_audience
  qdrant_url         = var.qdrant_url
  deploy_workloads   = var.deploy_workloads
}
