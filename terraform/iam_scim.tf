# ---------------------------------------------------------------
# IAM Roles for SCIM Sync Lambdas (least privilege)
# ---------------------------------------------------------------

# --- group-cache-refresh ---

resource "aws_iam_role" "group_cache_refresh" {
  name = "sp-ingest-group-cache-refresh-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "group_cache_refresh_basic" {
  role       = aws_iam_role.group_cache_refresh.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "group_cache_refresh" {
  name = "sp-ingest-group-cache-refresh-policy"
  role = aws_iam_role.group_cache_refresh.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "DynamoDBCacheReadWrite"
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:Scan"]
        Resource = [aws_dynamodb_table.user_group_cache.arn]
      },
      {
        Sid    = "IdentityStoreRead"
        Effect = "Allow"
        Action = [
          "identitystore:ListUsers",
          "identitystore:ListGroups",
          "identitystore:ListGroupMemberships",
          "identitystore:ListGroupMembershipsForMember",
        ]
        Resource = ["*"]
      },
      {
        Sid      = "DLQSend"
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = [aws_sqs_queue.group_cache_refresh_dlq.arn]
      },
    ]
  })
}

# --- permission-drift-detector ---

resource "aws_iam_role" "permission_drift_detector" {
  name = "sp-ingest-permission-drift-detector-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "permission_drift_detector_basic" {
  role       = aws_iam_role.permission_drift_detector.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "permission_drift_detector" {
  name = "sp-ingest-permission-drift-detector-policy"
  role = aws_iam_role.permission_drift_detector.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "DynamoDBPermissionsRead"
        Effect   = "Allow"
        Action   = ["dynamodb:Scan"]
        Resource = [aws_dynamodb_table.permission_mappings.arn]
      },
      {
        Sid      = "S3ListBucket"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = [aws_s3_bucket.documents.arn]
      },
      {
        Sid      = "S3PutReports"
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = ["${aws_s3_bucket.documents.arn}/governance-reports/*"]
      },
      {
        Sid      = "IdentityStoreListGroups"
        Effect   = "Allow"
        Action   = ["identitystore:ListGroups"]
        Resource = ["*"]
      },
      {
        Sid      = "SNSPublish"
        Effect   = "Allow"
        Action   = ["sns:Publish"]
        Resource = [aws_sns_topic.governance_alerts.arn]
      },
      {
        Sid      = "DLQSend"
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = [aws_sqs_queue.permission_drift_detector_dlq.arn]
      },
    ]
  })
}

# --- stale-account-cleanup ---

resource "aws_iam_role" "stale_account_cleanup" {
  name = "sp-ingest-stale-account-cleanup-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "stale_account_cleanup_basic" {
  role       = aws_iam_role.stale_account_cleanup.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "stale_account_cleanup" {
  name = "sp-ingest-stale-account-cleanup-policy"
  role = aws_iam_role.stale_account_cleanup.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DynamoDBCacheReadWrite"
        Effect = "Allow"
        Action = [
          "dynamodb:Scan",
          "dynamodb:GetItem",
          "dynamodb:UpdateItem",
        ]
        Resource = [aws_dynamodb_table.user_group_cache.arn]
      },
      {
        Sid    = "IdentityStoreDescribe"
        Effect = "Allow"
        Action = [
          "identitystore:DescribeUser",
          "identitystore:ListGroupMembershipsForMember",
        ]
        Resource = ["*"]
      },
      {
        Sid      = "DLQSend"
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = [aws_sqs_queue.stale_account_cleanup_dlq.arn]
      },
    ]
  })
}
