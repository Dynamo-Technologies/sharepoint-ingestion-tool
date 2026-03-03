# ---------------------------------------------------------------
# API Gateway HTTP API + Lambda Authorizer
# ---------------------------------------------------------------

resource "aws_apigatewayv2_api" "query" {
  count         = var.enable_webui ? 1 : 0
  name          = "sp-ingest-query-api"
  protocol_type = "HTTP"

  cors_configuration {
    allow_origins = ["*"]
    allow_methods = ["GET", "POST", "OPTIONS"]
    allow_headers = ["Content-Type", "Authorization"]
    max_age       = 3600
  }
}

resource "aws_apigatewayv2_stage" "query" {
  count       = var.enable_webui ? 1 : 0
  api_id      = aws_apigatewayv2_api.query[0].id
  name        = "$default"
  auto_deploy = true

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_gateway[0].arn
    format = jsonencode({
      requestId      = "$context.requestId"
      ip             = "$context.identity.sourceIp"
      requestTime    = "$context.requestTime"
      httpMethod     = "$context.httpMethod"
      routeKey       = "$context.routeKey"
      status         = "$context.status"
      protocol       = "$context.protocol"
      responseLength = "$context.responseLength"
      errorMessage   = "$context.error.message"
    })
  }

  default_route_settings {
    throttling_burst_limit = 100
    throttling_rate_limit  = 50
  }
}

resource "aws_cloudwatch_log_group" "api_gateway" {
  count             = var.enable_webui ? 1 : 0
  name              = "/aws/apigateway/sp-ingest-query-api"
  retention_in_days = 30
}

# --- Lambda Authorizer ---

resource "aws_apigatewayv2_authorizer" "api_key" {
  count                             = var.enable_webui ? 1 : 0
  api_id                            = aws_apigatewayv2_api.query[0].id
  authorizer_type                   = "REQUEST"
  authorizer_uri                    = aws_lambda_function.api_authorizer[0].invoke_arn
  authorizer_payload_format_version = "2.0"
  authorizer_result_ttl_in_seconds  = 300
  identity_sources                  = ["$request.header.Authorization"]
  name                              = "api-key-authorizer"
  enable_simple_responses           = true
}

# --- Routes ---

# POST /query (authorized)
resource "aws_apigatewayv2_integration" "query_handler" {
  count                  = var.enable_webui ? 1 : 0
  api_id                 = aws_apigatewayv2_api.query[0].id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.query_handler[0].invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "post_query" {
  count     = var.enable_webui ? 1 : 0
  api_id    = aws_apigatewayv2_api.query[0].id
  route_key = "POST /query"
  target    = "integrations/${aws_apigatewayv2_integration.query_handler[0].id}"

  authorization_type = "CUSTOM"
  authorizer_id      = aws_apigatewayv2_authorizer.api_key[0].id
}

# GET /health (no auth)
resource "aws_apigatewayv2_route" "get_health" {
  count     = var.enable_webui ? 1 : 0
  api_id    = aws_apigatewayv2_api.query[0].id
  route_key = "GET /health"
  target    = "integrations/${aws_apigatewayv2_integration.query_handler[0].id}"
}

# GET /user/permissions (authorized)
resource "aws_apigatewayv2_route" "get_permissions" {
  count     = var.enable_webui ? 1 : 0
  api_id    = aws_apigatewayv2_api.query[0].id
  route_key = "GET /user/permissions"
  target    = "integrations/${aws_apigatewayv2_integration.query_handler[0].id}"

  authorization_type = "CUSTOM"
  authorizer_id      = aws_apigatewayv2_authorizer.api_key[0].id
}

# --- Lambda Permissions for API Gateway ---

resource "aws_lambda_permission" "api_gw_query_handler" {
  count         = var.enable_webui ? 1 : 0
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.query_handler[0].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.query[0].execution_arn}/*/*"
}

resource "aws_lambda_permission" "api_gw_authorizer" {
  count         = var.enable_webui ? 1 : 0
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api_authorizer[0].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.query[0].execution_arn}/authorizers/${aws_apigatewayv2_authorizer.api_key[0].id}"
}
