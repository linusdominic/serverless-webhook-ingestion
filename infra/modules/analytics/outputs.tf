output "glue_database_name" {
  description = "Glue database holding the curated tables."
  value       = aws_glue_catalog_database.curated.name
}

output "landing_table_name" {
  description = "Landing table (JSON, may contain duplicates)."
  value       = aws_glue_catalog_table.landing.name
}

output "compacted_table_name" {
  description = "Compacted table (Parquet, one row per event)."
  value       = aws_glue_catalog_table.compacted.name
}

output "athena_workgroup_name" {
  description = "Athena workgroup for curated queries."
  value       = aws_athena_workgroup.curated.name
}

output "state_machine_arn" {
  description = "Curation state machine ARN."
  value       = aws_sfn_state_machine.curation.arn
}

output "state_machine_name" {
  description = "Curation state machine name."
  value       = aws_sfn_state_machine.curation.name
}

output "schedule_name" {
  description = "EventBridge Scheduler schedule that starts the pipeline."
  value       = aws_scheduler_schedule.curation.name
}

output "function_names" {
  description = "Pipeline function names by step."
  value = {
    compactor = module.compactor.function_name
    catalog   = module.catalog.function_name
    quality   = module.quality.function_name
    reconcile = module.reconcile.function_name
  }
}

output "log_group_names" {
  description = "Pipeline function log groups by step."
  value = {
    compactor = module.compactor.log_group_name
    catalog   = module.catalog.log_group_name
    quality   = module.quality.log_group_name
    reconcile = module.reconcile.log_group_name
  }
}
