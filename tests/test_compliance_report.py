"""Tests for compliance-report-generator Lambda handler."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import boto3
import moto
import pytest

BUCKET = "test-documents"
SNS_TOPIC = "arn:aws:sns:us-east-1:123456789012:test-governance-alerts"
QUERY_LOG_GROUP = "/aws/lambda/sp-ingest-query-handler"
GROUP_CACHE_LOG_GROUP = "/aws/lambda/sp-ingest-group-cache-refresh"


@pytest.fixture
def _env(monkeypatch):
    monkeypatch.setenv("S3_BUCKET", BUCKET)
    monkeypatch.setenv("USER_GROUP_CACHE_TABLE", "test-user-group-cache")
    monkeypatch.setenv("PERMISSION_MAPPINGS_TABLE", "test-doc-permission-mappings")
    monkeypatch.setenv("GOVERNANCE_ALERTS_TOPIC_ARN", SNS_TOPIC)
    monkeypatch.setenv("QUERY_HANDLER_LOG_GROUP", QUERY_LOG_GROUP)
    monkeypatch.setenv("GROUP_CACHE_LOG_GROUP", GROUP_CACHE_LOG_GROUP)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_REGION_NAME", "us-east-1")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")


@pytest.fixture
def aws_resources(_env):
    with moto.mock_aws():
        region = "us-east-1"

        # S3
        s3 = boto3.client("s3", region_name=region)
        s3.create_bucket(Bucket=BUCKET)

        # SNS
        sns = boto3.client("sns", region_name=region)
        sns.create_topic(Name="test-governance-alerts")

        yield {"s3": s3, "sns": sns}


def _make_logs_response(results):
    """Build a CloudWatch Logs Insights query result structure."""
    return {
        "results": results,
        "statistics": {"recordsMatched": len(results)},
        "status": "Complete",
    }


def _seed_quarantine_docs(s3, count):
    """Put dummy objects under the quarantine/ prefix."""
    for i in range(count):
        s3.put_object(Bucket=BUCKET, Key=f"quarantine/doc_{i}.pdf", Body=b"data")


def _seed_drift_report(s3, report_date="2026-02-15"):
    """Put a drift report in governance-reports/."""
    drift = {
        "report_date": report_date,
        "summary": {
            "unmapped_prefixes": 2,
            "stale_mappings": 1,
            "orphaned_groups": 0,
        },
        "unmapped_prefixes": ["source/A", "source/B"],
        "stale_mappings": ["source/Old"],
        "orphaned_groups": [],
    }
    s3.put_object(
        Bucket=BUCKET,
        Key=f"governance-reports/drift-report-{report_date}.json",
        Body=json.dumps(drift),
        ContentType="application/json",
    )
    return drift


def _mock_cw_logs_client(query_stats=None, group_changes=None):
    """Create a mock CloudWatch Logs client with start/get query support."""
    mock = MagicMock()
    mock.start_query.return_value = {"queryId": "test-query-id"}

    # Default query stats response
    if query_stats is None:
        query_stats = _make_logs_response([
            [
                {"field": "total_queries", "value": "150"},
                {"field": "unique_users", "value": "12"},
                {"field": "no_results_count", "value": "5"},
                {"field": "avg_latency_ms", "value": "234.5"},
            ]
        ])

    # Default group changes response
    if group_changes is None:
        group_changes = _make_logs_response([
            [
                {"field": "refresh_count", "value": "60"},
                {"field": "added_groups_total", "value": "8"},
                {"field": "removed_groups_total", "value": "3"},
            ]
        ])

    # Return query stats first, then group changes on second call
    mock.get_query_results.side_effect = [query_stats, group_changes]
    return mock


def _mock_cw_client(invocation_count=120, error_count=2):
    """Create a mock CloudWatch (metrics) client for SCIM sync uptime."""
    mock = MagicMock()
    mock.get_metric_statistics.side_effect = [
        {
            "Datapoints": [{"Sum": invocation_count}],
        },
        {
            "Datapoints": [{"Sum": error_count}],
        },
    ]
    return mock


class TestComplianceReportGenerator:

    @patch("compliance_report_generator.boto3")
    def test_handler_returns_200_with_report_summary(self, mock_boto3, aws_resources):
        """Handler generates report and returns 200 with summary body."""
        s3 = aws_resources["s3"]
        _seed_quarantine_docs(s3, 3)
        _seed_drift_report(s3)

        mock_boto3.client.side_effect = lambda svc, **kw: {
            "s3": s3,
            "sns": aws_resources["sns"],
            "logs": _mock_cw_logs_client(),
            "cloudwatch": _mock_cw_client(),
        }[svc]

        from compliance_report_generator import handler

        result = handler({}, None)

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert "query_stats" in body
        assert "quarantined_documents" in body
        assert "scim_sync" in body
        assert "report_period" in body

    @patch("compliance_report_generator.boto3")
    def test_report_json_written_to_s3(self, mock_boto3, aws_resources):
        """Handler writes JSON report to governance-reports/ with expected structure."""
        s3 = aws_resources["s3"]
        _seed_quarantine_docs(s3, 2)
        _seed_drift_report(s3)

        mock_boto3.client.side_effect = lambda svc, **kw: {
            "s3": s3,
            "sns": aws_resources["sns"],
            "logs": _mock_cw_logs_client(),
            "cloudwatch": _mock_cw_client(),
        }[svc]

        from compliance_report_generator import handler

        handler({}, None)

        # Find the JSON report in S3
        objs = s3.list_objects_v2(Bucket=BUCKET, Prefix="governance-reports/compliance-")
        json_keys = [o["Key"] for o in objs.get("Contents", []) if o["Key"].endswith(".json")]
        assert len(json_keys) == 1, f"Expected 1 JSON report, found {json_keys}"

        report_body = s3.get_object(Bucket=BUCKET, Key=json_keys[0])["Body"].read()
        report = json.loads(report_body)

        # Verify all required sections
        assert "query_stats" in report
        assert "quarantined_documents" in report
        assert "scim_sync" in report
        assert "group_changes" in report
        assert "drift_summary" in report

        # Verify query stats structure
        assert report["query_stats"]["total_queries"] == 150
        assert report["query_stats"]["unique_users"] == 12

        # Verify quarantine count
        assert report["quarantined_documents"]["count"] == 2

    @patch("compliance_report_generator.boto3")
    def test_report_markdown_written_to_s3(self, mock_boto3, aws_resources):
        """Handler writes Markdown report to governance-reports/."""
        s3 = aws_resources["s3"]
        _seed_drift_report(s3)

        mock_boto3.client.side_effect = lambda svc, **kw: {
            "s3": s3,
            "sns": aws_resources["sns"],
            "logs": _mock_cw_logs_client(),
            "cloudwatch": _mock_cw_client(),
        }[svc]

        from compliance_report_generator import handler

        handler({}, None)

        objs = s3.list_objects_v2(Bucket=BUCKET, Prefix="governance-reports/compliance-")
        md_keys = [o["Key"] for o in objs.get("Contents", []) if o["Key"].endswith(".md")]
        assert len(md_keys) == 1, f"Expected 1 Markdown report, found {md_keys}"

        md_body = s3.get_object(Bucket=BUCKET, Key=md_keys[0])["Body"].read().decode()
        assert "# Compliance Report" in md_body
        assert "Query Statistics" in md_body
        assert "Quarantined Documents" in md_body

    @patch("compliance_report_generator.boto3")
    def test_sns_summary_published(self, mock_boto3, aws_resources):
        """Handler publishes summary to governance-alerts SNS topic."""
        s3 = aws_resources["s3"]
        _seed_drift_report(s3)

        mock_sns = MagicMock()
        mock_boto3.client.side_effect = lambda svc, **kw: {
            "s3": s3,
            "sns": mock_sns,
            "logs": _mock_cw_logs_client(),
            "cloudwatch": _mock_cw_client(),
        }[svc]

        from compliance_report_generator import handler

        handler({}, None)

        mock_sns.publish.assert_called_once()
        call_kwargs = mock_sns.publish.call_args[1]
        assert call_kwargs["TopicArn"] == SNS_TOPIC
        assert "Compliance Report" in call_kwargs["Subject"]
        assert len(call_kwargs["Message"]) > 0

    @patch("compliance_report_generator.boto3")
    def test_quarantine_listing(self, mock_boto3, aws_resources):
        """Handler counts quarantined documents from S3."""
        s3 = aws_resources["s3"]
        _seed_quarantine_docs(s3, 5)
        _seed_drift_report(s3)

        mock_boto3.client.side_effect = lambda svc, **kw: {
            "s3": s3,
            "sns": aws_resources["sns"],
            "logs": _mock_cw_logs_client(),
            "cloudwatch": _mock_cw_client(),
        }[svc]

        from compliance_report_generator import handler

        result = handler({}, None)
        body = json.loads(result["body"])

        assert body["quarantined_documents"] == 5

        # Also verify the JSON report
        objs = s3.list_objects_v2(Bucket=BUCKET, Prefix="governance-reports/compliance-")
        json_keys = [o["Key"] for o in objs.get("Contents", []) if o["Key"].endswith(".json")]
        report_body = s3.get_object(Bucket=BUCKET, Key=json_keys[0])["Body"].read()
        report = json.loads(report_body)
        assert report["quarantined_documents"]["count"] == 5
        assert len(report["quarantined_documents"]["keys"]) == 5

    @patch("compliance_report_generator.boto3")
    def test_no_query_logs_graceful(self, mock_boto3, aws_resources):
        """Handler handles zero query logs gracefully."""
        s3 = aws_resources["s3"]

        # Empty query stats — no log entries
        empty_stats = _make_logs_response([])
        empty_group_changes = _make_logs_response([])

        mock_boto3.client.side_effect = lambda svc, **kw: {
            "s3": s3,
            "sns": aws_resources["sns"],
            "logs": _mock_cw_logs_client(
                query_stats=empty_stats,
                group_changes=empty_group_changes,
            ),
            "cloudwatch": _mock_cw_client(invocation_count=0, error_count=0),
        }[svc]

        from compliance_report_generator import handler

        result = handler({}, None)

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["query_stats"]["total_queries"] == 0
        assert body["query_stats"]["unique_users"] == 0
