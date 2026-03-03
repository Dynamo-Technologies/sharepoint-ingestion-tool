# ---------------------------------------------------------------------------
# Delta Tokens — stores Graph API delta links for incremental sync.
# PK: drive_id (S)
# ---------------------------------------------------------------------------
resource "aws_dynamodb_table" "delta_tokens" {
  name         = var.delta_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "drive_id"

  attribute {
    name = "drive_id"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }
}

# ---------------------------------------------------------------------------
# Document Registry — tracks every document through the ingest lifecycle.
# PK: s3_source_key (S)
# GSI: textract-status-index  (PK: textract_status, SK: ingested_at)
# GSI: sp-library-index       (PK: sp_library, SK: sp_last_modified)
# ---------------------------------------------------------------------------
resource "aws_dynamodb_table" "document_registry" {
  name         = var.registry_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "s3_source_key"

  attribute {
    name = "s3_source_key"
    type = "S"
  }

  attribute {
    name = "textract_status"
    type = "S"
  }

  attribute {
    name = "ingested_at"
    type = "S"
  }

  attribute {
    name = "sp_library"
    type = "S"
  }

  attribute {
    name = "sp_last_modified"
    type = "S"
  }

  global_secondary_index {
    name            = "textract_status-index"
    hash_key        = "textract_status"
    range_key       = "ingested_at"
    projection_type = "ALL"
  }

  global_secondary_index {
    name            = "sp_library-index"
    hash_key        = "sp_library"
    range_key       = "sp_last_modified"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }
}


# ---------------------------------------------------------------------------
# Permission Mappings — maps S3 prefixes to allowed Entra ID groups.
# PK: s3_prefix (S)
# GSI: sensitivity_level-index (PK: sensitivity_level)
# ---------------------------------------------------------------------------
resource "aws_dynamodb_table" "permission_mappings" {
  name         = var.permission_mappings_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "s3_prefix"

  attribute {
    name = "s3_prefix"
    type = "S"
  }

  attribute {
    name = "sensitivity_level"
    type = "S"
  }

  global_secondary_index {
    name            = "sensitivity_level-index"
    hash_key        = "sensitivity_level"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }
}

# ---------------------------------------------------------------------------
# User-Group Cache — caches Entra ID user group memberships for RAG access.
# PK: user_id (S)
# TTL on ttl_expiry attribute (24 hours from last sync)
# ---------------------------------------------------------------------------
resource "aws_dynamodb_table" "user_group_cache" {
  name         = var.user_group_cache_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "user_id"

  attribute {
    name = "user_id"
    type = "S"
  }

  ttl {
    attribute_name = "ttl_expiry"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }
}
