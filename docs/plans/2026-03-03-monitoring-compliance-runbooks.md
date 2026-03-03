# Monitoring, Compliance Reporting & Operational Runbooks — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add CloudWatch monitoring/alerting extension, a monthly compliance report Lambda, 7 operational runbooks, and comprehensive documentation to make the system production-ready and maintainable.

**Architecture:** Extend existing `terraform/monitoring.tf` with new metric filters, dashboard rows, and alarms for query/governance metrics. Add a new compliance-report-generator Lambda triggered monthly via EventBridge. Create operational runbooks in `docs/runbooks/`. Rewrite README.md and add a deployment checklist.

**Tech Stack:** Python 3.11, boto3, pytest + unittest.mock, Terraform (AWS provider ~> 5.0), CloudWatch Logs Insights, EventBridge Scheduler

---

### Task 1: Compliance Report Generator Lambda — Tests

**Files:**
- Create: `tests/test_compliance_report.py`

**Context:**
- The compliance Lambda reads from: CloudWatch Logs Insights (audit data from query handler), DynamoDB (user-group-cache for stats), S3 (quarantine/ prefix listing, governance-reports/ drift reports), and Lambda invocation metrics
- Audit logs are structured JSON in `/aws/lambda/sp-ingest-query-handler` with fields: `result_type`, `response_latency_ms`, `user_id`, `user_upn`, `timestamp`
- Group-cache-refresh logs "Cache refresh complete" and returns `{"updated": N, "unchanged": N, "errors": N}`
- Drift reports are at `s3://[bucket]/governance-reports/drift-report-YYYY-MM-DD.json`
- Output: JSON + Markdown to `s3://[bucket]/governance-reports/compliance-YYYY-MM.*` + SNS summary
- The Lambda uses try/except ImportError pattern for layer imports (same as other Lambda handlers)
- Environment variables: `S3_BUCKET`, `USER_GROUP_CACHE_TABLE`, `PERMISSION_MAPPINGS_TABLE`, `GOVERNANCE_ALERTS_TOPIC_ARN`, `QUERY_HANDLER_LOG_GROUP` (defaults to `/aws/lambda/sp-ingest-query-handler`), `GROUP_CACHE_LOG_GROUP` (defaults to `/aws/lambda/sp-ingest-group-cache-refresh`), `AWS_REGION_NAME`, `LOG_LEVEL`
- Test pattern: use `@pytest.fixture` with `monkeypatch` for env, `unittest.mock.patch` for boto3 clients, `moto.mock_aws()` for S3/DynamoDB

**Step 1: Write the failing tests**

```python
"""Tests for compliance-report-generator Lambda handler."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import boto3
import moto
import pytest

BUCKET = "test-documents"
CACHE_TABLE = "test-user-group-cache"
MAPPINGS_TABLE = "test-permission-mappings"
ALERTS_TOPIC = "arn:aws:sns:us-east-1:123456789012:test-governance-alerts"
QUERY_LOG_GROUP = "/aws/lambda/sp-ingest-query-handler"
CACHE_LOG_GROUP = "/aws/lambda/sp-ingest-group-cache-refresh"


@pytest.fixture
def _env(monkeypatch):
    monkeypatch.setenv("S3_BUCKET", BUCKET)
    monkeypatch.setenv("USER_GROUP_CACHE_TABLE", CACHE_TABLE)
    monkeypatch.setenv("PERMISSION_MAPPINGS_TABLE", MAPPINGS_TABLE)
    monkeypatch.setenv("GOVERNANCE_ALERTS_TOPIC_ARN", ALERTS_TOPIC)
    monkeypatch.setenv("QUERY_HANDLER_LOG_GROUP", QUERY_LOG_GROUP)
    monkeypatch.setenv("GROUP_CACHE_LOG_GROUP", CACHE_LOG_GROUP)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_REGION_NAME", "us-east-1")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")


@pytest.fixture
def s3_bucket(_env):
    with moto.mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=BUCKET)
        yield s3


@pytest.fixture
def mock_logs():
    """Mock CloudWatch Logs Insights queries."""
    with patch("compliance_report_generator.boto3") as mock_boto:
        mock_logs_client = MagicMock()
        mock_sns_client = MagicMock()

        def client_factory(service, **kwargs):
            if service == "logs":
                return mock_logs_client
            if service == "sns":
                return mock_sns_client
            if service == "s3":
                return boto3.client("s3", region_name="us-east-1")
            if service == "cloudwatch":
                return MagicMock()
            return MagicMock()

        mock_boto.client.side_effect = client_factory

        # Default: Logs Insights returns query results
        mock_logs_client.start_query.return_value = {"queryId": "q-123"}
        mock_logs_client.get_query_results.return_value = {
            "status": "Complete",
            "results": [
                [
                    {"field": "total_queries", "value": "150"},
                    {"field": "unique_users", "value": "12"},
                    {"field": "no_results_count", "value": "8"},
                    {"field": "avg_latency_ms", "value": "345.6"},
                ],
            ],
        }

        yield {
            "logs": mock_logs_client,
            "sns": mock_sns_client,
            "boto": mock_boto,
        }


class TestComplianceReportGenerator:
    def test_handler_returns_200_with_report_summary(self, s3_bucket, mock_logs, _env):
        """Handler generates report and returns summary."""
        from compliance_report_generator import handler

        result = handler({}, None)

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert "report_period" in body
        assert "total_queries" in body

    def test_report_json_written_to_s3(self, s3_bucket, mock_logs, _env):
        """Handler writes JSON report to governance-reports/ prefix."""
        from compliance_report_generator import handler

        handler({}, None)

        objs = s3_bucket.list_objects_v2(
            Bucket=BUCKET, Prefix="governance-reports/compliance-"
        )
        keys = [o["Key"] for o in objs.get("Contents", [])]
        json_keys = [k for k in keys if k.endswith(".json")]
        assert len(json_keys) == 1

        resp = s3_bucket.get_object(Bucket=BUCKET, Key=json_keys[0])
        report = json.loads(resp["Body"].read())
        assert "query_stats" in report
        assert "quarantined_documents" in report
        assert "scim_sync" in report
        assert "drift_summary" in report

    def test_report_markdown_written_to_s3(self, s3_bucket, mock_logs, _env):
        """Handler writes Markdown report to governance-reports/ prefix."""
        from compliance_report_generator import handler

        handler({}, None)

        objs = s3_bucket.list_objects_v2(
            Bucket=BUCKET, Prefix="governance-reports/compliance-"
        )
        keys = [o["Key"] for o in objs.get("Contents", [])]
        md_keys = [k for k in keys if k.endswith(".md")]
        assert len(md_keys) == 1

        resp = s3_bucket.get_object(Bucket=BUCKET, Key=md_keys[0])
        content = resp["Body"].read().decode()
        assert "# Monthly Compliance Report" in content
        assert "Query Statistics" in content

    def test_sns_summary_published(self, s3_bucket, mock_logs, _env):
        """Handler publishes summary to governance-alerts SNS topic."""
        from compliance_report_generator import handler

        handler({}, None)

        mock_logs["sns"].publish.assert_called_once()
        call_kwargs = mock_logs["sns"].publish.call_args[1]
        assert call_kwargs["TopicArn"] == ALERTS_TOPIC
        assert "Compliance Report" in call_kwargs["Subject"]

    def test_quarantine_listing(self, s3_bucket, mock_logs, _env):
        """Handler lists quarantined documents from S3."""
        # Seed quarantine objects
        s3_bucket.put_object(Bucket=BUCKET, Key="quarantine/doc1.pdf", Body=b"x")
        s3_bucket.put_object(Bucket=BUCKET, Key="quarantine/doc2.docx", Body=b"x")

        from compliance_report_generator import handler

        result = handler({}, None)
        body = json.loads(result["body"])
        assert body["quarantined_documents"] == 2

    def test_no_query_logs_graceful(self, s3_bucket, mock_logs, _env):
        """Handler handles zero query logs gracefully."""
        mock_logs["logs"].get_query_results.return_value = {
            "status": "Complete",
            "results": [],
        }

        from compliance_report_generator import handler

        result = handler({}, None)
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["total_queries"] == 0
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_compliance_report.py -v --tb=short 2>&1 | head -30`
Expected: FAIL with `ModuleNotFoundError: No module named 'compliance_report_generator'`

---

### Task 2: Compliance Report Generator Lambda — Implementation

**Files:**
- Create: `src/compliance_report_generator.py`

**Context:**
- Must follow the try/except ImportError pattern used by all other Lambda handlers (e.g., `src/group_cache_refresh.py`)
- Reads audit data from CloudWatch Logs Insights on the query handler log group
- Lists quarantined documents from S3 `quarantine/` prefix
- Gets latest drift report from S3 `governance-reports/drift-report-*.json`
- Checks SCIM sync health from CloudWatch metrics (invocation count for group-cache-refresh)
- Outputs JSON + Markdown reports to S3 `governance-reports/compliance-YYYY-MM.*`
- Publishes SNS summary to governance-alerts topic
- Environment variables: `S3_BUCKET`, `USER_GROUP_CACHE_TABLE`, `PERMISSION_MAPPINGS_TABLE`, `GOVERNANCE_ALERTS_TOPIC_ARN`, `QUERY_HANDLER_LOG_GROUP`, `GROUP_CACHE_LOG_GROUP`, `AWS_REGION_NAME`, `LOG_LEVEL`

**Step 1: Implement the compliance report generator**

```python
"""Monthly compliance report generator Lambda.

Triggered on the 1st of each month via EventBridge. Collects metrics from
CloudWatch Logs Insights, S3, and CloudWatch Metrics to produce a JSON +
Markdown compliance report stored in S3 governance-reports/.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone, timedelta

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


def handler(event, context):
    """Generate monthly compliance report."""
    region = os.environ.get("AWS_REGION_NAME", "us-east-1")
    bucket = os.environ["S3_BUCKET"]
    alerts_topic = os.environ.get("GOVERNANCE_ALERTS_TOPIC_ARN", "")
    query_log_group = os.environ.get(
        "QUERY_HANDLER_LOG_GROUP", "/aws/lambda/sp-ingest-query-handler"
    )
    cache_log_group = os.environ.get(
        "GROUP_CACHE_LOG_GROUP", "/aws/lambda/sp-ingest-group-cache-refresh"
    )

    now = datetime.now(timezone.utc)
    # Report covers the previous month
    first_of_this_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    first_of_last_month = (first_of_this_month - timedelta(days=1)).replace(day=1)
    period_start = first_of_last_month
    period_end = first_of_this_month
    period_label = period_start.strftime("%Y-%m")

    logs_client = boto3.client("logs", region_name=region)
    s3_client = boto3.client("s3", region_name=region)
    sns_client = boto3.client("sns", region_name=region)
    cw_client = boto3.client("cloudwatch", region_name=region)

    # --- Section 1: Query statistics from CloudWatch Logs Insights ---
    query_stats = _get_query_stats(
        logs_client, query_log_group, period_start, period_end
    )

    # --- Section 2: Quarantined documents ---
    quarantine_count = _count_quarantined(s3_client, bucket)

    # --- Section 3: SCIM sync health ---
    scim_stats = _get_scim_health(cw_client, period_start, period_end)

    # --- Section 4: Group membership changes ---
    group_changes = _get_group_changes(
        logs_client, cache_log_group, period_start, period_end
    )

    # --- Section 5: Drift detection summary ---
    drift_summary = _get_latest_drift(s3_client, bucket)

    # --- Build report ---
    report = {
        "report_period": period_label,
        "generated_at": now.isoformat(),
        "query_stats": query_stats,
        "quarantined_documents": quarantine_count,
        "scim_sync": scim_stats,
        "group_changes": group_changes,
        "drift_summary": drift_summary,
    }

    # --- Write JSON report to S3 ---
    json_key = f"governance-reports/compliance-{period_label}.json"
    s3_client.put_object(
        Bucket=bucket,
        Key=json_key,
        Body=json.dumps(report, indent=2, default=str),
        ContentType="application/json",
    )

    # --- Write Markdown report to S3 ---
    md_key = f"governance-reports/compliance-{period_label}.md"
    md_content = _render_markdown(report)
    s3_client.put_object(
        Bucket=bucket,
        Key=md_key,
        Body=md_content.encode(),
        ContentType="text/markdown",
    )

    # --- Publish SNS summary ---
    if alerts_topic:
        summary = (
            f"Period: {period_label}\n"
            f"Total queries: {query_stats.get('total_queries', 0)}\n"
            f"Unique users: {query_stats.get('unique_users', 0)}\n"
            f"Permission denials: {query_stats.get('no_results_count', 0)}\n"
            f"Quarantined docs: {quarantine_count}\n"
            f"Reports: s3://{bucket}/{json_key}"
        )
        sns_client.publish(
            TopicArn=alerts_topic,
            Subject=f"Monthly Compliance Report — {period_label}",
            Message=summary,
        )

    logger.info("Compliance report generated: %s", json_key)

    return {
        "statusCode": 200,
        "body": json.dumps({
            "report_period": period_label,
            "total_queries": query_stats.get("total_queries", 0),
            "unique_users": query_stats.get("unique_users", 0),
            "quarantined_documents": quarantine_count,
            "s3_json": json_key,
            "s3_markdown": md_key,
        }),
    }


def _get_query_stats(logs_client, log_group, start, end):
    """Query CloudWatch Logs Insights for audit statistics."""
    query = (
        "fields @timestamp, result_type, response_latency_ms, user_id\n"
        "| stats count(*) as total_queries,\n"
        "        count_distinct(user_id) as unique_users,\n"
        "        sum(result_type = 'no_results') as no_results_count,\n"
        "        avg(response_latency_ms) as avg_latency_ms"
    )
    try:
        resp = logs_client.start_query(
            logGroupName=log_group,
            startTime=int(start.timestamp()),
            endTime=int(end.timestamp()),
            queryString=query,
        )
        query_id = resp["queryId"]

        # Poll for results (max 30 seconds)
        for _ in range(30):
            result = logs_client.get_query_results(queryId=query_id)
            if result["status"] == "Complete":
                break
            time.sleep(1)

        if not result.get("results"):
            return {
                "total_queries": 0,
                "unique_users": 0,
                "no_results_count": 0,
                "avg_latency_ms": 0,
            }

        row = {r["field"]: r["value"] for r in result["results"][0]}
        return {
            "total_queries": int(float(row.get("total_queries", 0))),
            "unique_users": int(float(row.get("unique_users", 0))),
            "no_results_count": int(float(row.get("no_results_count", 0))),
            "avg_latency_ms": round(float(row.get("avg_latency_ms", 0)), 1),
        }
    except Exception:
        logger.exception("Failed to query audit logs")
        return {
            "total_queries": 0,
            "unique_users": 0,
            "no_results_count": 0,
            "avg_latency_ms": 0,
        }


def _count_quarantined(s3_client, bucket):
    """Count documents in quarantine/ prefix."""
    try:
        paginator = s3_client.get_paginator("list_objects_v2")
        count = 0
        for page in paginator.paginate(Bucket=bucket, Prefix="quarantine/"):
            count += page.get("KeyCount", 0)
        return count
    except Exception:
        logger.exception("Failed to list quarantine objects")
        return 0


def _get_scim_health(cw_client, start, end):
    """Check SCIM sync uptime from CloudWatch Lambda invocation metrics."""
    try:
        resp = cw_client.get_metric_statistics(
            Namespace="AWS/Lambda",
            MetricName="Invocations",
            Dimensions=[
                {"Name": "FunctionName", "Value": "sp-ingest-group-cache-refresh"},
            ],
            StartTime=start,
            EndTime=end,
            Period=900,  # 15-minute windows
            Statistics=["Sum"],
        )
        datapoints = resp.get("Datapoints", [])
        total_windows = max(len(datapoints), 1)
        success_windows = sum(1 for dp in datapoints if dp.get("Sum", 0) > 0)
        uptime_pct = round(100 * success_windows / total_windows, 1)
        return {
            "total_windows": total_windows,
            "success_windows": success_windows,
            "uptime_pct": uptime_pct,
        }
    except Exception:
        logger.exception("Failed to get SCIM health metrics")
        return {"total_windows": 0, "success_windows": 0, "uptime_pct": 0}


def _get_group_changes(logs_client, log_group, start, end):
    """Count group membership changes from cache-refresh logs."""
    query = (
        'filter @message like /added groups|removed groups/\n'
        '| stats count(*) as total_changes'
    )
    try:
        resp = logs_client.start_query(
            logGroupName=log_group,
            startTime=int(start.timestamp()),
            endTime=int(end.timestamp()),
            queryString=query,
        )
        query_id = resp["queryId"]

        for _ in range(30):
            result = logs_client.get_query_results(queryId=query_id)
            if result["status"] == "Complete":
                break
            time.sleep(1)

        if not result.get("results"):
            return {"total_changes": 0}

        row = {r["field"]: r["value"] for r in result["results"][0]}
        return {"total_changes": int(float(row.get("total_changes", 0)))}
    except Exception:
        logger.exception("Failed to query group change logs")
        return {"total_changes": 0}


def _get_latest_drift(s3_client, bucket):
    """Get latest drift detection report from S3."""
    try:
        resp = s3_client.list_objects_v2(
            Bucket=bucket,
            Prefix="governance-reports/drift-report-",
        )
        contents = resp.get("Contents", [])
        if not contents:
            return {"status": "no_reports_found"}

        latest = sorted(contents, key=lambda o: o["Key"], reverse=True)[0]
        obj = s3_client.get_object(Bucket=bucket, Key=latest["Key"])
        report = json.loads(obj["Body"].read())
        return {
            "report_date": report.get("report_date", "unknown"),
            "unmapped_prefixes": len(report.get("unmapped_prefixes", [])),
            "stale_mappings": len(report.get("stale_mappings", [])),
            "orphaned_groups": len(report.get("orphaned_groups", [])),
        }
    except Exception:
        logger.exception("Failed to read drift report")
        return {"status": "error"}


def _render_markdown(report):
    """Render compliance report as Markdown."""
    qs = report.get("query_stats", {})
    scim = report.get("scim_sync", {})
    drift = report.get("drift_summary", {})
    gc = report.get("group_changes", {})

    lines = [
        f"# Monthly Compliance Report — {report['report_period']}",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "## Query Statistics",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Total queries | {qs.get('total_queries', 0)} |",
        f"| Unique users | {qs.get('unique_users', 0)} |",
        f"| Permission denials (no results) | {qs.get('no_results_count', 0)} |",
        f"| Average latency (ms) | {qs.get('avg_latency_ms', 0)} |",
        "",
        "## Quarantined Documents",
        "",
        f"Documents in quarantine: **{report.get('quarantined_documents', 0)}**",
        "",
        "## SCIM Sync Health",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| 15-min windows monitored | {scim.get('total_windows', 0)} |",
        f"| Successful sync windows | {scim.get('success_windows', 0)} |",
        f"| Uptime | {scim.get('uptime_pct', 0)}% |",
        "",
        "## Group Membership Changes",
        "",
        f"Total group changes recorded: **{gc.get('total_changes', 0)}**",
        "",
        "## Drift Detection Summary",
        "",
    ]

    if drift.get("status") == "no_reports_found":
        lines.append("No drift reports found for this period.")
    elif drift.get("status") == "error":
        lines.append("Error reading drift report.")
    else:
        lines.extend([
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Last report date | {drift.get('report_date', 'N/A')} |",
            f"| Unmapped prefixes | {drift.get('unmapped_prefixes', 0)} |",
            f"| Stale mappings | {drift.get('stale_mappings', 0)} |",
            f"| Orphaned groups | {drift.get('orphaned_groups', 0)} |",
        ])

    return "\n".join(lines) + "\n"
```

**Step 2: Run tests to verify they pass**

Run: `python -m pytest tests/test_compliance_report.py -v --tb=short`
Expected: 6 tests PASS

**Step 3: Commit**

```bash
git add src/compliance_report_generator.py tests/test_compliance_report.py
git commit -m "feat: add monthly compliance report generator Lambda"
```

---

### Task 3: Compliance Lambda Terraform

**Files:**
- Create: `terraform/lambda_compliance.tf`
- Create: `terraform/iam_compliance.tf`

**Context:**
- Follow the exact pattern from `terraform/lambda_scim.tf` for Lambda + EventBridge + DLQ + Log Group
- Follow the exact pattern from `terraform/iam_scim.tf` for IAM role + policies
- Lambda naming convention: `sp-ingest-compliance-report`
- Log group: `/aws/lambda/sp-ingest-compliance-report`
- DLQ: `sp-ingest-compliance-report-dlq`
- EventBridge schedule: `cron(0 6 1 * ? *)` — 6 AM UTC on 1st of each month
- Compliance Lambda needs: CloudWatch Logs Insights read, S3 read/write on governance-reports/ and quarantine/ prefixes, CloudWatch GetMetricStatistics, SNS Publish to governance-alerts, DLQ send
- Uses `aws_lambda_layer_version.shared_deps.arn` for the layer
- Uses `${path.module}/../dist/lambda-code.zip` for code

**Step 1: Create lambda_compliance.tf**

```hcl
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

# --- Lambda ---

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

# --- EventBridge: 1st of month at 6 AM UTC ---

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
```

**Step 2: Create iam_compliance.tf**

```hcl
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
        Sid    = "CloudWatchLogsInsights"
        Effect = "Allow"
        Action = [
          "logs:StartQuery",
          "logs:GetQueryResults",
        ]
        Resource = ["*"]
      },
      {
        Sid    = "CloudWatchMetrics"
        Effect = "Allow"
        Action = ["cloudwatch:GetMetricStatistics"]
        Resource = ["*"]
      },
      {
        Sid    = "S3ReadQuarantine"
        Effect = "Allow"
        Action = ["s3:ListBucket"]
        Resource = [aws_s3_bucket.documents.arn]
      },
      {
        Sid    = "S3ReadWriteReports"
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:PutObject"]
        Resource = ["${aws_s3_bucket.documents.arn}/governance-reports/*"]
      },
      {
        Sid    = "SNSPublish"
        Effect = "Allow"
        Action = ["sns:Publish"]
        Resource = [aws_sns_topic.governance_alerts.arn]
      },
      {
        Sid    = "DLQSend"
        Effect = "Allow"
        Action = ["sqs:SendMessage"]
        Resource = [aws_sqs_queue.compliance_report_dlq.arn]
      },
    ]
  })
}
```

**Step 3: Run terraform validate**

Run: `cd terraform && terraform validate`
Expected: `Success! The configuration is valid.`

**Step 4: Commit**

```bash
git add terraform/lambda_compliance.tf terraform/iam_compliance.tf
git commit -m "infra: add compliance report Lambda with EventBridge monthly schedule"
```

---

### Task 4: CloudWatch Metric Filters

**Files:**
- Modify: `terraform/monitoring.tf` (append after existing metric filters, before alarms section)

**Context:**
- Existing metric filters are in `terraform/monitoring.tf` lines 50-165
- Metric namespace: `SP-Ingest` (from `local.metric_namespace`)
- Query handler log group: `aws_cloudwatch_log_group.query_handler[0].name` (conditional on `enable_webui`)
- Group-cache-refresh log group: `aws_cloudwatch_log_group.group_cache_refresh.name`
- Audit log JSON fields: `result_type`, `response_latency_ms`, `user_id`
- Group-cache-refresh logs: "Cache refresh complete" with stats JSON
- Need metric filters for:
  1. QueryVolume — count all audit log entries in query handler log group
  2. QueryNoResults — count `"result_type": "no_results"` entries
  3. QueryLatency — extract `response_latency_ms` value
  4. GuardrailActivation — match `guardrail_intervened` or `GUARDRAIL_INTERVENED`
  5. SCIMSyncSuccess — match "Cache refresh complete" in group-cache-refresh logs
- Query handler metric filters must be conditional on `enable_webui`

**Step 1: Add metric filters to monitoring.tf**

Insert after the `bulk_documents_ingested` metric filter (after line 165), before the alarms section:

```hcl
# --- Query handler audit metrics (conditional on WebUI) ---

resource "aws_cloudwatch_log_metric_filter" "query_volume" {
  count          = var.enable_webui ? 1 : 0
  name           = "sp-ingest-query-volume"
  log_group_name = aws_cloudwatch_log_group.query_handler[0].name
  pattern        = "{ $.result_type = \"*\" }"

  metric_transformation {
    name          = "QueryVolume"
    namespace     = local.metric_namespace
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_log_metric_filter" "query_no_results" {
  count          = var.enable_webui ? 1 : 0
  name           = "sp-ingest-query-no-results"
  log_group_name = aws_cloudwatch_log_group.query_handler[0].name
  pattern        = "{ $.result_type = \"no_results\" }"

  metric_transformation {
    name          = "QueryNoResults"
    namespace     = local.metric_namespace
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_log_metric_filter" "query_latency" {
  count          = var.enable_webui ? 1 : 0
  name           = "sp-ingest-query-latency"
  log_group_name = aws_cloudwatch_log_group.query_handler[0].name
  pattern        = "{ $.response_latency_ms = * }"

  metric_transformation {
    name          = "QueryLatencyMs"
    namespace     = local.metric_namespace
    value         = "$.response_latency_ms"
    default_value = "0"
  }
}

resource "aws_cloudwatch_log_metric_filter" "guardrail_activation" {
  count          = var.enable_webui ? 1 : 0
  name           = "sp-ingest-guardrail-activation"
  log_group_name = aws_cloudwatch_log_group.query_handler[0].name
  pattern        = "\"GUARDRAIL_INTERVENED\""

  metric_transformation {
    name          = "GuardrailActivation"
    namespace     = local.metric_namespace
    value         = "1"
    default_value = "0"
  }
}

# --- SCIM sync success metric ---

resource "aws_cloudwatch_log_metric_filter" "scim_sync_success" {
  name           = "sp-ingest-scim-sync-success"
  log_group_name = aws_cloudwatch_log_group.group_cache_refresh.name
  pattern        = "\"Cache refresh complete\""

  metric_transformation {
    name          = "SCIMSyncSuccess"
    namespace     = local.metric_namespace
    value         = "1"
    default_value = "0"
  }
}
```

**Step 2: Run terraform validate**

Run: `cd terraform && terraform validate`
Expected: `Success! The configuration is valid.`

**Step 3: Commit**

```bash
git add terraform/monitoring.tf
git commit -m "infra: add CloudWatch metric filters for query and governance metrics"
```

---

### Task 5: CloudWatch Dashboard Extension

**Files:**
- Modify: `terraform/monitoring.tf` (extend dashboard widget array)

**Context:**
- Existing dashboard has 4 rows at y=0 (h=4), y=4 (h=6), y=10 (h=6), y=16 (h=6)
- Next available y position: y=22
- Add 3 new rows:
  - Row 5 (y=22): Query & Auth Metrics — query volume, null result rate, avg latency, guardrail activations
  - Row 6 (y=28): Governance Health — SCIM sync success, quarantine count (S3 metric)
  - Row 7 (y=34): Governance Lambda Errors & DLQ Depth — errors for all governance Lambdas + DLQ depth
- Dashboard resource is `aws_cloudwatch_dashboard.pipeline` in monitoring.tf
- The widgets array is in the `dashboard_body` jsonencode block
- New widgets use custom metrics from `local.metric_namespace` ("SP-Ingest")
- DLQs: `sp-ingest-group-cache-refresh-dlq`, `sp-ingest-permission-drift-detector-dlq`, `sp-ingest-stale-account-cleanup-dlq`, `sp-ingest-query-handler-dlq`, `sp-ingest-compliance-report-dlq`

**Step 1: Add 3 new dashboard rows**

Append these widgets to the dashboard widgets array (before the closing `]` in the widgets list, after the Row 4 widgets):

```hcl
      # ---------------------------------------------------------------
      # Row 5: Query & Auth Metrics
      # ---------------------------------------------------------------
      {
        type   = "metric"
        x      = 0
        y      = 22
        width  = 6
        height = 6
        properties = {
          metrics = [
            [local.metric_namespace, "QueryVolume", { stat = "Sum", label = "Queries" }],
          ]
          view   = "timeSeries"
          region = var.aws_region
          title  = "Query Volume"
          period = 300
        }
      },
      {
        type   = "metric"
        x      = 6
        y      = 22
        width  = 6
        height = 6
        properties = {
          metrics = [
            [local.metric_namespace, "QueryNoResults", { stat = "Sum", label = "No Results" }],
            [local.metric_namespace, "QueryVolume", { stat = "Sum", label = "Total" }],
          ]
          view   = "timeSeries"
          region = var.aws_region
          title  = "Permission-Scoped Null Results"
          period = 3600
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 22
        width  = 6
        height = 6
        properties = {
          metrics = [
            [local.metric_namespace, "QueryLatencyMs", { stat = "Average", label = "Avg" }],
            [local.metric_namespace, "QueryLatencyMs", { stat = "p99", label = "p99" }],
          ]
          view   = "timeSeries"
          region = var.aws_region
          title  = "Query Latency (ms)"
          period = 300
        }
      },
      {
        type   = "metric"
        x      = 18
        y      = 22
        width  = 6
        height = 6
        properties = {
          metrics = [
            [local.metric_namespace, "GuardrailActivation", { stat = "Sum", label = "Guardrail Activations" }],
          ]
          view   = "timeSeries"
          region = var.aws_region
          title  = "Bedrock Guardrail Activations"
          period = 3600
          yAxis  = { left = { min = 0 } }
        }
      },

      # ---------------------------------------------------------------
      # Row 6: Governance Health
      # ---------------------------------------------------------------
      {
        type   = "metric"
        x      = 0
        y      = 28
        width  = 8
        height = 6
        properties = {
          metrics = [
            [local.metric_namespace, "SCIMSyncSuccess", { stat = "Sum", label = "Sync Successes" }],
          ]
          view   = "timeSeries"
          region = var.aws_region
          title  = "SCIM Sync Health"
          period = 900
        }
      },
      {
        type   = "metric"
        x      = 8
        y      = 28
        width  = 8
        height = 6
        properties = {
          metrics = [
            ["AWS/Lambda", "Invocations", "FunctionName", "sp-ingest-group-cache-refresh", { stat = "Sum", label = "Cache Refresh" }],
            ["AWS/Lambda", "Invocations", "FunctionName", "sp-ingest-stale-account-cleanup", { stat = "Sum", label = "Stale Cleanup" }],
            ["AWS/Lambda", "Invocations", "FunctionName", "sp-ingest-permission-drift-detector", { stat = "Sum", label = "Drift Detector" }],
          ]
          view   = "timeSeries"
          region = var.aws_region
          title  = "Governance Lambda Invocations"
          period = 3600
        }
      },
      {
        type   = "metric"
        x      = 16
        y      = 28
        width  = 8
        height = 6
        properties = {
          metrics = [
            ["AWS/S3", "NumberOfObjects", "StorageType", "AllStorageTypes", "BucketName", var.s3_bucket_name, "FilterId", "quarantine-count", { stat = "Average", label = "Quarantine Objects" }],
          ]
          view   = "singleValue"
          region = var.aws_region
          title  = "Quarantine Document Count"
          period = 86400
        }
      },

      # ---------------------------------------------------------------
      # Row 7: Governance Lambda Errors & DLQ Depth
      # ---------------------------------------------------------------
      {
        type   = "metric"
        x      = 0
        y      = 34
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["AWS/Lambda", "Errors", "FunctionName", "sp-ingest-group-cache-refresh", { stat = "Sum", label = "Cache Refresh" }],
            ["AWS/Lambda", "Errors", "FunctionName", "sp-ingest-permission-drift-detector", { stat = "Sum", label = "Drift Detector" }],
            ["AWS/Lambda", "Errors", "FunctionName", "sp-ingest-stale-account-cleanup", { stat = "Sum", label = "Stale Cleanup" }],
            ["AWS/Lambda", "Errors", "FunctionName", "sp-ingest-compliance-report", { stat = "Sum", label = "Compliance Report" }],
          ]
          view   = "timeSeries"
          region = var.aws_region
          title  = "Governance Lambda Errors"
          period = 300
          yAxis  = { left = { min = 0 } }
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 34
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", "sp-ingest-group-cache-refresh-dlq", { stat = "Maximum", label = "Cache Refresh DLQ" }],
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", "sp-ingest-permission-drift-detector-dlq", { stat = "Maximum", label = "Drift Detector DLQ" }],
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", "sp-ingest-stale-account-cleanup-dlq", { stat = "Maximum", label = "Stale Cleanup DLQ" }],
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", "sp-ingest-compliance-report-dlq", { stat = "Maximum", label = "Compliance DLQ" }],
          ]
          view   = "timeSeries"
          region = var.aws_region
          title  = "DLQ Message Depth"
          period = 300
          yAxis  = { left = { min = 0 } }
        }
      },
```

**Step 2: Run terraform validate**

Run: `cd terraform && terraform validate`
Expected: `Success! The configuration is valid.`

**Step 3: Commit**

```bash
git add terraform/monitoring.tf
git commit -m "infra: extend CloudWatch dashboard with query, governance, and DLQ rows"
```

---

### Task 6: CloudWatch Alarms

**Files:**
- Modify: `terraform/monitoring.tf` (append new alarms after existing alarms)

**Context:**
- Existing alarms end at line ~273 in monitoring.tf
- All new alarms route to `aws_sns_topic.alerts.arn` (sp-ingest-alerts)
- Follow the same alarm pattern: `alarm_name`, `alarm_description`, `comparison_operator`, etc.
- 7 new alarms (the design says 8 but there are really 3 DLQ alarms we can group):
  1. CRITICAL: SCIM sync stale — group-cache-refresh invocations <1 in 2h
  2. CRITICAL: Quarantine detected — quarantine SNS publish count >0
  3. HIGH: Permission null rate — QueryNoResults/QueryVolume >20% in 1h (use math expression)
  4. HIGH: DLQ depth >0 — one per governance DLQ (3 alarms, or use composite)
  5. MEDIUM: Query latency p99 >10000ms
  6. MEDIUM: Stale cache entries — group-cache-refresh errors >10% (simplified)
  7. LOW: PII/Guardrail activation — GuardrailActivation >0 (OK action notification)
- For DLQ alarms, create one per DLQ (3 governance DLQs) to keep it simple

**Step 1: Add 8 new alarms to monitoring.tf**

Append after the existing `dynamo_throttle_registry` alarm:

```hcl
# --- CRITICAL: SCIM sync not completed in 2 hours ---
resource "aws_cloudwatch_metric_alarm" "scim_sync_stale" {
  alarm_name          = "sp-ingest-scim-sync-stale"
  alarm_description   = "CRITICAL: Group cache refresh has not run in 2 hours"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Invocations"
  namespace           = "AWS/Lambda"
  period              = 7200
  statistic           = "Sum"
  threshold           = 1
  treat_missing_data  = "breaching"

  dimensions = {
    FunctionName = aws_lambda_function.group_cache_refresh.function_name
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]
}

# --- CRITICAL: Quarantine documents detected ---
resource "aws_cloudwatch_metric_alarm" "quarantine_detected" {
  alarm_name          = "sp-ingest-quarantine-detected"
  alarm_description   = "CRITICAL: Documents quarantined due to missing permission mappings"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "NumberOfMessagesPublished"
  namespace           = "AWS/SNS"
  period              = 300
  statistic           = "Sum"
  threshold           = 0
  treat_missing_data  = "notBreaching"

  dimensions = {
    TopicName = aws_sns_topic.quarantine_alerts.name
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]
}

# --- HIGH: Permission null result rate > 20% (1h window) ---
resource "aws_cloudwatch_metric_alarm" "permission_null_rate" {
  count               = var.enable_webui ? 1 : 0
  alarm_name          = "sp-ingest-permission-null-rate"
  alarm_description   = "HIGH: More than 20% of queries return no results (permission denial)"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = 20

  metric_query {
    id          = "null_rate"
    expression  = "IF(total > 0, (denied / total) * 100, 0)"
    label       = "Null Result Rate %"
    return_data = true
  }

  metric_query {
    id = "denied"
    metric {
      metric_name = "QueryNoResults"
      namespace   = local.metric_namespace
      period      = 3600
      stat        = "Sum"
    }
  }

  metric_query {
    id = "total"
    metric {
      metric_name = "QueryVolume"
      namespace   = local.metric_namespace
      period      = 3600
      stat        = "Sum"
    }
  }

  treat_missing_data = "notBreaching"

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]
}

# --- HIGH: DLQ depth > 0 (group-cache-refresh) ---
resource "aws_cloudwatch_metric_alarm" "dlq_group_cache_refresh" {
  alarm_name          = "sp-ingest-dlq-group-cache-refresh"
  alarm_description   = "HIGH: Messages in group-cache-refresh dead letter queue"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 300
  statistic           = "Maximum"
  threshold           = 0
  treat_missing_data  = "notBreaching"

  dimensions = {
    QueueName = aws_sqs_queue.group_cache_refresh_dlq.name
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
}

# --- HIGH: DLQ depth > 0 (permission-drift-detector) ---
resource "aws_cloudwatch_metric_alarm" "dlq_permission_drift" {
  alarm_name          = "sp-ingest-dlq-permission-drift"
  alarm_description   = "HIGH: Messages in permission-drift-detector dead letter queue"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 300
  statistic           = "Maximum"
  threshold           = 0
  treat_missing_data  = "notBreaching"

  dimensions = {
    QueueName = aws_sqs_queue.permission_drift_detector_dlq.name
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
}

# --- HIGH: DLQ depth > 0 (stale-account-cleanup) ---
resource "aws_cloudwatch_metric_alarm" "dlq_stale_account" {
  alarm_name          = "sp-ingest-dlq-stale-account"
  alarm_description   = "HIGH: Messages in stale-account-cleanup dead letter queue"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 300
  statistic           = "Maximum"
  threshold           = 0
  treat_missing_data  = "notBreaching"

  dimensions = {
    QueueName = aws_sqs_queue.stale_account_cleanup_dlq.name
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
}

# --- MEDIUM: Query latency p99 > 10 seconds ---
resource "aws_cloudwatch_metric_alarm" "query_latency_p99" {
  count               = var.enable_webui ? 1 : 0
  alarm_name          = "sp-ingest-query-latency-p99"
  alarm_description   = "MEDIUM: Query latency p99 exceeds 10 seconds"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "QueryLatencyMs"
  namespace           = local.metric_namespace
  period              = 300
  extended_statistic  = "p99"
  threshold           = 10000
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]
}

# --- LOW: Bedrock guardrail PII redaction triggered ---
resource "aws_cloudwatch_metric_alarm" "guardrail_triggered" {
  count               = var.enable_webui ? 1 : 0
  alarm_name          = "sp-ingest-guardrail-triggered"
  alarm_description   = "LOW: Bedrock guardrail intervened (PII redaction or content blocking)"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "GuardrailActivation"
  namespace           = local.metric_namespace
  period              = 3600
  statistic           = "Sum"
  threshold           = 0
  treat_missing_data  = "notBreaching"

  ok_actions    = [aws_sns_topic.alerts.arn]
}
```

**Step 2: Run terraform validate**

Run: `cd terraform && terraform validate`
Expected: `Success! The configuration is valid.`

**Step 3: Commit**

```bash
git add terraform/monitoring.tf
git commit -m "infra: add 8 CloudWatch alarms for SCIM, quarantine, DLQ, latency, guardrails"
```

---

### Task 7: Terraform Outputs + Validation

**Files:**
- Modify: `terraform/outputs.tf`

**Context:**
- Add compliance Lambda ARN output
- Existing outputs end with guardrail_id at line ~185
- Follow existing pattern: output name, description, value

**Step 1: Add compliance Lambda output**

Append to `terraform/outputs.tf`:

```hcl
# --- Compliance Report ---

output "compliance_report_lambda_arn" {
  description = "ARN of the compliance-report-generator Lambda"
  value       = aws_lambda_function.compliance_report.arn
}
```

**Step 2: Run terraform validate**

Run: `cd terraform && terraform validate`
Expected: `Success! The configuration is valid.`

**Step 3: Commit**

```bash
git add terraform/outputs.tf
git commit -m "infra: add compliance report Lambda ARN output"
```

---

### Task 8: Operational Runbooks (1-4)

**Files:**
- Create: `docs/runbooks/ADD_NEW_DEPARTMENT.md`
- Create: `docs/runbooks/ONBOARD_NEW_USER.md`
- Create: `docs/runbooks/OFFBOARD_USER.md`
- Create: `docs/runbooks/ADD_NEW_DOCUMENT_LIBRARY.md`

**Context:**
- All runbooks live in `docs/runbooks/` directory (create directory if needed)
- Each runbook has numbered step-by-step instructions with exact commands
- Reference existing infrastructure: DynamoDB tables (`doc-permission-mappings`, `user-group-cache`), Lambdas (`sp-ingest-group-cache-refresh`, `sp-ingest-permission-drift-detector`), S3 bucket
- Reference existing docs: `docs/ENTRA_ID_SCIM_SETUP_RUNBOOK.md` for SCIM/Entra ID steps
- Key tables: `doc-permission-mappings` (s3_prefix → allowed_groups, sensitivity_level), `user-group-cache` (user_id → groups, upn, last_synced)
- SCIM sync runs every 15 min via EventBridge → group-cache-refresh Lambda
- Drift detector runs weekly Sunday 02:00 UTC
- Group-cache-refresh reads from IAM Identity Center Identity Store
- Quarantine documents are in `s3://[bucket]/quarantine/` prefix

**Step 1: Create docs/runbooks/ directory**

Run: `mkdir -p docs/runbooks`

**Step 2: Write ADD_NEW_DEPARTMENT.md**

Content should cover:
1. Create Entra ID security group for department
2. Assign group to enterprise application in Entra admin center
3. Wait for SCIM provisioning (~40 min)
4. Create S3 prefix structure for department documents
5. Add permission mapping to DynamoDB `doc-permission-mappings` table
6. Trigger group-cache-refresh Lambda manually
7. Run drift detector to confirm no unmapped prefixes
8. Validate: verify user in new department can query their documents

Include exact AWS CLI commands for each step (e.g., `aws dynamodb put-item`, `aws lambda invoke`).

**Step 3: Write ONBOARD_NEW_USER.md**

Content should cover:
1. Add user to appropriate Entra ID security groups
2. Assign user to enterprise application (if not already group-assigned)
3. Wait for SCIM provisioning
4. Verify user appears in IAM Identity Center
5. Trigger group-cache-refresh Lambda manually for faster sync
6. Verify user record in DynamoDB `user-group-cache` table
7. Validate: verify user can authenticate and query appropriate documents

**Step 4: Write OFFBOARD_USER.md**

Content should cover:
1. Remove user from Entra ID security groups (or disable account)
2. SCIM provisioning will propagate deletion (~40 min)
3. Trigger group-cache-refresh Lambda to update cache immediately
4. Verify user record updated in DynamoDB (status = "deleted" or groups cleared)
5. Verify stale-account-cleanup Lambda will set TTL for cache expiry
6. Audit: check CloudWatch audit logs for user's recent queries

**Step 5: Write ADD_NEW_DOCUMENT_LIBRARY.md**

Content should cover:
1. Create S3 prefix for document library (e.g., `source/Dynamo/NewLibrary/`)
2. Add permission mapping to DynamoDB `doc-permission-mappings` table
3. Run drift detector to confirm mapping is recognized
4. Ingest documents (via daily sync or bulk ingestion)
5. Validate: run a test query filtering to the new library prefix

**Step 6: Commit**

```bash
git add docs/runbooks/ADD_NEW_DEPARTMENT.md docs/runbooks/ONBOARD_NEW_USER.md docs/runbooks/OFFBOARD_USER.md docs/runbooks/ADD_NEW_DOCUMENT_LIBRARY.md
git commit -m "docs: add runbooks for department, user onboarding/offboarding, document library"
```

---

### Task 9: Operational Runbooks (5-7)

**Files:**
- Create: `docs/runbooks/HANDLE_QUARANTINED_DOCUMENT.md`
- Create: `docs/runbooks/INVESTIGATE_PERMISSION_DENIAL.md`
- Create: `docs/runbooks/EMERGENCY_REVOKE_ACCESS.md`

**Context:**
- Same conventions as Task 8
- Quarantine SNS topic: `doc-quarantine-alerts`
- Quarantine prefix: `s3://[bucket]/quarantine/`
- Document registry table: `doc-registry` (tracks all ingested documents)
- Permission mappings table: `doc-permission-mappings`
- User group cache table: `user-group-cache`
- Query handler logs to `/aws/lambda/sp-ingest-query-handler` with audit JSON entries
- IAM Identity Center controls SCIM-provisioned access

**Step 1: Write HANDLE_QUARANTINED_DOCUMENT.md**

Content should cover:
1. Alert notification arrives via SNS (quarantine-alerts topic)
2. Identify the quarantined document: `aws s3 ls s3://[bucket]/quarantine/`
3. Determine the original S3 prefix from the document key or registry
4. Check if permission mapping exists for that prefix: `aws dynamodb scan` on `doc-permission-mappings`
5. If missing: create the permission mapping (identify appropriate Entra group, sensitivity level)
6. Move document from quarantine to correct prefix: `aws s3 mv`
7. Re-trigger ingestion (Textract or direct extract) by invoking textract-trigger Lambda
8. Verify document appears in knowledge base search results

**Step 2: Write INVESTIGATE_PERMISSION_DENIAL.md**

Content should cover:
1. Identify the affected user and their query from CloudWatch Logs Insights
2. Look up user's groups in DynamoDB `user-group-cache`: `aws dynamodb get-item`
3. Look up permission mappings for the document's S3 prefix: `aws dynamodb scan`
4. Compare user's groups with the mapping's `allowed_groups`
5. Common causes: user missing from Entra group, SCIM not synced, stale cache, missing mapping
6. Resolution steps for each cause
7. Verify fix: re-run query and check audit log for `result_type: "success"`

**Step 3: Write EMERGENCY_REVOKE_ACCESS.md**

Content should cover:
1. **Immediate action**: Truncate the user-group-cache table entries for affected users
2. **Optional**: Disable the API key in api-authorizer environment variables
3. **If IAM Identity Center compromise**: Disable user in Entra ID, wait for SCIM propagation
4. Invoke group-cache-refresh to clear stale data
5. Audit recent queries: CloudWatch Logs Insights query for the user_id
6. Generate ad-hoc compliance report: invoke compliance-report Lambda manually
7. Post-incident: review drift report, check for unauthorized document access

**Step 4: Commit**

```bash
git add docs/runbooks/HANDLE_QUARANTINED_DOCUMENT.md docs/runbooks/INVESTIGATE_PERMISSION_DENIAL.md docs/runbooks/EMERGENCY_REVOKE_ACCESS.md
git commit -m "docs: add runbooks for quarantine handling, permission denial, emergency revocation"
```

---

### Task 10: README.md

**Files:**
- Create/Rewrite: `docs/README.md`

**Context:**
- Full project README with architecture diagram, component reference, environment variables, quick start, links to runbooks
- Reference all Lambdas: daily-sync, textract-trigger, textract-complete, group-cache-refresh, permission-drift-detector, stale-account-cleanup, compliance-report-generator, query-handler (conditional), api-authorizer (conditional)
- DynamoDB tables: delta-tokens, doc-registry, doc-permission-mappings, user-group-cache
- S3 prefixes: source/, twins/, quarantine/, governance-reports/
- EventBridge schedules: daily sync (24h), SCIM refresh (15 min), drift detector (weekly Sunday), stale cleanup (daily 03:00), compliance report (monthly 1st)
- Monitoring: CloudWatch dashboard `SP-Ingest-Pipeline`, SNS alerts, alarms
- ASCII architecture diagram covering: SharePoint → daily sync → S3 → Textract → Knowledge Base → Query API → Open WebUI
- Link to all 7 runbooks
- Deployment reference: link to `docs/DEPLOYMENT_CHECKLIST.md`

**Step 1: Write docs/README.md**

Full README with sections:
1. Overview (what this system does)
2. Architecture Diagram (ASCII art showing all components and data flows)
3. Component Reference (table of all Lambdas with trigger, purpose, env vars)
4. DynamoDB Tables (table of all tables with key schema, purpose)
5. S3 Bucket Structure (prefix tree with descriptions)
6. Environment Variables (table of all env vars across all Lambdas)
7. Monitoring & Alerting (dashboard URL pattern, alarm summary table)
8. Quick Start (link to deployment checklist, basic verification steps)
9. Operational Runbooks (links to all 7 runbooks)
10. Development (local setup, testing commands, deployment)

**Step 2: Commit**

```bash
git add docs/README.md
git commit -m "docs: add comprehensive project README with architecture and component reference"
```

---

### Task 11: Deployment Checklist

**Files:**
- Create: `docs/DEPLOYMENT_CHECKLIST.md`

**Context:**
- Ordered steps from scratch to production
- References Terraform, Lambda deployment, SCIM setup, monitoring verification
- Links to existing `docs/ENTRA_ID_SCIM_SETUP_RUNBOOK.md`
- Includes verification steps at each stage

**Step 1: Write docs/DEPLOYMENT_CHECKLIST.md**

Sections:
1. **Prerequisites**: AWS account, Entra ID, Terraform, Python 3.11, Azure app registration
2. **Infrastructure Deployment**: `terraform init`, `terraform plan`, `terraform apply`
3. **Lambda Code Deployment**: Build layer, package code, update Lambda functions
4. **SCIM Configuration**: Follow `docs/ENTRA_ID_SCIM_SETUP_RUNBOOK.md`
5. **Initial Data Load**: Configure SharePoint sites, run bulk ingestion
6. **Bedrock Knowledge Base**: Create knowledge base, configure data source, sync
7. **WebUI Deployment** (optional): Set `enable_webui = true`, configure API keys
8. **Monitoring Verification**: Check dashboard, verify alarms, test SNS delivery
9. **Production Go-Live**: Final checklist — all Lambdas invoked at least once, no alarms firing, drift report clean

Each step should include exact commands and expected outputs.

**Step 2: Commit**

```bash
git add docs/DEPLOYMENT_CHECKLIST.md
git commit -m "docs: add ordered deployment checklist from scratch to production"
```

---

### Task 12: Full Validation

**Files:** None (validation only)

**Context:**
- Run the full test suite to confirm nothing is broken
- Validate Terraform configuration
- Verify all deliverables from the design doc

**Step 1: Run compliance report tests**

Run: `python -m pytest tests/test_compliance_report.py -v --tb=short`
Expected: 6 tests PASS

**Step 2: Run full test suite**

Run: `python -m pytest tests/ --ignore=tests/integration -v --tb=short 2>&1 | tail -20`
Expected: All tests pass (6 pre-existing failures in test_file_converter.py and test_path_mapper.py are acceptable)

**Step 3: Terraform validate**

Run: `cd terraform && terraform validate`
Expected: `Success! The configuration is valid.`

**Step 4: Verify all deliverables**

Checklist:
- [ ] `src/compliance_report_generator.py` exists with handler function
- [ ] `tests/test_compliance_report.py` exists with 6+ tests
- [ ] `terraform/lambda_compliance.tf` exists with Lambda + EventBridge + DLQ
- [ ] `terraform/iam_compliance.tf` exists with IAM role + policies
- [ ] `terraform/monitoring.tf` has new metric filters (5), dashboard rows (3), alarms (8)
- [ ] `terraform/outputs.tf` has compliance_report_lambda_arn output
- [ ] `docs/runbooks/ADD_NEW_DEPARTMENT.md` exists
- [ ] `docs/runbooks/ONBOARD_NEW_USER.md` exists
- [ ] `docs/runbooks/OFFBOARD_USER.md` exists
- [ ] `docs/runbooks/ADD_NEW_DOCUMENT_LIBRARY.md` exists
- [ ] `docs/runbooks/HANDLE_QUARANTINED_DOCUMENT.md` exists
- [ ] `docs/runbooks/INVESTIGATE_PERMISSION_DENIAL.md` exists
- [ ] `docs/runbooks/EMERGENCY_REVOKE_ACCESS.md` exists
- [ ] `docs/README.md` exists with architecture diagram
- [ ] `docs/DEPLOYMENT_CHECKLIST.md` exists with ordered steps
- [ ] Terraform validates successfully
- [ ] All new tests pass

**Step 5: Final commit (if any fixes needed)**

```bash
git add -A
git commit -m "chore: final validation and fixes for monitoring + compliance + runbooks"
```
