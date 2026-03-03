# ---------------------------------------------------------------
# API Lambdas: query-handler + api-authorizer
# ---------------------------------------------------------------

# --- CloudWatch Log Groups ---

resource "aws_cloudwatch_log_group" "query_handler" {
  count             = var.enable_webui ? 1 : 0
  name              = "/aws/lambda/sp-ingest-query-handler"
  retention_in_days = 90
}

resource "aws_cloudwatch_log_group" "api_authorizer" {
  count             = var.enable_webui ? 1 : 0
  name              = "/aws/lambda/sp-ingest-api-authorizer"
  retention_in_days = 90
}

# --- DLQs ---

resource "aws_sqs_queue" "query_handler_dlq" {
  count                     = var.enable_webui ? 1 : 0
  name                      = "sp-ingest-query-handler-dlq"
  message_retention_seconds = 1209600
}

# --- Lambda: query-handler ---

resource "aws_lambda_function" "query_handler" {
  count         = var.enable_webui ? 1 : 0
  function_name = "sp-ingest-query-handler"
  role          = aws_iam_role.query_handler[0].arn
  handler       = "src.query_handler.handler"
  runtime       = "python3.11"
  timeout       = 60
  memory_size   = 512

  filename         = "${path.module}/../dist/lambda-code.zip"
  source_code_hash = filebase64sha256("${path.module}/../dist/lambda-code.zip")

  layers = [aws_lambda_layer_version.shared_deps.arn]

  dead_letter_config {
    target_arn = aws_sqs_queue.query_handler_dlq[0].arn
  }

  environment {
    variables = {
      PYTHONPATH          = "/var/task/src:/opt/python"
      KNOWLEDGE_BASE_ID   = var.knowledge_base_id
      BEDROCK_MODEL_ID    = var.bedrock_model_id
      GUARDRAIL_ID        = var.enable_webui ? aws_bedrock_guardrail.rag[0].guardrail_id : ""
      GUARDRAIL_VERSION   = var.enable_webui ? aws_bedrock_guardrail.rag[0].version : ""
      AWS_REGION_NAME     = var.aws_region
      LOG_LEVEL           = "INFO"
    }
  }

  depends_on = [aws_cloudwatch_log_group.query_handler]
}

# --- Lambda: api-authorizer ---

resource "aws_lambda_function" "api_authorizer" {
  count         = var.enable_webui ? 1 : 0
  function_name = "sp-ingest-api-authorizer"
  role          = aws_iam_role.api_authorizer[0].arn
  handler       = "src.api_authorizer.handler"
  runtime       = "python3.11"
  timeout       = 10
  memory_size   = 128

  filename         = "${path.module}/../dist/lambda-code.zip"
  source_code_hash = filebase64sha256("${path.module}/../dist/lambda-code.zip")

  layers = [aws_lambda_layer_version.shared_deps.arn]

  environment {
    variables = {
      PYTHONPATH       = "/var/task/src:/opt/python"
      API_KEYS         = var.api_keys
      API_KEY_USER_MAP = var.api_key_user_map
      LOG_LEVEL        = "INFO"
    }
  }

  depends_on = [aws_cloudwatch_log_group.api_authorizer]
}
