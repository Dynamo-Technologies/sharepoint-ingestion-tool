# ---------------------------------------------------------------
# IAM Roles for API Lambdas (query-handler + api-authorizer)
# ---------------------------------------------------------------

# --- query-handler role ---

resource "aws_iam_role" "query_handler" {
  count = var.enable_webui ? 1 : 0
  name  = "sp-ingest-query-handler-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "query_handler_basic" {
  count      = var.enable_webui ? 1 : 0
  role       = aws_iam_role.query_handler[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "query_handler" {
  count = var.enable_webui ? 1 : 0
  name  = "sp-ingest-query-handler-policy"
  role  = aws_iam_role.query_handler[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "BedrockKBRetrieve"
        Effect = "Allow"
        Action = [
          "bedrock:Retrieve",
          "bedrock:InvokeModel",
        ]
        Resource = ["*"]
      },
      {
        Sid    = "BedrockGuardrail"
        Effect = "Allow"
        Action = [
          "bedrock:ApplyGuardrail",
        ]
        Resource = var.enable_webui ? [aws_bedrock_guardrail.rag[0].guardrail_arn] : []
      },
      {
        Sid    = "DynamoDBPermissions"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:Query",
          "dynamodb:Scan",
        ]
        Resource = [
          aws_dynamodb_table.permission_mappings.arn,
          aws_dynamodb_table.user_group_cache.arn,
        ]
      },
      {
        Sid      = "DLQSend"
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = var.enable_webui ? [aws_sqs_queue.query_handler_dlq[0].arn] : []
      },
    ]
  })
}

# --- api-authorizer role ---

resource "aws_iam_role" "api_authorizer" {
  count = var.enable_webui ? 1 : 0
  name  = "sp-ingest-api-authorizer-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "api_authorizer_basic" {
  count      = var.enable_webui ? 1 : 0
  role       = aws_iam_role.api_authorizer[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}
