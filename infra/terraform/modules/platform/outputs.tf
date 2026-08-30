output "api_uri" { value = try(google_cloud_run_v2_service.api[0].uri, null) }
output "ui_uri" { value = try(google_cloud_run_v2_service.ui[0].uri, null) }
output "observability_ui_uri" { value = try(google_cloud_run_v2_service.admin_ui[0].uri, null) }
output "artifact_repository" { value = google_artifact_registry_repository.images.name }
output "documents_bucket" { value = google_storage_bucket.documents.name }
output "database_connection_name" { value = google_sql_database_instance.postgres.connection_name }
output "redis_host" {
  value     = google_redis_instance.cache.host
  sensitive = true
}
output "database_dsn_secret" { value = google_secret_manager_secret.database_dsn.id }
output "qdrant_secret" { value = google_secret_manager_secret.qdrant.id }
output "openai_secret" { value = google_secret_manager_secret.openai.id }
