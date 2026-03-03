variable "project_name" {
  description = "Top-level project name for tagging"
  type        = string
  default     = "dynamo-ai-platform"
}

variable "aws_region" {
  description = "AWS region for all resources"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Deployment environment (dev, staging, prod)"
  type        = string
  default     = "prod"
}

variable "s3_bucket_name" {
  description = "S3 bucket for document storage"
  type        = string
  default     = "dynamo-ai-documents"
}

variable "delta_table_name" {
  description = "DynamoDB table for delta tokens"
  type        = string
  default     = "sp-ingest-delta-tokens"
}

variable "registry_table_name" {
  description = "DynamoDB table for document registry"
  type        = string
  default     = "sp-ingest-document-registry"
}

variable "permission_mappings_table_name" {
  description = "DynamoDB table for S3 prefix → group permission mappings"
  type        = string
  default     = "doc-permission-mappings"
}

variable "user_group_cache_table_name" {
  description = "DynamoDB table caching user → group memberships from Entra ID"
  type        = string
  default     = "user-group-cache"
}

variable "alert_email" {
  description = "Email address for SNS alert notifications"
  type        = string
  default     = ""
}

variable "sharepoint_site_name" {
  description = "SharePoint site name for Graph API crawling"
  type        = string
  default     = ""
}

variable "excluded_folders" {
  description = "Comma-separated list of SharePoint folder paths to exclude from sync"
  type        = string
  default     = ""
}

# -------------------------------------------------------------------
# Bulk EC2 instance (temporary — set to false after use)
# -------------------------------------------------------------------

variable "enable_bulk_instance" {
  description = "Set to true to create the temporary EC2 bulk loader, false to destroy it"
  type        = bool
  default     = false
}

variable "bulk_key_pair_name" {
  description = "EC2 key pair name for SSH access to the bulk loader (must already exist in AWS)"
  type        = string
  default     = ""
}

variable "bulk_admin_cidr" {
  description = "CIDR block allowed to SSH into the bulk loader (e.g. 203.0.113.10/32)"
  type        = string
  default     = ""
}

# -------------------------------------------------------------------
# IAM Identity Center / SCIM
# -------------------------------------------------------------------

variable "identity_store_id" {
  description = "IAM Identity Center Identity Store ID (auto-detected if blank)"
  type        = string
  default     = ""
}

variable "governance_alerts_email" {
  description = "Email address for governance drift alerts (optional)"
  type        = string
  default     = ""
}

# -------------------------------------------------------------------
# Open WebUI / API Gateway
# -------------------------------------------------------------------

variable "open_webui_image" {
  description = "ECR image URI for the Open WebUI container"
  type        = string
  default     = ""
}

variable "knowledge_base_id" {
  description = "Bedrock Knowledge Base ID for RAG queries"
  type        = string
  default     = ""
}

variable "bedrock_model_id" {
  description = "Default Bedrock model ID for LLM generation"
  type        = string
  default     = "anthropic.claude-3-sonnet-20240229-v1:0"
}

variable "api_keys" {
  description = "Comma-separated API keys for the query API (stored as env var)"
  type        = string
  default     = ""
  sensitive   = true
}

variable "api_key_user_map" {
  description = "JSON mapping of API key → user identity"
  type        = string
  default     = "{}"
  sensitive   = true
}

variable "enable_webui" {
  description = "Set to true to deploy Open WebUI ECS infrastructure"
  type        = bool
  default     = false
}
