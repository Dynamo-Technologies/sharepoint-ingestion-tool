"""Tests for QuarantineManager — moves unmapped docs to quarantine prefix."""

import json
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, "src")

BUCKET = "test-ingest-bucket"
REGION = "us-east-1"
SNS_TOPIC_ARN = "arn:aws:sns:us-east-1:123456789012:quarantine-alerts"


@pytest.fixture
def mock_s3():
    """Return a MagicMock standing in for the boto3 S3 client."""
    return MagicMock()


@pytest.fixture
def mock_sns():
    """Return a MagicMock standing in for the boto3 SNS client."""
    return MagicMock()


@pytest.fixture
def manager(mock_s3, mock_sns):
    """Create a QuarantineManager with injected mock boto3 clients."""
    with patch("quarantine.boto3") as mock_boto3:
        # Make boto3.client return our mocks depending on the service name
        def client_factory(service, **kwargs):
            if service == "s3":
                return mock_s3
            elif service == "sns":
                return mock_sns
            return MagicMock()

        mock_boto3.client.side_effect = client_factory

        from quarantine import QuarantineManager
        mgr = QuarantineManager(
            bucket=BUCKET,
            sns_topic_arn=SNS_TOPIC_ARN,
            region=REGION,
        )
    return mgr


# ===================================================================
# _to_quarantine_key (static method)
# ===================================================================

class TestToQuarantineKey:
    def test_quarantine_key_strips_source_prefix(self):
        from quarantine import QuarantineManager
        result = QuarantineManager._to_quarantine_key("source/Dynamo/HR/doc.pdf")
        assert result == "quarantine/Dynamo/HR/doc.pdf"

    def test_quarantine_key_handles_nested_paths(self):
        from quarantine import QuarantineManager
        result = QuarantineManager._to_quarantine_key(
            "source/Dynamo/HR/Policies/2025/handbook.pdf"
        )
        assert result == "quarantine/Dynamo/HR/Policies/2025/handbook.pdf"

    def test_quarantine_key_prepends_when_no_source_prefix(self):
        from quarantine import QuarantineManager
        result = QuarantineManager._to_quarantine_key("other/path/doc.pdf")
        assert result == "quarantine/other/path/doc.pdf"

    def test_quarantine_key_source_only(self):
        from quarantine import QuarantineManager
        result = QuarantineManager._to_quarantine_key("source/doc.pdf")
        assert result == "quarantine/doc.pdf"


# ===================================================================
# quarantine_document
# ===================================================================

class TestQuarantineDocument:
    def test_quarantine_document_copies_to_quarantine_prefix(self, manager, mock_s3):
        s3_key = "source/Dynamo/HR/doc.pdf"
        result = manager.quarantine_document(s3_key, reason="no_mapping")

        assert result == "quarantine/Dynamo/HR/doc.pdf"

        # Verify copy_object was called with correct destination Key
        mock_s3.copy_object.assert_called_once()
        call_kwargs = mock_s3.copy_object.call_args[1]
        assert call_kwargs["Key"] == "quarantine/Dynamo/HR/doc.pdf"
        assert call_kwargs["Bucket"] == BUCKET
        assert call_kwargs["CopySource"] == {"Bucket": BUCKET, "Key": s3_key}

    def test_quarantine_document_tags_with_original_prefix(self, manager, mock_s3):
        s3_key = "source/Dynamo/HR/doc.pdf"
        manager.quarantine_document(s3_key, reason="no_mapping")

        call_kwargs = mock_s3.copy_object.call_args[1]

        # Must use REPLACE tagging directive
        assert call_kwargs["TaggingDirective"] == "REPLACE"

        # Parse the Tagging string to verify tags
        tagging_str = call_kwargs["Tagging"]
        tag_pairs = dict(pair.split("=") for pair in tagging_str.split("&"))

        assert "original_prefix" in tag_pairs
        # original_prefix should be URL-encoded
        from urllib.parse import unquote
        assert unquote(tag_pairs["original_prefix"]) == s3_key

        assert "quarantine_reason" in tag_pairs
        assert tag_pairs["quarantine_reason"] == "no_mapping"

        assert "quarantined_at" in tag_pairs
        # quarantined_at should be a URL-encoded ISO timestamp
        ts = unquote(tag_pairs["quarantined_at"])
        assert "T" in ts  # basic ISO format check

    def test_quarantine_document_deletes_source(self, manager, mock_s3):
        s3_key = "source/Dynamo/HR/doc.pdf"
        manager.quarantine_document(s3_key)

        mock_s3.delete_object.assert_called_once_with(
            Bucket=BUCKET, Key=s3_key,
        )

    def test_quarantine_document_publishes_sns(self, manager, mock_sns):
        s3_key = "source/Dynamo/HR/doc.pdf"
        manager.quarantine_document(s3_key, reason="no_mapping")

        mock_sns.publish.assert_called_once()
        call_kwargs = mock_sns.publish.call_args[1]

        assert call_kwargs["TopicArn"] == SNS_TOPIC_ARN

        message = json.loads(call_kwargs["Message"])
        assert message["s3_key"] == s3_key
        assert message["quarantine_key"] == "quarantine/Dynamo/HR/doc.pdf"
        assert message["reason"] == "no_mapping"
        assert message["bucket"] == BUCKET
        assert "timestamp" in message

    def test_quarantine_document_sns_failure_does_not_crash(
        self, manager, mock_s3, mock_sns
    ):
        """If SNS publish fails, the method should still return the quarantine key."""
        mock_sns.publish.side_effect = Exception("SNS unavailable")

        s3_key = "source/Dynamo/HR/doc.pdf"
        result = manager.quarantine_document(s3_key, reason="no_mapping")

        # Should still succeed — SNS failure is logged, not raised
        assert result == "quarantine/Dynamo/HR/doc.pdf"
        # S3 operations should still have occurred
        mock_s3.copy_object.assert_called_once()
        mock_s3.delete_object.assert_called_once()

    def test_quarantine_document_default_reason(self, manager, mock_sns):
        """Default reason should be 'no_mapping'."""
        s3_key = "source/Dynamo/HR/doc.pdf"
        manager.quarantine_document(s3_key)

        message = json.loads(mock_sns.publish.call_args[1]["Message"])
        assert message["reason"] == "no_mapping"
