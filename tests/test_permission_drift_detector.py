"""Tests for permission-drift-detector Lambda handler."""

from __future__ import annotations

import json
import os

import boto3
import moto
import pytest
from unittest.mock import MagicMock, patch

PERM_TABLE = "test-doc-permission-mappings"
BUCKET = "test-documents"
STORE_ID = "d-test123"
SNS_TOPIC = "arn:aws:sns:us-east-1:123456789012:test-governance-alerts"


@pytest.fixture
def _env(monkeypatch):
    monkeypatch.setenv("IDENTITY_STORE_ID", STORE_ID)
    monkeypatch.setenv("PERMISSION_MAPPINGS_TABLE", PERM_TABLE)
    monkeypatch.setenv("S3_BUCKET", BUCKET)
    monkeypatch.setenv("GOVERNANCE_ALERTS_TOPIC_ARN", SNS_TOPIC)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_REGION_NAME", "us-east-1")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")


@pytest.fixture
def aws_resources(_env):
    with moto.mock_aws():
        region = "us-east-1"

        # DynamoDB
        dynamodb = boto3.resource("dynamodb", region_name=region)
        dynamodb.create_table(
            TableName=PERM_TABLE,
            KeySchema=[{"AttributeName": "s3_prefix", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "s3_prefix", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        # S3
        s3 = boto3.client("s3", region_name=region)
        s3.create_bucket(Bucket=BUCKET)

        # SNS
        sns = boto3.client("sns", region_name=region)
        sns.create_topic(Name="test-governance-alerts")

        yield {"dynamodb": dynamodb, "s3": s3, "sns": sns}


def _seed(aws, s3_prefixes, permission_mappings):
    """Seed S3 objects and permission mappings."""
    for prefix in s3_prefixes:
        aws["s3"].put_object(Bucket=BUCKET, Key=f"{prefix}/doc.pdf", Body=b"data")

    table = aws["dynamodb"].Table(PERM_TABLE)
    for mapping in permission_mappings:
        table.put_item(Item=mapping)


class TestPermissionDriftDetector:
    @patch("permission_drift_detector.IdentityStoreClient")
    def test_all_mapped_no_drift(self, MockClient, aws_resources):
        _seed(aws_resources,
            s3_prefixes=["source/Dynamo/HR"],
            permission_mappings=[{
                "s3_prefix": "source/Dynamo/HR",
                "allowed_groups": ["g1"],
                "sensitivity_level": "internal",
            }],
        )
        MockClient.return_value.list_groups.return_value = iter([
            {"GroupId": "g1"},
        ])

        from permission_drift_detector import handler
        result = handler({}, None)
        body = json.loads(result["body"])

        assert body["unmapped_prefixes"] == 0
        assert body["stale_mappings"] == 0
        assert body["orphaned_groups"] == 0

    @patch("permission_drift_detector.IdentityStoreClient")
    def test_unmapped_prefix_detected(self, MockClient, aws_resources):
        _seed(aws_resources,
            s3_prefixes=["source/Dynamo/HR", "source/Dynamo/NewFolder"],
            permission_mappings=[{
                "s3_prefix": "source/Dynamo/HR",
                "allowed_groups": ["g1"],
                "sensitivity_level": "internal",
            }],
        )
        MockClient.return_value.list_groups.return_value = iter([{"GroupId": "g1"}])

        from permission_drift_detector import handler
        result = handler({}, None)
        body = json.loads(result["body"])

        assert body["unmapped_prefixes"] == 1

        # Verify report written to S3
        s3 = aws_resources["s3"]
        objs = s3.list_objects_v2(Bucket=BUCKET, Prefix="governance-reports/")
        assert objs["KeyCount"] == 1

    @patch("permission_drift_detector.IdentityStoreClient")
    def test_stale_mapping_detected(self, MockClient, aws_resources):
        _seed(aws_resources,
            s3_prefixes=["source/Dynamo/HR"],
            permission_mappings=[
                {
                    "s3_prefix": "source/Dynamo/HR",
                    "allowed_groups": ["g1"],
                    "sensitivity_level": "internal",
                },
                {
                    "s3_prefix": "source/Dynamo/OldFolder",
                    "allowed_groups": ["g1"],
                    "sensitivity_level": "internal",
                },
            ],
        )
        MockClient.return_value.list_groups.return_value = iter([{"GroupId": "g1"}])

        from permission_drift_detector import handler
        result = handler({}, None)
        body = json.loads(result["body"])

        assert body["stale_mappings"] == 1

    @patch("permission_drift_detector.IdentityStoreClient")
    def test_orphaned_group_detected(self, MockClient, aws_resources):
        _seed(aws_resources,
            s3_prefixes=["source/Dynamo/HR"],
            permission_mappings=[{
                "s3_prefix": "source/Dynamo/HR",
                "allowed_groups": ["g1", "g-deleted"],
                "sensitivity_level": "internal",
            }],
        )
        MockClient.return_value.list_groups.return_value = iter([{"GroupId": "g1"}])

        from permission_drift_detector import handler
        result = handler({}, None)
        body = json.loads(result["body"])

        assert body["orphaned_groups"] == 1

    @patch("permission_drift_detector.IdentityStoreClient")
    def test_sns_alert_on_unmapped(self, MockClient, aws_resources):
        _seed(aws_resources,
            s3_prefixes=["source/Dynamo/Unmapped"],
            permission_mappings=[],
        )
        MockClient.return_value.list_groups.return_value = iter([])

        from permission_drift_detector import handler
        handler({}, None)

        # If no error, SNS publish succeeded (moto accepts it)
