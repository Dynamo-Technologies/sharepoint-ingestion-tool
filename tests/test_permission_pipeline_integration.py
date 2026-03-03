"""Integration tests for permission tagging through the full pipeline.

Uses moto for S3 + DynamoDB mocking to test:
1. Tagged document -> twin includes permissions -> chunks include permissions
2. Unmapped document -> quarantined
3. Quarantined document -> reprocessed after mapping added
"""

import json
import sys
import time
from datetime import datetime, timezone

import boto3
import moto
import pytest

sys.path.insert(0, "src")

from lib.dynamo_permissions.client import PermissionClient, PrefixPermission


PERM_TABLE = "test-permission-mappings"
CACHE_TABLE = "test-user-cache"
BUCKET = "test-bucket"


@pytest.fixture
def aws_env():
    """Set up mocked AWS services."""
    with moto.mock_aws():
        region = "us-east-1"
        dynamodb = boto3.resource("dynamodb", region_name=region)
        s3 = boto3.client("s3", region_name=region)
        sns = boto3.client("sns", region_name=region)

        # Create DynamoDB tables
        dynamodb.create_table(
            TableName=PERM_TABLE,
            KeySchema=[{"AttributeName": "s3_prefix", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "s3_prefix", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        # Create S3 bucket
        s3.create_bucket(Bucket=BUCKET)

        # Create SNS topic
        topic = sns.create_topic(Name="test-quarantine-alerts")
        topic_arn = topic["TopicArn"]

        # Seed permission mapping
        perm_client = PermissionClient(
            permission_table_name=PERM_TABLE,
            dynamodb_resource=dynamodb,
        )
        perm_client.put_permission_mapping(PrefixPermission(
            s3_prefix="source/Dynamo/HR",
            allowed_groups=["grp-hr-1", "grp-hr-2"],
            sensitivity_level="confidential",
            last_updated=datetime.now(timezone.utc).isoformat(),
            updated_by="test",
        ))
        perm_client.put_permission_mapping(PrefixPermission(
            s3_prefix="source/Dynamo",
            allowed_groups=["grp-all"],
            sensitivity_level="internal",
            last_updated=datetime.now(timezone.utc).isoformat(),
            updated_by="test",
        ))

        yield {
            "dynamodb": dynamodb,
            "s3": s3,
            "sns": sns,
            "perm_client": perm_client,
            "topic_arn": topic_arn,
            "region": region,
        }


class TestPermissionTagLookup:
    def test_permission_tags_for_hr_document(self, aws_env):
        from permission_tagger import PermissionTagger
        tagger = PermissionTagger.__new__(PermissionTagger)
        tagger._client = aws_env["perm_client"]

        tags = tagger.get_permission_tags("source/Dynamo/HR/doc.pdf")
        assert tags is not None
        assert "grp-hr-1" in tags["allowed_groups"]
        assert tags["sensitivity_level"] == "confidential"

    def test_permission_tags_for_root_fallback(self, aws_env):
        from permission_tagger import PermissionTagger
        tagger = PermissionTagger.__new__(PermissionTagger)
        tagger._client = aws_env["perm_client"]

        tags = tagger.get_permission_tags("source/Dynamo/Random/doc.pdf")
        assert tags is not None
        assert tags["sensitivity_level"] == "internal"

    def test_no_mapping_returns_none(self, aws_env):
        from permission_tagger import PermissionTagger
        tagger = PermissionTagger.__new__(PermissionTagger)
        tagger._client = aws_env["perm_client"]

        tags = tagger.get_permission_tags("other-bucket/doc.pdf")
        assert tags is None


class TestTwinPermissionMetadata:
    def test_twin_carries_permissions(self, aws_env):
        from digital_twin import DigitalTwinBuilder
        builder = DigitalTwinBuilder()

        source_meta = {
            "s3_source_key": "source/Dynamo/HR/doc.pdf",
            "sp_library": "HR",
            "sp_path": "/HR/doc.pdf",
            "file_type": ".pdf",
            "size_bytes": 1024,
            "permissions": {
                "allowed_groups": ["grp-hr-1", "grp-hr-2"],
                "sensitivity_level": "confidential",
                "s3_prefix": "source/Dynamo/HR",
                "custom_filters": {},
            },
        }
        twin = builder.build_twin_from_direct_extract("Hello world", [], source_meta)
        assert twin["permissions"]["allowed_groups"] == ["grp-hr-1", "grp-hr-2"]
        assert twin["permissions"]["sensitivity_level"] == "confidential"


class TestChunkPermissionMetadata:
    def test_chunks_carry_permission_fields(self, aws_env):
        from digital_twin import DigitalTwinBuilder
        from chunker import DocumentChunker

        builder = DigitalTwinBuilder()
        source_meta = {
            "s3_source_key": "source/Dynamo/HR/doc.pdf",
            "sp_library": "HR",
            "sp_path": "/HR/doc.pdf",
            "file_type": ".pdf",
            "size_bytes": 1024,
            "permissions": {
                "allowed_groups": ["grp-hr-1", "grp-hr-2"],
                "sensitivity_level": "confidential",
                "s3_prefix": "source/Dynamo/HR",
                "custom_filters": {},
            },
        }
        twin = builder.build_twin_from_direct_extract("Test content here.", [], source_meta)
        chunker = DocumentChunker()
        chunks = chunker.chunk_document(twin)

        assert len(chunks) >= 1
        chunk = chunks[0]
        assert chunk["allowed_groups"] == ["grp-hr-1", "grp-hr-2"]
        assert chunk["sensitivity_level"] == "confidential"
        assert chunk["s3_prefix"] == "source/Dynamo/HR"
        assert chunk["document_id"] == twin["document_id"]


class TestQuarantineFlow:
    def test_quarantine_copies_and_deletes(self, aws_env):
        s3 = aws_env["s3"]

        # Upload a test document to source/
        s3.put_object(Bucket=BUCKET, Key="source/Unknown/doc.pdf", Body=b"content")

        from quarantine import QuarantineManager
        mgr = QuarantineManager.__new__(QuarantineManager)
        mgr._s3 = s3
        mgr._sns = aws_env["sns"]
        mgr.bucket = BUCKET  # NOTE: public attribute, not _bucket
        mgr._sns_topic_arn = aws_env["topic_arn"]

        q_key = mgr.quarantine_document("source/Unknown/doc.pdf", reason="no_mapping")
        assert q_key == "quarantine/Unknown/doc.pdf"

        # Verify quarantine copy exists
        resp = s3.get_object(Bucket=BUCKET, Key="quarantine/Unknown/doc.pdf")
        assert resp["Body"].read() == b"content"

        # Verify source deleted
        with pytest.raises(s3.exceptions.NoSuchKey):
            s3.get_object(Bucket=BUCKET, Key="source/Unknown/doc.pdf")
