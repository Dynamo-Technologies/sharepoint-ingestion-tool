"""Tests for the textract_trigger Lambda handler."""

import json
import sys
from unittest.mock import MagicMock, patch, ANY

import pytest

sys.path.insert(0, "src")


def _s3_event(*keys):
    """Build a minimal S3 PutObject event for the given keys."""
    return {
        "Records": [
            {
                "s3": {
                    "bucket": {"name": "dynamo-ai-documents"},
                    "object": {"key": key},
                },
            }
            for key in keys
        ],
    }


def _sample_doc(**overrides):
    base = {
        "s3_source_key": "source/Dynamo/HR/doc.pdf",
        "sp_item_id": "sp-1",
        "sp_path": "/HR/doc.pdf",
        "sp_library": "HR",
        "file_type": ".pdf",
        "size_bytes": 1024,
        "content_type": "application/pdf",
    }
    base.update(overrides)
    return base


_VALID_PERM_TAGS = {
    "allowed_groups": "grp-test",
    "sensitivity_level": "internal",
    "matched_prefix": "source/Dynamo",
}


class TestTextractTriggerHandler:
    @patch("textract_trigger.QuarantineManager")
    @patch("textract_trigger.PermissionTagger")
    @patch("textract_trigger.PathMapper")
    @patch("textract_trigger.FileConverter")
    @patch("textract_trigger.DigitalTwinBuilder")
    @patch("textract_trigger.DocumentRegistry")
    @patch("textract_trigger.S3Client")
    @patch("textract_trigger.TextractClient")
    def test_pdf_starts_textract_job(
        self, MockTextract, MockS3, MockRegistry, MockBuilder,
        MockConverter, MockMapper, MockTagger, MockQuarantine,
    ):
        MockTextract.return_value.start_document_analysis.return_value = "job-123"
        MockRegistry.return_value.get_document.return_value = _sample_doc()
        MockConverter.return_value.get_extraction_strategy.return_value = "textract-direct"
        MockTagger.return_value.get_permission_tags.return_value = _VALID_PERM_TAGS

        from textract_trigger import handler
        result = handler(_s3_event("source/Dynamo/HR/doc.pdf"), None)

        body = json.loads(result["body"])
        assert body["textract_jobs"] == 1
        MockTextract.return_value.start_document_analysis.assert_called_once_with(
            "dynamo-ai-documents", "source/Dynamo/HR/doc.pdf",
        )
        MockRegistry.return_value.update_textract_status.assert_called_once_with(
            "source/Dynamo/HR/doc.pdf", "processing", job_id="job-123",
        )

    @patch("textract_trigger.QuarantineManager")
    @patch("textract_trigger.PermissionTagger")
    @patch("textract_trigger.PathMapper")
    @patch("textract_trigger.FileConverter")
    @patch("textract_trigger.DigitalTwinBuilder")
    @patch("textract_trigger.DocumentRegistry")
    @patch("textract_trigger.S3Client")
    @patch("textract_trigger.TextractClient")
    def test_docx_starts_textract_job(
        self, MockTextract, MockS3, MockRegistry, MockBuilder,
        MockConverter, MockMapper, MockTagger, MockQuarantine,
    ):
        MockTextract.return_value.start_document_analysis.return_value = "job-docx"
        MockRegistry.return_value.get_document.return_value = _sample_doc(
            file_type=".docx",
        )
        MockConverter.return_value.get_extraction_strategy.return_value = "textract-direct"
        MockTagger.return_value.get_permission_tags.return_value = _VALID_PERM_TAGS

        from textract_trigger import handler
        result = handler(_s3_event("source/Dynamo/HR/doc.docx"), None)

        body = json.loads(result["body"])
        assert body["textract_jobs"] == 1

    @patch("textract_trigger.QuarantineManager")
    @patch("textract_trigger.PermissionTagger")
    @patch("textract_trigger.boto3")
    @patch("textract_trigger.PathMapper")
    @patch("textract_trigger.FileConverter")
    @patch("textract_trigger.DigitalTwinBuilder")
    @patch("textract_trigger.DocumentRegistry")
    @patch("textract_trigger.S3Client")
    @patch("textract_trigger.TextractClient")
    def test_pptx_direct_extract(
        self, MockTextract, MockS3, MockRegistry, MockBuilder,
        MockConverter, MockMapper, mock_boto3, MockTagger, MockQuarantine,
    ):
        MockRegistry.return_value.get_document.return_value = _sample_doc(
            file_type=".pptx",
        )
        MockConverter.return_value.get_extraction_strategy.return_value = "convert-then-textract"
        MockConverter.return_value.convert_to_pdf_lambda.return_value = b"slide text"
        MockBuilder.return_value.build_twin_from_direct_extract.return_value = {"twin": True}
        MockMapper.return_value.to_s3_extracted_key.return_value = "extracted/key.json"
        MockTagger.return_value.get_permission_tags.return_value = _VALID_PERM_TAGS

        # Mock the raw S3 download
        mock_s3_raw = MagicMock()
        mock_body = MagicMock()
        mock_body.read.return_value = b"pptx-bytes"
        mock_s3_raw.get_object.return_value = {"Body": mock_body}
        mock_boto3.client.return_value = mock_s3_raw

        from textract_trigger import handler
        result = handler(_s3_event("source/Dynamo/HR/slides.pptx"), None)

        body = json.loads(result["body"])
        assert body["direct_extracts"] == 1
        assert body["textract_jobs"] == 0

        MockConverter.return_value.convert_to_pdf_lambda.assert_called_once()
        MockBuilder.return_value.build_twin_from_direct_extract.assert_called_once()
        MockS3.return_value.upload_json_twin.assert_called_once()
        MockRegistry.return_value.update_textract_status.assert_called_once_with(
            "source/Dynamo/HR/slides.pptx", "completed",
            twin_key="extracted/key.json",
        )

    @patch("textract_trigger.QuarantineManager")
    @patch("textract_trigger.PermissionTagger")
    @patch("textract_trigger.boto3")
    @patch("textract_trigger.PathMapper")
    @patch("textract_trigger.FileConverter")
    @patch("textract_trigger.DigitalTwinBuilder")
    @patch("textract_trigger.DocumentRegistry")
    @patch("textract_trigger.S3Client")
    @patch("textract_trigger.TextractClient")
    def test_xlsx_direct_extract(
        self, MockTextract, MockS3, MockRegistry, MockBuilder,
        MockConverter, MockMapper, mock_boto3, MockTagger, MockQuarantine,
    ):
        MockRegistry.return_value.get_document.return_value = _sample_doc(
            file_type=".xlsx",
        )
        MockConverter.return_value.get_extraction_strategy.return_value = "convert-then-textract"
        MockConverter.return_value.convert_to_pdf_lambda.return_value = b"sheet data"
        MockBuilder.return_value.build_twin_from_direct_extract.return_value = {"twin": True}
        MockMapper.return_value.to_s3_extracted_key.return_value = "extracted/key.json"
        MockTagger.return_value.get_permission_tags.return_value = _VALID_PERM_TAGS

        mock_s3_raw = MagicMock()
        mock_body = MagicMock()
        mock_body.read.return_value = b"xlsx-bytes"
        mock_s3_raw.get_object.return_value = {"Body": mock_body}
        mock_boto3.client.return_value = mock_s3_raw

        from textract_trigger import handler
        result = handler(_s3_event("source/Dynamo/HR/data.xlsx"), None)

        body = json.loads(result["body"])
        assert body["direct_extracts"] == 1

    @patch("textract_trigger.QuarantineManager")
    @patch("textract_trigger.PermissionTagger")
    @patch("textract_trigger.boto3")
    @patch("textract_trigger.PathMapper")
    @patch("textract_trigger.FileConverter")
    @patch("textract_trigger.DigitalTwinBuilder")
    @patch("textract_trigger.DocumentRegistry")
    @patch("textract_trigger.S3Client")
    @patch("textract_trigger.TextractClient")
    def test_txt_plain_text_extract(
        self, MockTextract, MockS3, MockRegistry, MockBuilder,
        MockConverter, MockMapper, mock_boto3, MockTagger, MockQuarantine,
    ):
        MockRegistry.return_value.get_document.return_value = _sample_doc(
            file_type=".txt",
        )
        MockConverter.return_value.get_extraction_strategy.return_value = "plain-text"
        MockBuilder.return_value.build_twin_from_direct_extract.return_value = {"twin": True}
        MockMapper.return_value.to_s3_extracted_key.return_value = "extracted/key.json"
        MockTagger.return_value.get_permission_tags.return_value = _VALID_PERM_TAGS

        mock_s3_raw = MagicMock()
        mock_body = MagicMock()
        mock_body.read.return_value = b"Hello plain text"
        mock_s3_raw.get_object.return_value = {"Body": mock_body}
        mock_boto3.client.return_value = mock_s3_raw

        from textract_trigger import handler
        result = handler(_s3_event("source/Dynamo/HR/readme.txt"), None)

        body = json.loads(result["body"])
        assert body["direct_extracts"] == 1

        MockBuilder.return_value.build_twin_from_direct_extract.assert_called_once()
        call_args = MockBuilder.return_value.build_twin_from_direct_extract.call_args
        assert call_args[0][0] == "Hello plain text"

    @patch("textract_trigger.QuarantineManager")
    @patch("textract_trigger.PermissionTagger")
    @patch("textract_trigger.PathMapper")
    @patch("textract_trigger.FileConverter")
    @patch("textract_trigger.DigitalTwinBuilder")
    @patch("textract_trigger.DocumentRegistry")
    @patch("textract_trigger.S3Client")
    @patch("textract_trigger.TextractClient")
    def test_unsupported_type_skipped(
        self, MockTextract, MockS3, MockRegistry, MockBuilder,
        MockConverter, MockMapper, MockTagger, MockQuarantine,
    ):
        MockConverter.return_value.get_extraction_strategy.return_value = "unsupported"

        from textract_trigger import handler
        result = handler(_s3_event("source/Dynamo/HR/photo.jpg"), None)

        body = json.loads(result["body"])
        assert body["skipped"] == 1
        MockTextract.return_value.start_document_analysis.assert_not_called()

    @patch("textract_trigger.QuarantineManager")
    @patch("textract_trigger.PermissionTagger")
    @patch("textract_trigger.PathMapper")
    @patch("textract_trigger.FileConverter")
    @patch("textract_trigger.DigitalTwinBuilder")
    @patch("textract_trigger.DocumentRegistry")
    @patch("textract_trigger.S3Client")
    @patch("textract_trigger.TextractClient")
    def test_skips_non_source_prefix(
        self, MockTextract, MockS3, MockRegistry, MockBuilder,
        MockConverter, MockMapper, MockTagger, MockQuarantine,
    ):
        from textract_trigger import handler
        result = handler(_s3_event("extracted/something.json"), None)

        body = json.loads(result["body"])
        assert body["skipped"] == 1
        MockRegistry.return_value.get_document.assert_not_called()

    @patch("textract_trigger.QuarantineManager")
    @patch("textract_trigger.PermissionTagger")
    @patch("textract_trigger.PathMapper")
    @patch("textract_trigger.FileConverter")
    @patch("textract_trigger.DigitalTwinBuilder")
    @patch("textract_trigger.DocumentRegistry")
    @patch("textract_trigger.S3Client")
    @patch("textract_trigger.TextractClient")
    def test_skips_unregistered_document(
        self, MockTextract, MockS3, MockRegistry, MockBuilder,
        MockConverter, MockMapper, MockTagger, MockQuarantine,
    ):
        MockConverter.return_value.get_extraction_strategy.return_value = "textract-direct"
        MockRegistry.return_value.get_document.return_value = None

        from textract_trigger import handler
        result = handler(_s3_event("source/Dynamo/HR/ghost.pdf"), None)

        body = json.loads(result["body"])
        assert body["skipped"] == 1
        MockTextract.return_value.start_document_analysis.assert_not_called()

    @patch("textract_trigger.QuarantineManager")
    @patch("textract_trigger.PermissionTagger")
    @patch("textract_trigger.PathMapper")
    @patch("textract_trigger.FileConverter")
    @patch("textract_trigger.DigitalTwinBuilder")
    @patch("textract_trigger.DocumentRegistry")
    @patch("textract_trigger.S3Client")
    @patch("textract_trigger.TextractClient")
    def test_textract_failure_marks_failed(
        self, MockTextract, MockS3, MockRegistry, MockBuilder,
        MockConverter, MockMapper, MockTagger, MockQuarantine,
    ):
        MockRegistry.return_value.get_document.return_value = _sample_doc()
        MockConverter.return_value.get_extraction_strategy.return_value = "textract-direct"
        MockTextract.return_value.start_document_analysis.side_effect = RuntimeError("boom")
        MockTagger.return_value.get_permission_tags.return_value = _VALID_PERM_TAGS

        from textract_trigger import handler
        result = handler(_s3_event("source/Dynamo/HR/doc.pdf"), None)

        body = json.loads(result["body"])
        assert body["errors"] == 1
        # Should attempt to mark as failed
        MockRegistry.return_value.update_textract_status.assert_called_with(
            "source/Dynamo/HR/doc.pdf", "failed",
        )

    @patch("textract_trigger.QuarantineManager")
    @patch("textract_trigger.PermissionTagger")
    @patch("textract_trigger.PathMapper")
    @patch("textract_trigger.FileConverter")
    @patch("textract_trigger.DigitalTwinBuilder")
    @patch("textract_trigger.DocumentRegistry")
    @patch("textract_trigger.S3Client")
    @patch("textract_trigger.TextractClient")
    def test_multiple_records_processed(
        self, MockTextract, MockS3, MockRegistry, MockBuilder,
        MockConverter, MockMapper, MockTagger, MockQuarantine,
    ):
        MockTextract.return_value.start_document_analysis.return_value = "job-x"
        MockRegistry.return_value.get_document.return_value = _sample_doc()
        MockConverter.return_value.get_extraction_strategy.return_value = "textract-direct"
        MockTagger.return_value.get_permission_tags.return_value = _VALID_PERM_TAGS

        from textract_trigger import handler
        result = handler(
            _s3_event(
                "source/Dynamo/HR/a.pdf",
                "source/Dynamo/HR/b.pdf",
            ),
            None,
        )

        body = json.loads(result["body"])
        assert body["textract_jobs"] == 2

    @patch("textract_trigger.QuarantineManager")
    @patch("textract_trigger.PermissionTagger")
    @patch("textract_trigger.PathMapper")
    @patch("textract_trigger.FileConverter")
    @patch("textract_trigger.DigitalTwinBuilder")
    @patch("textract_trigger.DocumentRegistry")
    @patch("textract_trigger.S3Client")
    @patch("textract_trigger.TextractClient")
    def test_error_does_not_crash_remaining(
        self, MockTextract, MockS3, MockRegistry, MockBuilder,
        MockConverter, MockMapper, MockTagger, MockQuarantine,
    ):
        """First record fails, second should still be processed."""
        MockRegistry.return_value.get_document.return_value = _sample_doc()
        MockConverter.return_value.get_extraction_strategy.return_value = "textract-direct"
        MockTextract.return_value.start_document_analysis.side_effect = [
            RuntimeError("fail"), "job-ok",
        ]
        MockTagger.return_value.get_permission_tags.return_value = _VALID_PERM_TAGS

        from textract_trigger import handler
        result = handler(
            _s3_event("source/a.pdf", "source/b.pdf"),
            None,
        )

        body = json.loads(result["body"])
        assert body["errors"] == 1
        assert body["textract_jobs"] == 1

    @patch("textract_trigger.QuarantineManager")
    @patch("textract_trigger.PermissionTagger")
    @patch("textract_trigger.PathMapper")
    @patch("textract_trigger.FileConverter")
    @patch("textract_trigger.DigitalTwinBuilder")
    @patch("textract_trigger.DocumentRegistry")
    @patch("textract_trigger.S3Client")
    @patch("textract_trigger.TextractClient")
    def test_empty_event(
        self, MockTextract, MockS3, MockRegistry, MockBuilder,
        MockConverter, MockMapper, MockTagger, MockQuarantine,
    ):
        from textract_trigger import handler
        result = handler({"Records": []}, None)

        body = json.loads(result["body"])
        assert body == {
            "textract_jobs": 0, "direct_extracts": 0,
            "skipped": 0, "errors": 0, "quarantined": 0,
        }


class TestTextractTriggerQuarantine:
    @patch("textract_trigger.QuarantineManager")
    @patch("textract_trigger.PermissionTagger")
    @patch("textract_trigger.PathMapper")
    @patch("textract_trigger.FileConverter")
    @patch("textract_trigger.DigitalTwinBuilder")
    @patch("textract_trigger.DocumentRegistry")
    @patch("textract_trigger.S3Client")
    @patch("textract_trigger.TextractClient")
    def test_unmapped_document_quarantined(
        self, MockTextract, MockS3, MockRegistry, MockBuilder,
        MockConverter, MockMapper, MockTagger, MockQuarantine,
    ):
        """PermissionTagger returns None -> quarantine_document called,
        textract NOT started, results['quarantined'] == 1."""
        MockRegistry.return_value.get_document.return_value = _sample_doc()
        MockConverter.return_value.get_extraction_strategy.return_value = "textract-direct"
        MockTagger.return_value.get_permission_tags.return_value = None

        from textract_trigger import handler
        result = handler(_s3_event("source/Dynamo/HR/doc.pdf"), None)

        body = json.loads(result["body"])
        assert body["quarantined"] == 1
        assert body["textract_jobs"] == 0

        # Quarantine should have been called
        MockQuarantine.return_value.quarantine_document.assert_called_once_with(
            "source/Dynamo/HR/doc.pdf", reason="no_mapping",
        )
        # Registry should be updated to quarantined
        MockRegistry.return_value.update_textract_status.assert_called_once_with(
            "source/Dynamo/HR/doc.pdf", "quarantined",
        )
        # Textract should NOT have been started
        MockTextract.return_value.start_document_analysis.assert_not_called()

    @patch("textract_trigger.QuarantineManager")
    @patch("textract_trigger.PermissionTagger")
    @patch("textract_trigger.PathMapper")
    @patch("textract_trigger.FileConverter")
    @patch("textract_trigger.DigitalTwinBuilder")
    @patch("textract_trigger.DocumentRegistry")
    @patch("textract_trigger.S3Client")
    @patch("textract_trigger.TextractClient")
    def test_mapped_document_proceeds_normally(
        self, MockTextract, MockS3, MockRegistry, MockBuilder,
        MockConverter, MockMapper, MockTagger, MockQuarantine,
    ):
        """PermissionTagger returns valid tags -> textract started,
        quarantine NOT called, results['quarantined'] == 0."""
        MockTextract.return_value.start_document_analysis.return_value = "job-456"
        MockRegistry.return_value.get_document.return_value = _sample_doc()
        MockConverter.return_value.get_extraction_strategy.return_value = "textract-direct"
        MockTagger.return_value.get_permission_tags.return_value = _VALID_PERM_TAGS

        from textract_trigger import handler
        result = handler(_s3_event("source/Dynamo/HR/doc.pdf"), None)

        body = json.loads(result["body"])
        assert body["quarantined"] == 0
        assert body["textract_jobs"] == 1

        # Quarantine should NOT have been called
        MockQuarantine.return_value.quarantine_document.assert_not_called()
        # Textract should have been started
        MockTextract.return_value.start_document_analysis.assert_called_once_with(
            "dynamo-ai-documents", "source/Dynamo/HR/doc.pdf",
        )
