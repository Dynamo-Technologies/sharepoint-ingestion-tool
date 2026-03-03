# ---------------------------------------------------------------
# IAM Role for Compliance Report Lambda (least privilege)
# ---------------------------------------------------------------

resource "aws_iam_role" "compliance_report" {
  name = "sp-ingest-compliance-report-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "compliance_report_basic" {
  role       = aws_iam_role.compliance_report.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "compliance_report" {
  name = "sp-ingest-compliance-report-policy"
  role = aws_iam_role.compliance_report.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "CloudWatchLogsQuery"
        Effect = "Allow"
        Action = [
          "logs:StartQuery",
          "logs:GetQueryResults",
        ]
        Resource = "*"
      },
      {
        Sid    = "CloudWatchMetrics"
        Effect = "Allow"
        Action = [
          "cloudwatch:GetMetricStatistics",
        ]
        Resource = "*"
      },
      {
        Sid      = "S3ListBucket"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = [aws_s3_bucket.documents.arn]
      },
      {
        Sid    = "S3GovernanceReports"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
        ]
        Resource = ["${aws_s3_bucket.documents.arn}/governance-reports/*"]
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
        Resource = [aws_sqs_queue.compliance_report_dlq.arn]
      },
    ]
  })
}
