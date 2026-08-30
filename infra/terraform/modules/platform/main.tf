locals {
  name = "dataexplorer-${var.environment}"
  services = toset([
    "artifactregistry.googleapis.com", "cloudbuild.googleapis.com",
    "run.googleapis.com", "secretmanager.googleapis.com", "sqladmin.googleapis.com",
    "redis.googleapis.com", "vpcaccess.googleapis.com", "cloudkms.googleapis.com",
    "monitoring.googleapis.com", "logging.googleapis.com", "servicenetworking.googleapis.com"
  ])
}

resource "google_project_service" "required" {
  for_each           = local.services
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_compute_network" "main" {
  name                    = local.name
  project                 = var.project_id
  auto_create_subnetworks = false
  depends_on              = [google_project_service.required]
}
resource "google_compute_subnetwork" "main" {
  name                     = local.name
  project                  = var.project_id
  region                   = var.region
  network                  = google_compute_network.main.id
  ip_cidr_range            = "10.20.0.0/20"
  private_ip_google_access = true
}
resource "google_vpc_access_connector" "main" {
  name          = substr(local.name, 0, 24)
  project       = var.project_id
  region        = var.region
  network       = google_compute_network.main.name
  ip_cidr_range = "10.21.0.0/28"
}

resource "google_artifact_registry_repository" "images" {
  project       = var.project_id
  location      = var.region
  repository_id = "dataexplorer"
  format        = "DOCKER"
  depends_on    = [google_project_service.required]
}
resource "google_storage_bucket" "documents" {
  name                        = "${var.project_id}-${local.name}-documents"
  project                     = var.project_id
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  versioning { enabled = true }
  lifecycle_rule {
    condition { age = 365 }
    action {
      type          = "SetStorageClass"
      storage_class = "ARCHIVE"
    }
  }
}

resource "google_service_account" "api" {
  project      = var.project_id
  account_id   = substr("${local.name}-api", 0, 30)
  display_name = "Data Explorer API"
}
resource "google_service_account" "worker" {
  project      = var.project_id
  account_id   = substr("${local.name}-worker", 0, 30)
  display_name = "Data Explorer worker"
}
resource "google_service_account" "ui" {
  project      = var.project_id
  account_id   = substr("${local.name}-ui", 0, 30)
  display_name = "Data Explorer UI"
}
resource "google_service_account" "admin_ui" {
  project      = var.project_id
  account_id   = substr("${local.name}-admin-ui", 0, 30)
  display_name = "Data Explorer observability UI"
}
resource "google_project_iam_member" "api_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.api.email}"
}
resource "google_project_iam_member" "ui_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.ui.email}"
}
resource "google_project_iam_member" "admin_ui_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.admin_ui.email}"
}
resource "google_storage_bucket_iam_member" "worker_documents" {
  bucket = google_storage_bucket.documents.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.worker.email}"
}
resource "google_storage_bucket_iam_member" "api_artifacts" {
  bucket = google_storage_bucket.documents.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_service_account.api.email}"
}

resource "google_kms_key_ring" "main" {
  name     = local.name
  location = var.region
  project  = var.project_id
}
resource "google_kms_crypto_key" "data" {
  name            = "data"
  key_ring        = google_kms_key_ring.main.id
  rotation_period = "7776000s"
}
resource "google_kms_crypto_key_iam_member" "api_encrypt" {
  crypto_key_id = google_kms_crypto_key.data.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${google_service_account.api.email}"
}
resource "google_secret_manager_secret" "database_dsn" {
  secret_id = "${local.name}-database-dsn"
  project   = var.project_id
  replication {
    auto {}
  }
  depends_on = [google_project_service.required]
}
resource "google_secret_manager_secret" "qdrant" {
  secret_id = "${local.name}-qdrant-api-key"
  project   = var.project_id
  replication {
    auto {}
  }
}
resource "google_secret_manager_secret" "openai" {
  secret_id = "${local.name}-openai-api-key"
  project   = var.project_id
  replication {
    auto {}
  }
}
resource "google_secret_manager_secret_iam_member" "api_database" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.database_dsn.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.api.email}"
}
resource "google_secret_manager_secret_iam_member" "worker_database" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.database_dsn.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.worker.email}"
}
resource "google_secret_manager_secret_iam_member" "api_openai" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.openai.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.api.email}"
}
resource "google_secret_manager_secret_iam_member" "api_qdrant" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.qdrant.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.api.email}"
}

resource "google_compute_global_address" "private_services" {
  name          = "${local.name}-private-services"
  project       = var.project_id
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.main.id
}
resource "google_service_networking_connection" "private_services" {
  network                 = google_compute_network.main.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_services.name]
}

resource "google_sql_database_instance" "postgres" {
  name                = local.name
  project             = var.project_id
  region              = var.region
  database_version    = "POSTGRES_17"
  deletion_protection = true
  settings {
    tier              = "db-custom-2-7680"
    availability_type = var.environment == "production" ? "REGIONAL" : "ZONAL"
    disk_autoresize   = true
    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
    }
    ip_configuration {
      ipv4_enabled    = false
      private_network = google_compute_network.main.id
    }
    database_flags {
      name  = "cloudsql.iam_authentication"
      value = "on"
    }
  }
  depends_on = [google_project_service.required, google_service_networking_connection.private_services]
}
resource "google_redis_instance" "cache" {
  name                    = local.name
  project                 = var.project_id
  region                  = var.region
  tier                    = var.environment == "production" ? "STANDARD_HA" : "BASIC"
  memory_size_gb          = 1
  redis_version           = "REDIS_7_2"
  authorized_network      = google_compute_network.main.id
  transit_encryption_mode = "SERVER_AUTHENTICATION"
  depends_on              = [google_project_service.required]
}
resource "google_secret_manager_secret" "redis_ca" {
  secret_id = "${local.name}-redis-ca"
  project   = var.project_id
  replication {
    auto {}
  }
}
resource "google_secret_manager_secret_version" "redis_ca" {
  secret      = google_secret_manager_secret.redis_ca.id
  secret_data = google_redis_instance.cache.server_ca_certs[0].cert
}
resource "google_secret_manager_secret_iam_member" "api_redis_ca" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.redis_ca.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.api.email}"
}

resource "google_cloud_run_v2_service" "api" {
  count                = var.deploy_workloads ? 1 : 0
  name                 = "dataexplorer-api"
  project              = var.project_id
  location             = var.region
  ingress              = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"
  invoker_iam_disabled = true
  deletion_protection  = true
  template {
    service_account = google_service_account.api.email
    timeout         = "120s"
    scaling {
      min_instance_count = var.api_min_instances
      max_instance_count = var.api_max_instances
    }
    vpc_access {
      connector = google_vpc_access_connector.main.id
      egress    = "PRIVATE_RANGES_ONLY"
    }
    volumes {
      name = "redis-ca"
      secret {
        secret = google_secret_manager_secret.redis_ca.secret_id
        items {
          version = "latest"
          path    = "server-ca.pem"
          mode    = 0444
        }
      }
    }
    containers {
      image = var.image
      resources {
        limits   = { cpu = "2", memory = "2Gi" }
        cpu_idle = true
      }
      ports { container_port = 8080 }
      volume_mounts {
        name       = "redis-ca"
        mount_path = "/secrets/redis"
      }
      env {
        name  = "DATAEXPLORER_ENVIRONMENT"
        value = var.environment
      }
      env {
        name  = "DATAEXPLORER_AUTH_MODE"
        value = "jwt"
      }
      env {
        name  = "DATAEXPLORER_OIDC_JWKS_URL"
        value = var.oidc_jwks_url
      }
      env {
        name  = "DATAEXPLORER_OIDC_ISSUER"
        value = var.oidc_issuer
      }
      env {
        name  = "DATAEXPLORER_OIDC_AUDIENCE"
        value = var.oidc_audience
      }
      env {
        name  = "DATAEXPLORER_MODEL_PROVIDER"
        value = var.model_provider
      }
      env {
        name  = "DATAEXPLORER_PERSISTENCE_MODE"
        value = "postgres"
      }
      env {
        name  = "DATAEXPLORER_POLICY_BACKEND"
        value = "redis"
      }
      env {
        name  = "DATAEXPLORER_REDIS_URL"
        value = "rediss://${google_redis_instance.cache.host}:${google_redis_instance.cache.port}/0?ssl_cert_reqs=required&ssl_ca_certs=/secrets/redis/server-ca.pem"
      }
      env {
        name  = "DATAEXPLORER_VECTOR_BACKEND"
        value = "qdrant"
      }
      env {
        name  = "DATAEXPLORER_QDRANT_URL"
        value = var.qdrant_url
      }
      env {
        name = "DATAEXPLORER_QDRANT_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.qdrant.secret_id
            version = "latest"
          }
        }
      }
      env {
        name  = "DATAEXPLORER_DEPLOYMENT_REGION"
        value = var.region
      }
      env {
        name  = "DATAEXPLORER_ARTIFACT_GCS_BUCKET"
        value = google_storage_bucket.documents.name
      }
      env {
        name  = "DATAEXPLORER_ARTIFACT_GCS_KMS_KEY"
        value = google_kms_crypto_key.data.id
      }
      env {
        name = "DATAEXPLORER_DATABASE_DSN"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.database_dsn.secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "DATAEXPLORER_OPENAI_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.openai.secret_id
            version = "latest"
          }
        }
      }
      startup_probe {
        http_get {
          path = "/health/live"
          port = 8080
        }
        failure_threshold = 12
        period_seconds    = 5
      }
      liveness_probe {
        http_get {
          path = "/health/live"
          port = 8080
        }
        period_seconds = 30
      }
    }
  }
  depends_on = [google_sql_database_instance.postgres, google_redis_instance.cache]
}

resource "google_cloud_run_v2_service" "ui" {
  count               = var.deploy_workloads ? 1 : 0
  name                = "dataexplorer-ui"
  project             = var.project_id
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = true
  template {
    service_account = google_service_account.ui.email
    timeout         = "300s"
    scaling {
      min_instance_count = var.api_min_instances
      max_instance_count = var.api_max_instances
    }
    containers {
      image   = var.image
      command = ["streamlit"]
      args = [
        "run", "src/dataexplorer/ui.py", "--server.headless=true",
        "--server.address=0.0.0.0", "--server.port=8080",
        "--browser.gatherUsageStats=false"
      ]
      resources {
        limits   = { cpu = "1", memory = "1Gi" }
        cpu_idle = true
      }
      ports { container_port = 8080 }
      env {
        name  = "DATAEXPLORER_API_URL"
        value = google_cloud_run_v2_service.api[0].uri
      }
      startup_probe {
        http_get {
          path = "/_stcore/health"
          port = 8080
        }
        failure_threshold = 12
        period_seconds    = 5
      }
      liveness_probe {
        http_get {
          path = "/_stcore/health"
          port = 8080
        }
        period_seconds = 30
      }
    }
  }
  depends_on = [google_cloud_run_v2_service.api]
}

resource "google_cloud_run_v2_service" "admin_ui" {
  count               = var.deploy_workloads ? 1 : 0
  name                = "dataexplorer-observability"
  project             = var.project_id
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"
  deletion_protection = true
  template {
    service_account = google_service_account.admin_ui.email
    timeout         = "300s"
    scaling {
      min_instance_count = 0
      max_instance_count = var.api_max_instances
    }
    containers {
      image   = var.image
      command = ["streamlit"]
      args = [
        "run", "src/dataexplorer/ui_admin.py", "--server.headless=true",
        "--server.address=0.0.0.0", "--server.port=8080",
        "--browser.gatherUsageStats=false"
      ]
      resources {
        limits   = { cpu = "1", memory = "1Gi" }
        cpu_idle = true
      }
      ports { container_port = 8080 }
      env {
        name  = "DATAEXPLORER_API_URL"
        value = google_cloud_run_v2_service.api[0].uri
      }
      startup_probe {
        http_get {
          path = "/_stcore/health"
          port = 8080
        }
        failure_threshold = 12
        period_seconds    = 5
      }
      liveness_probe {
        http_get {
          path = "/_stcore/health"
          port = 8080
        }
        period_seconds = 30
      }
    }
  }
  depends_on = [google_cloud_run_v2_service.api]
}

resource "google_cloud_run_v2_job" "worker" {
  count               = var.deploy_workloads ? 1 : 0
  name                = "dataexplorer-worker"
  project             = var.project_id
  location            = var.region
  deletion_protection = true
  template {
    template {
      service_account = google_service_account.worker.email
      timeout         = "3600s"
      vpc_access {
        connector = google_vpc_access_connector.main.id
        egress    = "PRIVATE_RANGES_ONLY"
      }
      containers {
        image   = var.image
        command = ["python", "-m", "dataexplorer.worker"]
        env {
          name  = "DATAEXPLORER_WORKER_TASK"
          value = "migrate"
        }
        env {
          name = "DATAEXPLORER_DATABASE_DSN"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.database_dsn.secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }
}

resource "google_monitoring_alert_policy" "api_errors" {
  project      = var.project_id
  display_name = "${local.name} API 5xx errors"
  combiner     = "OR"
  conditions {
    display_name = "Cloud Run 5xx count"
    condition_threshold {
      filter          = "resource.type=\"cloud_run_revision\" AND metric.type=\"run.googleapis.com/request_count\" AND metric.label.response_code_class=\"5xx\""
      comparison      = "COMPARISON_GT"
      threshold_value = 5
      duration        = "300s"
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }
}
resource "google_billing_budget" "monthly" {
  count           = var.billing_account == null ? 0 : 1
  billing_account = var.billing_account
  display_name    = "${local.name} monthly budget"
  amount {
    specified_amount {
      currency_code = "AUD"
      units         = tostring(var.monthly_budget_aud)
    }
  }
  threshold_rules { threshold_percent = 0.5 }
  threshold_rules { threshold_percent = 0.9 }
  threshold_rules { threshold_percent = 1.0 }
}
