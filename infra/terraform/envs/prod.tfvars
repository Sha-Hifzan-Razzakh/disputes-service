env               = "prod"
region            = "me-central-1"
db_instance_class = "db.r7g.large"
db_multi_az       = true
api_desired_count = 3
# certificate_arn and image come from the pipeline: -var certificate_arn=... -var image=...@sha256:...
app_settings = {
  APP_DB_POOL_SIZE        = "10"
  APP_DB_MAX_OVERFLOW     = "4"
  APP_MAX_CONCURRENT_RUNS = "10"
  # APP_RESOLUTION_TENANTS unset: no tenant can start resolutions until the pilot (docs/rollout.md)
}
