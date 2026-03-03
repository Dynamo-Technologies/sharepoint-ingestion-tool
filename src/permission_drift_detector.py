"""permission-drift-detector Lambda — find unmapped S3 prefixes and stale mappings.

Triggered by EventBridge weekly (Sunday 02:00 UTC). Compares S3 prefixes
under source/ against the doc-permission-mappings DynamoDB table and
validates group IDs against the Identity Store.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

import boto3

try:
    from lib.identity_store.client import IdentityStoreClient
except ImportError:
    from identity_store.client import IdentityStoreClient  # type: ignore[no-redef]

logger = logging.getLogger(__name__)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))


def handler(event: dict, context: object) -> dict:
    """EventBridge-triggered handler: detect permission mapping drift."""
    identity_store_id = os.environ["IDENTITY_STORE_ID"]
    bucket = os.environ["S3_BUCKET"]
    perm_table_name = os.getenv("PERMISSION_MAPPINGS_TABLE", "doc-permission-mappings")
    sns_topic = os.getenv("GOVERNANCE_ALERTS_TOPIC_ARN", "")
    region = os.getenv("AWS_REGION_NAME", os.getenv("AWS_REGION", "us-east-1"))

    s3 = boto3.client("s3", region_name=region)
    dynamodb = boto3.resource("dynamodb", region_name=region)
    table = dynamodb.Table(perm_table_name)
    id_client = IdentityStoreClient(identity_store_id=identity_store_id)

    # 1. Enumerate S3 prefixes
    s3_prefixes = _enumerate_s3_prefixes(s3, bucket, "source/")

    # 2. Scan permission mappings
    mapped_prefixes: set[str] = set()
    all_mapping_groups: set[str] = set()
    for item in _scan_all(table):
        mapped_prefixes.add(item["s3_prefix"])
        for g in item.get("allowed_groups", []):
            all_mapping_groups.add(g)

    # 3. Validate groups against Identity Store
    identity_groups = {g["GroupId"] for g in id_client.list_groups()}

    # 4. Identify drift
    unmapped = sorted(s3_prefixes - mapped_prefixes)
    stale = sorted(mapped_prefixes - s3_prefixes)
    orphaned = sorted(all_mapping_groups - identity_groups)

    report = {
        "report_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "summary": {
            "total_s3_prefixes": len(s3_prefixes),
            "mapped_prefixes": len(s3_prefixes & mapped_prefixes),
            "unmapped_prefixes": len(unmapped),
            "stale_mappings": len(stale),
            "orphaned_groups": len(orphaned),
        },
        "unmapped_prefixes": unmapped,
        "stale_mappings": stale,
        "orphaned_groups": orphaned,
    }

    # 5. Write report to S3
    report_key = f"governance-reports/drift-report-{report['report_date']}.json"
    s3.put_object(
        Bucket=bucket,
        Key=report_key,
        Body=json.dumps(report, indent=2),
        ContentType="application/json",
    )
    logger.info("Drift report written to s3://%s/%s", bucket, report_key)

    # 6. SNS alert if unmapped prefixes found
    if unmapped and sns_topic:
        sns = boto3.client("sns", region_name=region)
        sns.publish(
            TopicArn=sns_topic,
            Subject="Permission Drift Alert: Unmapped S3 Prefixes",
            Message=json.dumps({
                "unmapped_prefixes": unmapped,
                "report_s3_key": report_key,
            }),
        )
        logger.info("SNS alert sent for %d unmapped prefixes", len(unmapped))

    return {"statusCode": 200, "body": json.dumps(report["summary"])}


def _enumerate_s3_prefixes(s3_client, bucket: str, root: str) -> set[str]:
    """Recursively enumerate leaf S3 directory prefixes under *root*.

    A leaf prefix is one that contains objects but has no sub-prefixes.
    This matches how permission mappings target specific folder paths
    (e.g. ``source/Dynamo/HR``) rather than intermediate directories.
    """
    prefixes: set[str] = set()
    paginator = s3_client.get_paginator("list_objects_v2")

    def _walk(prefix: str) -> None:
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix, Delimiter="/"):
            children = page.get("CommonPrefixes", [])
            if children:
                for cp in children:
                    _walk(cp["Prefix"])
            else:
                # Leaf prefix — no sub-directories
                clean = prefix.rstrip("/")
                if clean != root.rstrip("/"):
                    prefixes.add(clean)

    _walk(root)
    return prefixes


def _scan_all(table) -> list[dict]:
    """Paginate through a full DynamoDB scan."""
    items: list[dict] = []
    kwargs: dict = {}
    while True:
        response = table.scan(**kwargs)
        items.extend(response.get("Items", []))
        if "LastEvaluatedKey" not in response:
            break
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
    return items
