output "s3_bucket_arn" {
  description = "ARN of the document storage bucket"
  value       = aws_s3_bucket.documents.arn
}

output "s3_bucket_name" {
  description = "Name of the document storage bucket"
  value       = aws_s3_bucket.documents.id
}

output "delta_table_arn" {
  description = "ARN of the delta tokens DynamoDB table"
  value       = aws_dynamodb_table.delta_tokens.arn
}

output "registry_table_arn" {
  description = "ARN of the document registry DynamoDB table"
  value       = aws_dynamodb_table.document_registry.arn
}

output "permission_mappings_table_arn" {
  description = "ARN of the permission mappings DynamoDB table"
  value       = aws_dynamodb_table.permission_mappings.arn
}

output "user_group_cache_table_arn" {
  description = "ARN of the user-group cache DynamoDB table"
  value       = aws_dynamodb_table.user_group_cache.arn
}

output "textract_sns_topic_arn" {
  description = "ARN of the SNS topic for Textract notifications"
  value       = aws_sns_topic.textract_notifications.arn
}

output "quarantine_sns_topic_arn" {
  description = "ARN of the quarantine alerts SNS topic"
  value       = aws_sns_topic.quarantine_alerts.arn
}

output "textract_service_role_arn" {
  description = "ARN of the IAM role Textract uses to publish to SNS"
  value       = aws_iam_role.textract_service.arn
}

output "bulk_ec2_role_arn" {
  description = "ARN of the EC2 role for bulk ingestion"
  value       = aws_iam_role.bulk_ec2.arn
}

output "bulk_ec2_instance_profile_name" {
  description = "Name of the EC2 instance profile for bulk ingestion"
  value       = aws_iam_instance_profile.bulk_ec2.name
}

output "daily_sync_lambda_role_arn" {
  description = "ARN of the daily sync Lambda execution role"
  value       = aws_iam_role.daily_sync_lambda.arn
}

output "textract_trigger_lambda_role_arn" {
  description = "ARN of the textract trigger Lambda execution role"
  value       = aws_iam_role.textract_trigger_lambda.arn
}

output "textract_complete_lambda_role_arn" {
  description = "ARN of the textract complete Lambda execution role"
  value       = aws_iam_role.textract_complete_lambda.arn
}

output "daily_sync_lambda_arn" {
  description = "ARN of the daily sync Lambda"
  value       = aws_lambda_function.daily_sync.arn
}

output "textract_trigger_lambda_arn" {
  description = "ARN of the Textract trigger Lambda"
  value       = aws_lambda_function.textract_trigger.arn
}

output "textract_complete_lambda_arn" {
  description = "ARN of the Textract complete Lambda"
  value       = aws_lambda_function.textract_complete.arn
}

output "lambda_layer_arn" {
  description = "ARN of the shared dependencies Lambda layer"
  value       = aws_lambda_layer_version.shared_deps.arn
}

# --- Bulk EC2 (conditional) ---

output "bulk_instance_id" {
  description = "Instance ID of the bulk loader EC2 (empty if disabled)"
  value       = var.enable_bulk_instance ? aws_instance.bulk_loader[0].id : ""
}

output "bulk_instance_public_ip" {
  description = "Public IP of the bulk loader EC2 (empty if disabled)"
  value       = var.enable_bulk_instance ? aws_instance.bulk_loader[0].public_ip : ""
}

# --- Monitoring ---

output "alerts_sns_topic_arn" {
  description = "ARN of the SNS topic for pipeline alerts"
  value       = aws_sns_topic.alerts.arn
}

output "dashboard_url" {
  description = "URL to the CloudWatch dashboard"
  value       = "https://${var.aws_region}.console.aws.amazon.com/cloudwatch/home?region=${var.aws_region}#dashboards:name=SP-Ingest-Pipeline"
}

# --- Secrets ---

output "azure_client_id_secret_arn" {
  description = "ARN of the Azure client ID secret"
  value       = aws_secretsmanager_secret.azure_client_id.arn
}

output "azure_tenant_id_secret_arn" {
  description = "ARN of the Azure tenant ID secret"
  value       = aws_secretsmanager_secret.azure_tenant_id.arn
}

output "azure_client_secret_secret_arn" {
  description = "ARN of the Azure client secret secret"
  value       = aws_secretsmanager_secret.azure_client_secret.arn
}

# --- SCIM Sync ---

output "identity_store_id" {
  description = "IAM Identity Center Identity Store ID"
  value       = local.identity_store_id
}

output "group_cache_refresh_lambda_arn" {
  description = "ARN of the group-cache-refresh Lambda"
  value       = aws_lambda_function.group_cache_refresh.arn
}

output "permission_drift_detector_lambda_arn" {
  description = "ARN of the permission-drift-detector Lambda"
  value       = aws_lambda_function.permission_drift_detector.arn
}

output "stale_account_cleanup_lambda_arn" {
  description = "ARN of the stale-account-cleanup Lambda"
  value       = aws_lambda_function.stale_account_cleanup.arn
}

output "governance_alerts_topic_arn" {
  description = "ARN of the governance alerts SNS topic"
  value       = aws_sns_topic.governance_alerts.arn
}

output "compliance_report_lambda_arn" {
  description = "ARN of the compliance-report-generator Lambda"
  value       = aws_lambda_function.compliance_report.arn
}

# --- Open WebUI / API Gateway ---

output "api_gateway_url" {
  description = "API Gateway URL for the query API"
  value       = var.enable_webui ? "https://${aws_apigatewayv2_api.query[0].id}.execute-api.${var.aws_region}.amazonaws.com" : ""
}

output "alb_dns_name" {
  description = "DNS name of the Open WebUI ALB"
  value       = var.enable_webui ? aws_lb.webui[0].dns_name : ""
}

output "query_handler_lambda_arn" {
  description = "ARN of the query-handler Lambda"
  value       = var.enable_webui ? aws_lambda_function.query_handler[0].arn : ""
}

output "api_authorizer_lambda_arn" {
  description = "ARN of the api-authorizer Lambda"
  value       = var.enable_webui ? aws_lambda_function.api_authorizer[0].arn : ""
}

output "guardrail_id" {
  description = "Bedrock Guardrail ID"
  value       = var.enable_webui ? aws_bedrock_guardrail.rag[0].guardrail_id : ""
}
