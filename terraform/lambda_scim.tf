# ---------------------------------------------------------------
# SCIM Sync Lambdas + EventBridge Schedules + DLQs
# ---------------------------------------------------------------

# --- SNS: Governance Alerts ---

resource "aws_sns_topic" "governance_alerts" {
  name = "sp-ingest-governance-alerts"
}

# --- DLQs ---

resource "aws_sqs_queue" "group_cache_refresh_dlq" {
  name                      = "sp-ingest-group-cache-refresh-dlq"
  message_retention_seconds = 1209600
}

resource "aws_sqs_queue" "permission_drift_detector_dlq" {
  name                      = "sp-ingest-permission-drift-detector-dlq"
  message_retention_seconds = 1209600
}

resource "aws_sqs_queue" "stale_account_cleanup_dlq" {
  name                      = "sp-ingest-stale-account-cleanup-dlq"
  message_retention_seconds = 1209600
}

# --- CloudWatch Log Groups ---

resource "aws_cloudwatch_log_group" "group_cache_refresh" {
  name              = "/aws/lambda/sp-ingest-group-cache-refresh"
  retention_in_days = 90
}

resource "aws_cloudwatch_log_group" "permission_drift_detector" {
  name              = "/aws/lambda/sp-ingest-permission-drift-detector"
  retention_in_days = 90
}

resource "aws_cloudwatch_log_group" "stale_account_cleanup" {
  name              = "/aws/lambda/sp-ingest-stale-account-cleanup"
  retention_in_days = 90
}

# --- Lambda: group-cache-refresh ---

resource "aws_lambda_function" "group_cache_refresh" {
  function_name = "sp-ingest-group-cache-refresh"
  role          = aws_iam_role.group_cache_refresh.arn
  handler       = "src.group_cache_refresh.handler"
  runtime       = "python3.11"
  timeout       = 300
  memory_size   = 256

  filename         = "${path.module}/../dist/lambda-code.zip"
  source_code_hash = filebase64sha256("${path.module}/../dist/lambda-code.zip")

  layers = [aws_lambda_layer_version.shared_deps.arn]

  dead_letter_config {
    target_arn = aws_sqs_queue.group_cache_refresh_dlq.arn
  }

  environment {
    variables = {
      PYTHONPATH             = "/var/task/src:/opt/python"
      IDENTITY_STORE_ID      = local.identity_store_id
      USER_GROUP_CACHE_TABLE = var.user_group_cache_table_name
      AWS_REGION_NAME        = var.aws_region
      LOG_LEVEL              = "INFO"
    }
  }

  depends_on = [aws_cloudwatch_log_group.group_cache_refresh]
}

resource "aws_cloudwatch_event_rule" "group_cache_refresh" {
  name                = "sp-ingest-group-cache-refresh"
  schedule_expression = "rate(15 minutes)"
}

resource "aws_cloudwatch_event_target" "group_cache_refresh" {
  rule      = aws_cloudwatch_event_rule.group_cache_refresh.name
  target_id = "GroupCacheRefreshLambda"
  arn       = aws_lambda_function.group_cache_refresh.arn
}

resource "aws_lambda_permission" "group_cache_refresh_eventbridge" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.group_cache_refresh.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.group_cache_refresh.arn
}

# --- Lambda: permission-drift-detector ---

resource "aws_lambda_function" "permission_drift_detector" {
  function_name = "sp-ingest-permission-drift-detector"
  role          = aws_iam_role.permission_drift_detector.arn
  handler       = "src.permission_drift_detector.handler"
  runtime       = "python3.11"
  timeout       = 300
  memory_size   = 256

  filename         = "${path.module}/../dist/lambda-code.zip"
  source_code_hash = filebase64sha256("${path.module}/../dist/lambda-code.zip")

  layers = [aws_lambda_layer_version.shared_deps.arn]

  dead_letter_config {
    target_arn = aws_sqs_queue.permission_drift_detector_dlq.arn
  }

  environment {
    variables = {
      PYTHONPATH                  = "/var/task/src:/opt/python"
      IDENTITY_STORE_ID           = local.identity_store_id
      S3_BUCKET                   = var.s3_bucket_name
      PERMISSION_MAPPINGS_TABLE   = var.permission_mappings_table_name
      GOVERNANCE_ALERTS_TOPIC_ARN = aws_sns_topic.governance_alerts.arn
      AWS_REGION_NAME             = var.aws_region
      LOG_LEVEL                   = "INFO"
    }
  }

  depends_on = [aws_cloudwatch_log_group.permission_drift_detector]
}

resource "aws_cloudwatch_event_rule" "permission_drift_detector" {
  name                = "sp-ingest-permission-drift-detector"
  schedule_expression = "cron(0 2 ? * SUN *)"
}

resource "aws_cloudwatch_event_target" "permission_drift_detector" {
  rule      = aws_cloudwatch_event_rule.permission_drift_detector.name
  target_id = "PermissionDriftDetectorLambda"
  arn       = aws_lambda_function.permission_drift_detector.arn
}

resource "aws_lambda_permission" "permission_drift_detector_eventbridge" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.permission_drift_detector.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.permission_drift_detector.arn
}

# --- Lambda: stale-account-cleanup ---

resource "aws_lambda_function" "stale_account_cleanup" {
  function_name = "sp-ingest-stale-account-cleanup"
  role          = aws_iam_role.stale_account_cleanup.arn
  handler       = "src.stale_account_cleanup.handler"
  runtime       = "python3.11"
  timeout       = 300
  memory_size   = 256

  filename         = "${path.module}/../dist/lambda-code.zip"
  source_code_hash = filebase64sha256("${path.module}/../dist/lambda-code.zip")

  layers = [aws_lambda_layer_version.shared_deps.arn]

  dead_letter_config {
    target_arn = aws_sqs_queue.stale_account_cleanup_dlq.arn
  }

  environment {
    variables = {
      PYTHONPATH             = "/var/task/src:/opt/python"
      IDENTITY_STORE_ID      = local.identity_store_id
      USER_GROUP_CACHE_TABLE = var.user_group_cache_table_name
      AWS_REGION_NAME        = var.aws_region
      LOG_LEVEL              = "INFO"
    }
  }

  depends_on = [aws_cloudwatch_log_group.stale_account_cleanup]
}

resource "aws_cloudwatch_event_rule" "stale_account_cleanup" {
  name                = "sp-ingest-stale-account-cleanup"
  schedule_expression = "cron(0 3 * * ? *)"
}

resource "aws_cloudwatch_event_target" "stale_account_cleanup" {
  rule      = aws_cloudwatch_event_rule.stale_account_cleanup.name
  target_id = "StaleAccountCleanupLambda"
  arn       = aws_lambda_function.stale_account_cleanup.arn
}

resource "aws_lambda_permission" "stale_account_cleanup_eventbridge" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.stale_account_cleanup.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.stale_account_cleanup.arn
}
