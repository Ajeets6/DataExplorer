module "platform" {
  source           = "../../modules/platform"
  project_id       = var.project_id
  region           = var.region
  environment      = "development"
  image            = var.image
  billing_account  = var.billing_account
  oidc_jwks_url    = var.oidc_jwks_url
  oidc_issuer      = var.oidc_issuer
  oidc_audience    = var.oidc_audience
  qdrant_url       = var.qdrant_url
  deploy_workloads = var.deploy_workloads
}
