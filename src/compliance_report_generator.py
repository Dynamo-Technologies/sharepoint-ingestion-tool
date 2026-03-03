"""compliance-report-generator Lambda — monthly compliance report.

Triggered by EventBridge on the 1st of each month. Aggregates audit data
from CloudWatch Logs Insights, S3 quarantine listings, SCIM sync metrics,
group membership changes, and permission drift reports into a consolidated
compliance report (JSON + Markdown) written to S3 and summarised via SNS.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone, timedelta

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))


def handler(event: dict, context: object) -> dict:
    """EventBridge-triggered handler: generate monthly compliance report."""
    bucket = os.environ["S3_BUCKET"]
    sns_topic = os.getenv("GOVERNANCE_ALERTS_TOPIC_ARN", "")
    query_log_group = os.getenv("QUERY_HANDLER_LOG_GROUP", "/aws/lambda/sp-ingest-query-handler")
    group_cache_log_group = os.getenv(
        "GROUP_CACHE_LOG_GROUP", "/aws/lambda/sp-ingest-group-cache-refresh"
    )
    region = os.getenv("AWS_REGION_NAME", os.getenv("AWS_REGION", "us-east-1"))

    s3 = boto3.client("s3", region_name=region)
    logs = boto3.client("logs", region_name=region)
    cw = boto3.client("cloudwatch", region_name=region)
    sns = boto3.client("sns", region_name=region)

    # Report covers the previous calendar month
    now = datetime.now(timezone.utc)
    first_of_current = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_month_end = first_of_current - timedelta(seconds=1)
    first_of_last = last_month_end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    period_start = first_of_last
    period_end = first_of_current
    period_label = first_of_last.strftime("%Y-%m")

    # 1. Query statistics from CloudWatch Logs Insights
    query_stats = _get_query_stats(logs, query_log_group, period_start, period_end)

    # 2. Quarantined documents from S3
    quarantine_info = _get_quarantine_info(s3, bucket)

    # 3. SCIM sync uptime from CloudWatch metrics
    scim_sync = _get_scim_sync_stats(cw, group_cache_log_group, period_start, period_end)

    # 4. Group membership changes from logs
    group_changes = _get_group_changes(logs, group_cache_log_group, period_start, period_end)

    # 5. Latest drift report from S3
    drift_summary = _get_latest_drift_report(s3, bucket)

    # Assemble report
    report = {
        "report_period": period_label,
        "generated_at": now.isoformat(),
        "query_stats": query_stats,
        "quarantined_documents": quarantine_info,
        "scim_sync": scim_sync,
        "group_changes": group_changes,
        "drift_summary": drift_summary,
    }

    # Write JSON report
    json_key = f"governance-reports/compliance-{period_label}.json"
    s3.put_object(
        Bucket=bucket,
        Key=json_key,
        Body=json.dumps(report, indent=2),
        ContentType="application/json",
    )
    logger.info("JSON report written to s3://%s/%s", bucket, json_key)

    # Write Markdown report
    md_key = f"governance-reports/compliance-{period_label}.md"
    md_content = _render_markdown(report)
    s3.put_object(
        Bucket=bucket,
        Key=md_key,
        Body=md_content.encode("utf-8"),
        ContentType="text/markdown",
    )
    logger.info("Markdown report written to s3://%s/%s", bucket, md_key)

    # Publish SNS summary
    if sns_topic:
        summary_msg = _build_sns_summary(report)
        sns.publish(
            TopicArn=sns_topic,
            Subject=f"Compliance Report — {period_label}",
            Message=summary_msg,
        )
        logger.info("SNS summary published to %s", sns_topic)

    # Return summary in response body
    response_body = {
        "report_period": period_label,
        "query_stats": {
            "total_queries": query_stats["total_queries"],
            "unique_users": query_stats["unique_users"],
        },
        "quarantined_documents": quarantine_info["count"],
        "scim_sync": scim_sync,
        "s3_json_key": json_key,
        "s3_md_key": md_key,
    }

    return {"statusCode": 200, "body": json.dumps(response_body)}


# ---------------------------------------------------------------------------
# Data-gathering helpers
# ---------------------------------------------------------------------------


def _get_query_stats(
    logs_client, log_group: str, start: datetime, end: datetime
) -> dict:
    """Query CloudWatch Logs Insights for query handler statistics."""
    try:
        query = (
            "stats count(*) as total_queries, "
            "count_distinct(user_id) as unique_users, "
            "sum(result_type = 'no_results') as no_results_count, "
            "avg(response_latency_ms) as avg_latency_ms"
        )
        resp = logs_client.start_query(
            logGroupName=log_group,
            startTime=int(start.timestamp()),
            endTime=int(end.timestamp()),
            queryString=query,
        )
        results = logs_client.get_query_results(queryId=resp["queryId"])
        return _parse_query_stats(results)
    except Exception:
        logger.exception("Failed to query CloudWatch Logs for query stats")
        return _empty_query_stats()


def _parse_query_stats(results: dict) -> dict:
    """Parse CloudWatch Logs Insights results into query stats dict."""
    rows = results.get("results", [])
    if not rows:
        return _empty_query_stats()

    fields = {f["field"]: f["value"] for f in rows[0]}
    return {
        "total_queries": int(float(fields.get("total_queries", "0"))),
        "unique_users": int(float(fields.get("unique_users", "0"))),
        "no_results_count": int(float(fields.get("no_results_count", "0"))),
        "avg_latency_ms": round(float(fields.get("avg_latency_ms", "0")), 1),
    }


def _empty_query_stats() -> dict:
    return {
        "total_queries": 0,
        "unique_users": 0,
        "no_results_count": 0,
        "avg_latency_ms": 0.0,
    }


def _get_quarantine_info(s3_client, bucket: str) -> dict:
    """List quarantined documents under quarantine/ prefix."""
    keys: list[str] = []
    try:
        paginator = s3_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix="quarantine/"):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])
    except Exception:
        logger.exception("Failed to list quarantined documents")

    return {"count": len(keys), "keys": keys}


def _get_scim_sync_stats(
    cw_client, function_name: str, start: datetime, end: datetime
) -> dict:
    """Get SCIM sync (group-cache-refresh) invocation and error counts."""
    try:
        invocations = cw_client.get_metric_statistics(
            Namespace="AWS/Lambda",
            MetricName="Invocations",
            Dimensions=[{"Name": "FunctionName", "Value": function_name}],
            StartTime=start,
            EndTime=end,
            Period=int((end - start).total_seconds()),
            Statistics=["Sum"],
        )
        errors = cw_client.get_metric_statistics(
            Namespace="AWS/Lambda",
            MetricName="Errors",
            Dimensions=[{"Name": "FunctionName", "Value": function_name}],
            StartTime=start,
            EndTime=end,
            Period=int((end - start).total_seconds()),
            Statistics=["Sum"],
        )

        inv_count = _sum_datapoints(invocations)
        err_count = _sum_datapoints(errors)
        success_rate = ((inv_count - err_count) / inv_count * 100) if inv_count > 0 else 0.0

        return {
            "invocations": int(inv_count),
            "errors": int(err_count),
            "success_rate_pct": round(success_rate, 1),
        }
    except Exception:
        logger.exception("Failed to get SCIM sync metrics")
        return {"invocations": 0, "errors": 0, "success_rate_pct": 0.0}


def _sum_datapoints(metric_response: dict) -> float:
    """Sum the 'Sum' values across all datapoints."""
    total = 0.0
    for dp in metric_response.get("Datapoints", []):
        total += dp.get("Sum", 0)
    return total


def _get_group_changes(
    logs_client, log_group: str, start: datetime, end: datetime
) -> dict:
    """Query group-cache-refresh logs for group membership change stats."""
    try:
        query = (
            "stats count(*) as refresh_count, "
            "sum(added_groups_total) as added_groups_total, "
            "sum(removed_groups_total) as removed_groups_total"
        )
        resp = logs_client.start_query(
            logGroupName=log_group,
            startTime=int(start.timestamp()),
            endTime=int(end.timestamp()),
            queryString=query,
        )
        results = logs_client.get_query_results(queryId=resp["queryId"])
        return _parse_group_changes(results)
    except Exception:
        logger.exception("Failed to query group change logs")
        return _empty_group_changes()


def _parse_group_changes(results: dict) -> dict:
    """Parse CloudWatch Logs Insights results into group changes dict."""
    rows = results.get("results", [])
    if not rows:
        return _empty_group_changes()

    fields = {f["field"]: f["value"] for f in rows[0]}
    return {
        "refresh_count": int(float(fields.get("refresh_count", "0"))),
        "added_groups": int(float(fields.get("added_groups_total", "0"))),
        "removed_groups": int(float(fields.get("removed_groups_total", "0"))),
    }


def _empty_group_changes() -> dict:
    return {"refresh_count": 0, "added_groups": 0, "removed_groups": 0}


def _get_latest_drift_report(s3_client, bucket: str) -> dict:
    """Get the most recent drift report from S3."""
    try:
        resp = s3_client.list_objects_v2(
            Bucket=bucket, Prefix="governance-reports/drift-report-"
        )
        contents = resp.get("Contents", [])
        if not contents:
            return {"available": False}

        latest = sorted(contents, key=lambda o: o["Key"], reverse=True)[0]
        body = s3_client.get_object(Bucket=bucket, Key=latest["Key"])["Body"].read()
        drift = json.loads(body)

        return {
            "available": True,
            "report_date": drift.get("report_date", "unknown"),
            "unmapped_prefixes": drift.get("summary", {}).get("unmapped_prefixes", 0),
            "stale_mappings": drift.get("summary", {}).get("stale_mappings", 0),
            "orphaned_groups": drift.get("summary", {}).get("orphaned_groups", 0),
        }
    except Exception:
        logger.exception("Failed to read drift report")
        return {"available": False}


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------


def _render_markdown(report: dict) -> str:
    """Render the compliance report as Markdown."""
    qs = report["query_stats"]
    qd = report["quarantined_documents"]
    sc = report["scim_sync"]
    gc = report["group_changes"]
    ds = report["drift_summary"]

    lines = [
        f"# Compliance Report — {report['report_period']}",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "## Query Statistics",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Total queries | {qs['total_queries']} |",
        f"| Unique users | {qs['unique_users']} |",
        f"| No-results queries | {qs['no_results_count']} |",
        f"| Avg latency (ms) | {qs['avg_latency_ms']} |",
        "",
        "## Quarantined Documents",
        "",
        f"**Total quarantined:** {qd['count']}",
        "",
    ]

    if qd["keys"]:
        lines.append("| Document Key |")
        lines.append("|-------------|")
        for key in qd["keys"]:
            lines.append(f"| {key} |")
        lines.append("")

    lines.extend([
        "## SCIM Sync Status",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Invocations | {sc['invocations']} |",
        f"| Errors | {sc['errors']} |",
        f"| Success rate | {sc['success_rate_pct']}% |",
        "",
        "## Group Membership Changes",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Cache refresh count | {gc['refresh_count']} |",
        f"| Groups added | {gc['added_groups']} |",
        f"| Groups removed | {gc['removed_groups']} |",
        "",
        "## Permission Drift Summary",
        "",
    ])

    if ds.get("available"):
        lines.extend([
            f"Latest drift report: {ds.get('report_date', 'N/A')}",
            "",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Unmapped prefixes | {ds['unmapped_prefixes']} |",
            f"| Stale mappings | {ds['stale_mappings']} |",
            f"| Orphaned groups | {ds['orphaned_groups']} |",
        ])
    else:
        lines.append("No drift report available for this period.")

    lines.append("")
    return "\n".join(lines)


def _build_sns_summary(report: dict) -> str:
    """Build a concise text summary for SNS notification."""
    qs = report["query_stats"]
    qd = report["quarantined_documents"]
    sc = report["scim_sync"]
    ds = report["drift_summary"]

    parts = [
        f"Compliance Report for {report['report_period']}",
        "",
        f"Queries: {qs['total_queries']} total, {qs['unique_users']} unique users, "
        f"{qs['no_results_count']} no-results",
        f"Avg latency: {qs['avg_latency_ms']}ms",
        f"Quarantined documents: {qd['count']}",
        f"SCIM sync: {sc['invocations']} invocations, {sc['success_rate_pct']}% success",
    ]

    if ds.get("available"):
        parts.append(
            f"Drift: {ds['unmapped_prefixes']} unmapped, "
            f"{ds['stale_mappings']} stale, {ds['orphaned_groups']} orphaned"
        )

    parts.append(f"\nFull report: governance-reports/compliance-{report['report_period']}.json")
    return "\n".join(parts)
