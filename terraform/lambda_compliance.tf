# ---------------------------------------------------------------
# Compliance Report Lambda + EventBridge Schedule + DLQ
# ---------------------------------------------------------------

# --- DLQ ---

resource "aws_sqs_queue" "compliance_report_dlq" {
  name                      = "sp-ingest-compliance-report-dlq"
  message_retention_seconds = 1209600
}

# --- CloudWatch Log Group ---

resource "aws_cloudwatch_log_group" "compliance_report" {
  name              = "/aws/lambda/sp-ingest-compliance-report"
  retention_in_days = 90
}

# --- Lambda: compliance-report ---

resource "aws_lambda_function" "compliance_report" {
  function_name = "sp-ingest-compliance-report"
  role          = aws_iam_role.compliance_report.arn
  handler       = "src.compliance_report_generator.handler"
  runtime       = "python3.11"
  timeout       = 300
  memory_size   = 256

  filename         = "${path.module}/../dist/lambda-code.zip"
  source_code_hash = filebase64sha256("${path.module}/../dist/lambda-code.zip")

  layers = [aws_lambda_layer_version.shared_deps.arn]

  dead_letter_config {
    target_arn = aws_sqs_queue.compliance_report_dlq.arn
  }

  environment {
    variables = {
      PYTHONPATH                  = "/var/task/src:/opt/python"
      S3_BUCKET                   = var.s3_bucket_name
      USER_GROUP_CACHE_TABLE      = var.user_group_cache_table_name
      PERMISSION_MAPPINGS_TABLE   = var.permission_mappings_table_name
      GOVERNANCE_ALERTS_TOPIC_ARN = aws_sns_topic.governance_alerts.arn
      QUERY_HANDLER_LOG_GROUP     = var.enable_webui ? aws_cloudwatch_log_group.query_handler[0].name : ""
      GROUP_CACHE_LOG_GROUP       = aws_cloudwatch_log_group.group_cache_refresh.name
      AWS_REGION_NAME             = var.aws_region
      LOG_LEVEL                   = "INFO"
    }
  }

  depends_on = [aws_cloudwatch_log_group.compliance_report]
}

resource "aws_cloudwatch_event_rule" "compliance_report" {
  name                = "sp-ingest-compliance-report"
  schedule_expression = "cron(0 6 1 * ? *)"
}

resource "aws_cloudwatch_event_target" "compliance_report" {
  rule      = aws_cloudwatch_event_rule.compliance_report.name
  target_id = "ComplianceReportLambda"
  arn       = aws_lambda_function.compliance_report.arn
}

resource "aws_lambda_permission" "compliance_report_eventbridge" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.compliance_report.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.compliance_report.arn
}
