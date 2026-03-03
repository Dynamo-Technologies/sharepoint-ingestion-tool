# Permission Tagger Lambda Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add permission tagging to the ingestion pipeline so every document and chunk carries `allowed_groups`, `sensitivity_level`, and related metadata for RAG access control.

**Architecture:** Modify existing Lambdas inline rather than adding a new Lambda. `daily_sync.py` applies permission tags at upload time. `textract_trigger.py` acts as quarantine guard. Permission metadata flows through digital twins into chunks. A shared `permission_tagger.py` module provides DRY lookup logic.

**Tech Stack:** Python 3.11, boto3, DynamoDB, S3 object tags, SNS, Terraform, pytest + moto

---

## Task 1: Shared Permission Tagger Module

**Files:**
- Create: `src/permission_tagger.py`
- Test: `tests/test_permission_tagger.py`

**Context:** This module wraps `lib/dynamo_permissions/client.py` with S3-tag-level convenience methods. Used by `daily_sync.py`, `textract_trigger.py`, and scripts.

**Step 1: Write the tests**

```python
"""Tests for the permission tagger module."""

import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, "src")


class TestPermissionTagger:
    def _make_tagger(self, perm_result=None):
        """Create a PermissionTagger with mocked PermissionClient."""
        from permission_tagger import PermissionTagger

        mock_client = MagicMock()
        if perm_result is not None:
            mock_client.get_allowed_groups.return_value = perm_result
        else:
            mock_client.get_allowed_groups.return_value = None

        tagger = PermissionTagger.__new__(PermissionTagger)
        tagger._client = mock_client
        return tagger

    def _make_prefix_permission(self, **kwargs):
        from lib.dynamo_permissions.client import PrefixPermission
        defaults = {
            "s3_prefix": "source/Dynamo/HR",
            "allowed_groups": ["grp-hr-1", "grp-hr-2"],
            "sensitivity_level": "confidential",
            "custom_filters": {},
            "last_updated": "2026-01-01T00:00:00Z",
            "updated_by": "test",
        }
        defaults.update(kwargs)
        return PrefixPermission(**defaults)

    def test_get_permission_tags_returns_dict(self):
        perm = self._make_prefix_permission()
        tagger = self._make_tagger(perm_result=perm)
        result = tagger.get_permission_tags("source/Dynamo/HR/doc.pdf")
        assert result is not None
        assert result["allowed_groups"] == "grp-hr-1,grp-hr-2"
        assert result["sensitivity_level"] == "confidential"
        assert result["matched_prefix"] == "source/Dynamo/HR"

    def test_get_permission_tags_no_mapping_returns_none(self):
        tagger = self._make_tagger(perm_result=None)
        result = tagger.get_permission_tags("other-bucket/doc.pdf")
        assert result is None

    def test_get_permission_tags_with_custom_filters(self):
        perm = self._make_prefix_permission(
            custom_filters={"project_code": "P001"},
        )
        tagger = self._make_tagger(perm_result=perm)
        result = tagger.get_permission_tags("source/Dynamo/HR/doc.pdf")
        assert result["custom_filters"] == "project_code=P001"

    def test_get_permission_metadata_returns_full_dict(self):
        perm = self._make_prefix_permission()
        tagger = self._make_tagger(perm_result=perm)
        result = tagger.get_permission_metadata("source/Dynamo/HR/doc.pdf")
        assert result is not None
        assert result["allowed_groups"] == ["grp-hr-1", "grp-hr-2"]
        assert result["sensitivity_level"] == "confidential"
        assert result["s3_prefix"] == "source/Dynamo/HR"
        assert result["custom_filters"] == {}

    def test_get_permission_metadata_no_mapping_returns_none(self):
        tagger = self._make_tagger(perm_result=None)
        result = tagger.get_permission_metadata("other-bucket/doc.pdf")
        assert result is None

    def test_empty_allowed_groups(self):
        perm = self._make_prefix_permission(allowed_groups=[])
        tagger = self._make_tagger(perm_result=perm)
        result = tagger.get_permission_tags("source/Dynamo/HR/doc.pdf")
        assert result["allowed_groups"] == ""
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_permission_tagger.py -v`
Expected: FAIL — `permission_tagger` module does not exist

**Step 3: Write the implementation**

```python
"""Shared permission tagger for the ingestion pipeline.

Wraps lib/dynamo_permissions to provide:
- S3 tag-formatted permission lookups (comma-separated strings)
- Full permission metadata dicts for twin/chunk propagation

Used by daily_sync, textract_trigger, and retag scripts.
"""

import logging
import os
from typing import Any

# Support both Lambda (src on PYTHONPATH) and script (lib at repo root) imports
try:
    from lib.dynamo_permissions.client import PermissionClient, PrefixPermission
except ImportError:
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from lib.dynamo_permissions.client import PermissionClient, PrefixPermission

logger = logging.getLogger(__name__)


class PermissionTagger:
    """Look up DynamoDB permission mappings and format for S3 tags or metadata."""

    def __init__(
        self,
        permission_table_name: str | None = None,
        dynamodb_resource: Any | None = None,
    ) -> None:
        self._client = PermissionClient(
            permission_table_name=permission_table_name,
            dynamodb_resource=dynamodb_resource,
        )

    def get_permission_tags(self, s3_key: str) -> dict[str, str] | None:
        """Look up permission mapping and return S3-tag-formatted dict.

        Returns None if no mapping exists (quarantine signal).
        Tag values are strings suitable for S3 object tagging.
        """
        perm = self._client.get_allowed_groups(s3_key)
        if perm is None:
            return None

        tags: dict[str, str] = {
            "allowed_groups": ",".join(perm.allowed_groups),
            "sensitivity_level": perm.sensitivity_level,
            "matched_prefix": perm.s3_prefix,
        }

        if perm.custom_filters:
            tags["custom_filters"] = ",".join(
                f"{k}={v}" for k, v in perm.custom_filters.items()
            )

        return tags

    def get_permission_metadata(self, s3_key: str) -> dict | None:
        """Look up permission mapping and return full metadata dict.

        Returns None if no mapping exists.
        Values are native Python types (lists, dicts) for JSON embedding.
        """
        perm = self._client.get_allowed_groups(s3_key)
        if perm is None:
            return None

        return {
            "allowed_groups": perm.allowed_groups,
            "sensitivity_level": perm.sensitivity_level,
            "s3_prefix": perm.s3_prefix,
            "custom_filters": perm.custom_filters,
        }
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_permission_tagger.py -v`
Expected: All 7 tests PASS

**Step 5: Commit**

```
git add src/permission_tagger.py tests/test_permission_tagger.py
git commit -m "feat: add shared permission tagger module"
```

---

## Task 2: Quarantine Module

**Files:**
- Create: `src/quarantine.py`
- Test: `tests/test_quarantine.py`

**Context:** Moves unmapped documents to `quarantine/` prefix, preserves original path as S3 tag, publishes to SNS.

**Step 1: Write the tests**

```python
"""Tests for the quarantine module."""

import json
import sys
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, "src")


class TestQuarantineManager:
    def _make_manager(self):
        from quarantine import QuarantineManager

        mock_s3 = MagicMock()
        mock_sns = MagicMock()
        mgr = QuarantineManager.__new__(QuarantineManager)
        mgr._s3 = mock_s3
        mgr._sns = mock_sns
        mgr._bucket = "dynamo-ai-documents"
        mgr._sns_topic_arn = "arn:aws:sns:us-east-1:123:doc-quarantine-alerts"
        return mgr, mock_s3, mock_sns

    def test_quarantine_document_copies_to_quarantine_prefix(self):
        mgr, mock_s3, mock_sns = self._make_manager()

        mgr.quarantine_document("source/Dynamo/HR/doc.pdf", reason="no_mapping")

        mock_s3.copy_object.assert_called_once()
        call_kwargs = mock_s3.copy_object.call_args[1]
        assert call_kwargs["Key"] == "quarantine/Dynamo/HR/doc.pdf"
        assert call_kwargs["CopySource"]["Key"] == "source/Dynamo/HR/doc.pdf"

    def test_quarantine_document_tags_with_original_prefix(self):
        mgr, mock_s3, mock_sns = self._make_manager()

        mgr.quarantine_document("source/Dynamo/HR/doc.pdf", reason="no_mapping")

        call_kwargs = mock_s3.copy_object.call_args[1]
        tagging = call_kwargs["TaggingDirective"]
        assert tagging == "REPLACE"
        # Check tags contain original_prefix
        tag_str = call_kwargs["Tagging"]
        assert "original_prefix" in tag_str
        assert "source%2FDynamo%2FHR%2Fdoc.pdf" in tag_str or "source/Dynamo/HR/doc.pdf" in tag_str

    def test_quarantine_document_deletes_source(self):
        mgr, mock_s3, mock_sns = self._make_manager()

        mgr.quarantine_document("source/Dynamo/HR/doc.pdf", reason="no_mapping")

        mock_s3.delete_object.assert_called_once_with(
            Bucket="dynamo-ai-documents",
            Key="source/Dynamo/HR/doc.pdf",
        )

    def test_quarantine_document_publishes_sns(self):
        mgr, mock_s3, mock_sns = self._make_manager()

        mgr.quarantine_document("source/Dynamo/HR/doc.pdf", reason="no_mapping")

        mock_sns.publish.assert_called_once()
        call_kwargs = mock_sns.publish.call_args[1]
        assert call_kwargs["TopicArn"] == "arn:aws:sns:us-east-1:123:doc-quarantine-alerts"
        msg = json.loads(call_kwargs["Message"])
        assert msg["s3_key"] == "source/Dynamo/HR/doc.pdf"
        assert msg["reason"] == "no_mapping"

    def test_quarantine_key_strips_source_prefix(self):
        from quarantine import QuarantineManager
        key = QuarantineManager._to_quarantine_key("source/Dynamo/Finance/report.xlsx")
        assert key == "quarantine/Dynamo/Finance/report.xlsx"

    def test_quarantine_key_handles_nested_paths(self):
        from quarantine import QuarantineManager
        key = QuarantineManager._to_quarantine_key(
            "source/Dynamo/HR/Payroll/2026/salaries.pdf"
        )
        assert key == "quarantine/Dynamo/HR/Payroll/2026/salaries.pdf"
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_quarantine.py -v`
Expected: FAIL — `quarantine` module does not exist

**Step 3: Write the implementation**

```python
"""Quarantine workflow for unmapped documents.

Documents without a permission mapping are moved from source/ to quarantine/,
tagged with their original path, and an SNS notification is published.
"""

import json
import logging
import os
from datetime import datetime, timezone
from urllib.parse import quote

import boto3

from config import config

logger = logging.getLogger(__name__)


class QuarantineManager:
    """Move unmapped documents to quarantine and notify."""

    def __init__(
        self,
        bucket: str | None = None,
        sns_topic_arn: str | None = None,
        region: str | None = None,
    ) -> None:
        self._bucket = bucket or config.s3_bucket
        self._sns_topic_arn = sns_topic_arn or os.getenv(
            "QUARANTINE_SNS_TOPIC_ARN", ""
        )
        region = region or config.aws_region
        self._s3 = boto3.client("s3", region_name=region)
        self._sns = boto3.client("sns", region_name=region)

    def quarantine_document(self, s3_key: str, reason: str = "no_mapping") -> str:
        """Move a document to quarantine/ and publish an SNS alert.

        Returns the quarantine S3 key.
        """
        quarantine_key = self._to_quarantine_key(s3_key)
        now = datetime.now(timezone.utc).isoformat()

        tags = (
            f"original_prefix={quote(s3_key, safe='')}"
            f"&quarantine_reason={quote(reason, safe='')}"
            f"&quarantined_at={quote(now, safe='')}"
        )

        # Copy to quarantine/
        self._s3.copy_object(
            Bucket=self._bucket,
            Key=quarantine_key,
            CopySource={"Bucket": self._bucket, "Key": s3_key},
            TaggingDirective="REPLACE",
            Tagging=tags,
        )

        # Delete from source/
        self._s3.delete_object(Bucket=self._bucket, Key=s3_key)

        logger.warning(
            "Quarantined %s -> %s (reason: %s)", s3_key, quarantine_key, reason,
        )

        # Publish SNS alert
        if self._sns_topic_arn:
            try:
                self._sns.publish(
                    TopicArn=self._sns_topic_arn,
                    Subject=f"Document quarantined: {os.path.basename(s3_key)}",
                    Message=json.dumps({
                        "s3_key": s3_key,
                        "quarantine_key": quarantine_key,
                        "reason": reason,
                        "timestamp": now,
                        "bucket": self._bucket,
                    }),
                )
            except Exception:
                logger.exception("Failed to publish quarantine SNS alert")

        return quarantine_key

    @staticmethod
    def _to_quarantine_key(s3_key: str) -> str:
        """Convert source/... key to quarantine/... key."""
        if s3_key.startswith("source/"):
            return "quarantine/" + s3_key[len("source/"):]
        return "quarantine/" + s3_key
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_quarantine.py -v`
Expected: All 6 tests PASS

**Step 5: Commit**

```
git add src/quarantine.py tests/test_quarantine.py
git commit -m "feat: add quarantine module for unmapped documents"
```

---

## Task 3: Modify `daily_sync.py` to Add Permission Tags

**Files:**
- Modify: `src/daily_sync.py` (lines 141-153 — tag building section)
- Modify: `src/config.py` (add PERMISSION_MAPPINGS_TABLE)
- Test: `tests/test_daily_sync.py` (add new tests)

**Context:** After `daily_sync` computes the S3 key and builds tags, also look up the DynamoDB permission mapping. Add `allowed_groups` and `sensitivity_level` as S3 tags. If no mapping, log a warning but still upload (quarantine is enforced downstream in `textract_trigger`).

**Step 1: Add config env var**

In `src/config.py`, add to the `Config` dataclass:

```python
    # Permission tables
    permission_mappings_table: str = os.getenv(
        "PERMISSION_MAPPINGS_TABLE", "doc-permission-mappings"
    )
```

**Step 2: Write the failing tests for daily_sync permission tagging**

Add to `tests/test_daily_sync.py`:

```python
class TestDailySyncPermissionTagging:
    @patch("daily_sync.PermissionTagger")
    @patch("daily_sync.PathMapper")
    @patch("daily_sync.DocumentRegistry")
    @patch("daily_sync.DeltaTracker")
    @patch("daily_sync.S3Client")
    @patch("daily_sync.GraphClient")
    def test_permission_tags_added_to_upload(
        self, MockGraph, MockS3, MockDelta, MockRegistry, MockMapper,
        MockTagger,
    ):
        mock_graph = MockGraph.return_value
        _setup_graph(mock_graph, ([_new_file_item()], "token"))
        mock_graph.download_file.return_value = b"content"

        MockDelta.return_value.get_delta_token.return_value = None
        MockRegistry.return_value.get_document.return_value = None
        MockMapper.return_value.to_s3_source_key.return_value = "source/Dynamo/HR/doc.pdf"

        MockTagger.return_value.get_permission_tags.return_value = {
            "allowed_groups": "grp-hr-1,grp-hr-2",
            "sensitivity_level": "confidential",
            "matched_prefix": "source/Dynamo/HR",
        }

        from daily_sync import handler
        handler({}, None)

        call_kwargs = MockS3.return_value.upload_document.call_args[1]
        assert "allowed_groups" in call_kwargs["tags"]
        assert call_kwargs["tags"]["allowed_groups"] == "grp-hr-1,grp-hr-2"
        assert call_kwargs["tags"]["sensitivity_level"] == "confidential"

    @patch("daily_sync.PermissionTagger")
    @patch("daily_sync.PathMapper")
    @patch("daily_sync.DocumentRegistry")
    @patch("daily_sync.DeltaTracker")
    @patch("daily_sync.S3Client")
    @patch("daily_sync.GraphClient")
    def test_no_permission_mapping_still_uploads(
        self, MockGraph, MockS3, MockDelta, MockRegistry, MockMapper,
        MockTagger,
    ):
        """Documents without mappings are uploaded normally (quarantine is in textract_trigger)."""
        mock_graph = MockGraph.return_value
        _setup_graph(mock_graph, ([_new_file_item()], "token"))
        mock_graph.download_file.return_value = b"content"

        MockDelta.return_value.get_delta_token.return_value = None
        MockRegistry.return_value.get_document.return_value = None
        MockMapper.return_value.to_s3_source_key.return_value = "source/Other/doc.pdf"

        MockTagger.return_value.get_permission_tags.return_value = None

        from daily_sync import handler
        body = json.loads(handler({}, None)["body"])

        assert body["created"] == 1
        MockS3.return_value.upload_document.assert_called_once()
```

**Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_daily_sync.py::TestDailySyncPermissionTagging -v`
Expected: FAIL — `PermissionTagger` not imported in `daily_sync`

**Step 4: Modify `daily_sync.py`**

Add import at top:
```python
from permission_tagger import PermissionTagger
```

After `acl = AccessControlMapper()` (line 38), add:
```python
    perm_tagger = PermissionTagger()
```

Replace the tag-building section (lines 141-152) with:

```python
                access_tags = acl.map_document(lib_name, sp_path)

                tags = PathMapper.build_s3_tags({
                    "site_name": config.sharepoint_site_name,
                    "library_name": lib_name,
                    "sharepoint_path": sp_path,
                    "name": name,
                    "file_type": os.path.splitext(name)[1].lower(),
                    "last_modified": sp_last_modified,
                    "id": sp_id,
                })
                tags["access-tags"] = ",".join(access_tags)

                # Add DynamoDB permission tags
                perm_tags = perm_tagger.get_permission_tags(s3_key)
                if perm_tags:
                    tags["allowed_groups"] = perm_tags["allowed_groups"]
                    tags["sensitivity_level"] = perm_tags["sensitivity_level"]
                else:
                    logger.warning(
                        "No permission mapping for %s — will be quarantined at extraction",
                        s3_key,
                    )
```

**Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_daily_sync.py -v`
Expected: All tests PASS (existing + 2 new)

**Step 6: Commit**

```
git add src/config.py src/daily_sync.py tests/test_daily_sync.py
git commit -m "feat: add DynamoDB permission tags to daily sync uploads"
```

---

## Task 4: Modify `textract_trigger.py` with Quarantine Guard

**Files:**
- Modify: `src/textract_trigger.py` (add quarantine check before extraction)
- Test: `tests/test_textract_trigger.py` (add quarantine tests)

**Context:** Before starting extraction, check if the document has a permission mapping. If not, quarantine it and skip processing. This catches documents uploaded outside `daily_sync` (e.g., direct S3 uploads).

**Step 1: Write the failing tests**

Add to `tests/test_textract_trigger.py`:

```python
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
        MockRegistry.return_value.get_document.return_value = _sample_doc()
        MockConverter.return_value.get_extraction_strategy.return_value = "textract-direct"
        MockTagger.return_value.get_permission_tags.return_value = None

        from textract_trigger import handler
        result = handler(_s3_event("source/Unknown/doc.pdf"), None)

        body = json.loads(result["body"])
        assert body["quarantined"] == 1
        assert body["textract_jobs"] == 0
        MockQuarantine.return_value.quarantine_document.assert_called_once_with(
            "source/Unknown/doc.pdf", reason="no_mapping",
        )

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
        MockRegistry.return_value.get_document.return_value = _sample_doc()
        MockConverter.return_value.get_extraction_strategy.return_value = "textract-direct"
        MockTextract.return_value.start_document_analysis.return_value = "job-123"
        MockTagger.return_value.get_permission_tags.return_value = {
            "allowed_groups": "grp-hr-1",
            "sensitivity_level": "confidential",
            "matched_prefix": "source/Dynamo/HR",
        }

        from textract_trigger import handler
        result = handler(_s3_event("source/Dynamo/HR/doc.pdf"), None)

        body = json.loads(result["body"])
        assert body["textract_jobs"] == 1
        assert body["quarantined"] == 0
        MockQuarantine.return_value.quarantine_document.assert_not_called()
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_textract_trigger.py::TestTextractTriggerQuarantine -v`
Expected: FAIL

**Step 3: Modify `textract_trigger.py`**

Add imports:
```python
from permission_tagger import PermissionTagger
from quarantine import QuarantineManager
```

In `handler()`, after creating instances (line 37), add:
```python
    perm_tagger = PermissionTagger()
    quarantine_mgr = QuarantineManager()
```

Update results dict to include `"quarantined": 0`.

After the `doc = registry.get_document(s3_key)` check (after line 65), add:

```python
        # Permission check — quarantine unmapped documents
        perm_tags = perm_tagger.get_permission_tags(s3_key)
        if perm_tags is None:
            try:
                quarantine_mgr.quarantine_document(s3_key, reason="no_mapping")
                registry.update_textract_status(s3_key, "quarantined")
            except Exception:
                logger.exception("Failed to quarantine %s", s3_key)
            results["quarantined"] += 1
            continue
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_textract_trigger.py -v`
Expected: All tests PASS (existing + 2 new). Note: existing tests need their mocks updated to patch `PermissionTagger` and `QuarantineManager` as well. Update existing test class decorators to add those patches with default passthrough values.

**Step 5: Fix existing tests**

Existing `TestTextractTriggerHandler` tests need two additional patches. Add `@patch("textract_trigger.QuarantineManager")` and `@patch("textract_trigger.PermissionTagger")` to each test. The PermissionTagger mock should return a valid permission dict by default so existing tests continue through the quarantine guard:

```python
MockTagger.return_value.get_permission_tags.return_value = {
    "allowed_groups": "grp-test",
    "sensitivity_level": "internal",
    "matched_prefix": "source/Dynamo",
}
```

**Step 6: Run full test suite**

Run: `python -m pytest tests/test_textract_trigger.py -v`
Expected: All tests PASS

**Step 7: Commit**

```
git add src/textract_trigger.py tests/test_textract_trigger.py
git commit -m "feat: add quarantine guard to textract trigger"
```

---

## Task 5: Modify Digital Twin to Include Permission Metadata

**Files:**
- Modify: `src/digital_twin.py` — `_assemble_twin()` function
- Modify: `src/textract_trigger.py` — pass permission metadata to twin builder
- Modify: `src/textract_complete.py` — read permission tags from S3 and pass to twin
- Test: `tests/test_digital_twin.py` (add permission metadata tests)

**Context:** The twin JSON needs a `permissions` section so that downstream chunker can embed it.

**Step 1: Write the failing tests**

Add to `tests/test_digital_twin.py`:

```python
class TestPermissionMetadataInTwin:
    def test_twin_includes_permissions_section(self):
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
        twin = builder.build_twin_from_textract(
            {"Blocks": [{"BlockType": "LINE", "Text": "Hello", "Page": 1, "Id": "1"}]},
            source_meta,
        )
        assert "permissions" in twin
        assert twin["permissions"]["allowed_groups"] == ["grp-hr-1", "grp-hr-2"]
        assert twin["permissions"]["sensitivity_level"] == "confidential"

    def test_twin_permissions_default_to_empty(self):
        from digital_twin import DigitalTwinBuilder
        builder = DigitalTwinBuilder()
        source_meta = {
            "s3_source_key": "source/Dynamo/doc.pdf",
            "sp_library": "General",
            "file_type": ".pdf",
            "size_bytes": 1024,
        }
        twin = builder.build_twin_from_direct_extract("text", [], source_meta)
        assert "permissions" in twin
        assert twin["permissions"]["allowed_groups"] == []
        assert twin["permissions"]["sensitivity_level"] == ""
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_digital_twin.py::TestPermissionMetadataInTwin -v`
Expected: FAIL — `permissions` key not in twin

**Step 3: Modify `_assemble_twin()` in `digital_twin.py`**

Add after the `"extraction_metadata"` key in the return dict (line 169):

```python
        "permissions": {
            "allowed_groups": source_metadata.get("permissions", {}).get("allowed_groups", []),
            "sensitivity_level": source_metadata.get("permissions", {}).get("sensitivity_level", ""),
            "s3_prefix": source_metadata.get("permissions", {}).get("s3_prefix", ""),
            "custom_filters": source_metadata.get("permissions", {}).get("custom_filters", {}),
        },
```

**Step 4: Modify `textract_trigger.py` to pass permission metadata**

In `_handle_textract`, `_handle_direct_extract`, and `_handle_plain_text`: the registry `doc` dict is used as `source_metadata` for the twin builder. Before calling the builder, inject permission metadata:

```python
# In textract_trigger handler, after perm_tags check passes, store the metadata:
perm_metadata = perm_tagger.get_permission_metadata(s3_key)
```

Then pass `perm_metadata` into `_handle_direct_extract` and `_handle_plain_text`. Add it to the `doc` dict:

```python
if perm_metadata:
    doc["permissions"] = perm_metadata
```

For `_handle_textract` (async Textract path): permission metadata is not available at build time since the twin is built in `textract_complete.py`. Instead, the tags are already on the S3 object. `textract_complete.py` will read them.

**Step 5: Modify `textract_complete.py` to read permission tags**

After fetching the `doc` from registry (line 68), read S3 tags:

```python
            # Read permission tags from the source S3 object
            try:
                tag_resp = boto3.client("s3", region_name=config.aws_region).get_object_tagging(
                    Bucket=config.s3_bucket, Key=s3_key,
                )
                s3_tags = {t["Key"]: t["Value"] for t in tag_resp.get("TagSet", [])}
                if "allowed_groups" in s3_tags:
                    doc["permissions"] = {
                        "allowed_groups": s3_tags["allowed_groups"].split(",") if s3_tags["allowed_groups"] else [],
                        "sensitivity_level": s3_tags.get("sensitivity_level", ""),
                        "s3_prefix": s3_tags.get("matched_prefix", ""),
                        "custom_filters": {},
                    }
            except Exception:
                logger.warning("Could not read permission tags for %s", s3_key)
```

**Step 6: Run tests**

Run: `python -m pytest tests/test_digital_twin.py tests/test_textract_trigger.py tests/test_textract_complete.py -v`
Expected: All PASS

**Step 7: Commit**

```
git add src/digital_twin.py src/textract_trigger.py src/textract_complete.py tests/test_digital_twin.py
git commit -m "feat: propagate permission metadata into digital twins"
```

---

## Task 6: Modify Chunker to Include Permission Fields

**Files:**
- Modify: `src/chunker.py` — `chunk_document()` method
- Test: `tests/test_chunker.py` (add permission metadata tests)

**Context:** Each chunk must carry `allowed_groups`, `sensitivity_level`, `s3_prefix`, `document_id`, and `custom_filters` as top-level fields for vector store filtering.

**Step 1: Write the failing tests**

Add to `tests/test_chunker.py`:

```python
class TestChunkPermissionMetadata:
    def test_chunk_includes_permission_fields(self):
        chunker = DocumentChunker()
        twin = _make_twin(text="Some text.")
        twin["permissions"] = {
            "allowed_groups": ["grp-hr-1", "grp-hr-2"],
            "sensitivity_level": "confidential",
            "s3_prefix": "source/Dynamo/HR",
            "custom_filters": {"project_code": "P001"},
        }
        chunks = chunker.chunk_document(twin)
        assert len(chunks) >= 1

        chunk = chunks[0]
        assert chunk["allowed_groups"] == ["grp-hr-1", "grp-hr-2"]
        assert chunk["sensitivity_level"] == "confidential"
        assert chunk["s3_prefix"] == "source/Dynamo/HR"
        assert chunk["custom_filters"] == {"project_code": "P001"}

    def test_chunk_permissions_default_when_absent(self):
        chunker = DocumentChunker()
        twin = _make_twin(text="Some text.")
        # No permissions section
        chunks = chunker.chunk_document(twin)

        chunk = chunks[0]
        assert chunk["allowed_groups"] == []
        assert chunk["sensitivity_level"] == ""
        assert chunk["s3_prefix"] == ""
        assert chunk["custom_filters"] == {}

    def test_document_id_is_top_level(self):
        chunker = DocumentChunker()
        twin = _make_twin(text="Some text.")
        chunks = chunker.chunk_document(twin)
        assert chunks[0]["document_id"] == twin["document_id"]
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_chunker.py::TestChunkPermissionMetadata -v`
Expected: FAIL — `allowed_groups` not a key in chunk dict

**Step 3: Modify `chunker.py`**

In `chunk_document()`, after extracting `twin_meta` (line 77), extract permissions:

```python
        permissions = twin_json.get("permissions", {})
        perm_fields = {
            "allowed_groups": permissions.get("allowed_groups", []),
            "sensitivity_level": permissions.get("sensitivity_level", ""),
            "s3_prefix": permissions.get("s3_prefix", ""),
            "custom_filters": permissions.get("custom_filters", {}),
        }
```

In `_build_chunk()`, add the permission fields as top-level keys. Modify the method signature to accept `perm_fields` and merge them into the return dict.

Alternatively, add them in `chunk_document()` after building each chunk:

In the text chunk loop and table chunk loop, after `chunks.append(...)`, the permission fields get added in the back-fill loop. Actually, simplest: modify `_build_chunk` to accept `**extra_fields` and merge, or add them in the back-fill section at the end of `chunk_document()`:

```python
        # Back-fill total_chunks and permission fields
        total = len(chunks)
        for chunk in chunks:
            chunk["total_chunks"] = total
            chunk.update(perm_fields)
```

**Step 4: Run tests**

Run: `python -m pytest tests/test_chunker.py -v`
Expected: All tests PASS. Note: `test_chunk_has_required_keys` needs updating — add the new keys to `expected_keys`.

**Step 5: Commit**

```
git add src/chunker.py tests/test_chunker.py
git commit -m "feat: add permission metadata as top-level chunk fields"
```

---

## Task 7: Terraform — Quarantine SNS + IAM Updates + Lambda Env Vars

**Files:**
- Modify: `terraform/sns.tf` — add quarantine SNS topic
- Modify: `terraform/iam.tf` — add DynamoDB read + SNS publish to textract_trigger role
- Modify: `terraform/lambda.tf` — add env vars for permission table + quarantine SNS topic
- Modify: `terraform/outputs.tf` — add quarantine SNS output

**Step 1: Add quarantine SNS topic to `terraform/sns.tf`**

```hcl
resource "aws_sns_topic" "quarantine_alerts" {
  name = "doc-quarantine-alerts"
}
```

**Step 2: Add IAM permissions to `terraform/iam.tf`**

Add to `textract_trigger_lambda` policy:
- DynamoDB read on `permission_mappings` table (GetItem, Query, Scan)
- SNS publish on `quarantine_alerts` topic
- S3 CopyObject + DeleteObject on quarantine/ prefix (already has S3FullAccess)

Add to `daily_sync_lambda` policy (already done in Prompt 3 — verify `PermissionTablesRead` statement exists).

**Step 3: Add Lambda env vars to `terraform/lambda.tf`**

Add to `daily_sync` environment variables:
```hcl
PERMISSION_MAPPINGS_TABLE = var.permission_mappings_table_name
```

Add to `textract_trigger` environment variables:
```hcl
PERMISSION_MAPPINGS_TABLE  = var.permission_mappings_table_name
QUARANTINE_SNS_TOPIC_ARN   = aws_sns_topic.quarantine_alerts.arn
```

**Step 4: Add output**

```hcl
output "quarantine_sns_topic_arn" {
  description = "ARN of the quarantine alerts SNS topic"
  value       = aws_sns_topic.quarantine_alerts.arn
}
```

**Step 5: Validate**

Run: `cd terraform && terraform fmt -recursive && terraform validate`
Expected: Success

**Step 6: Commit**

```
git add terraform/
git commit -m "infra: add quarantine SNS topic and IAM permissions for permission tagger"
```

---

## Task 8: Retag Existing Documents Script

**Files:**
- Create: `scripts/retag_existing_documents.py`
- Create: `scripts/reprocess_quarantined.py`

**Context:** Batch scripts that apply permission tags to existing documents and re-process quarantined documents.

**Step 1: Create `scripts/retag_existing_documents.py`**

```python
#!/usr/bin/env python3
"""Retag existing S3 source documents with DynamoDB permission metadata.

Scans all objects under source/ prefix, looks up permission mappings, and
applies allowed_groups + sensitivity_level as S3 object tags.

Usage:
    python scripts/retag_existing_documents.py [--bucket BUCKET] [--dry-run] [--limit N]
"""

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import boto3
from lib.dynamo_permissions.client import PermissionClient


def retag_documents(
    bucket: str,
    prefix: str = "source/",
    dry_run: bool = False,
    limit: int = 0,
) -> dict:
    """Scan S3 and apply permission tags to each document.

    Returns stats dict with tagged, quarantine_candidates, errors, skipped.
    """
    s3 = boto3.client("s3", region_name="us-east-1")
    perm_client = PermissionClient()

    stats = {"tagged": 0, "quarantine_candidates": 0, "errors": 0, "skipped": 0, "total": 0}
    quarantine_list = []

    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            s3_key = obj["Key"]
            stats["total"] += 1

            if limit and stats["total"] > limit:
                return stats

            # Look up permission mapping
            perm = perm_client.get_allowed_groups(s3_key)

            if perm is None:
                stats["quarantine_candidates"] += 1
                quarantine_list.append(s3_key)
                if not dry_run:
                    print(f"  [QUARANTINE] {s3_key}")
                continue

            new_tags = {
                "allowed_groups": ",".join(perm.allowed_groups),
                "sensitivity_level": perm.sensitivity_level,
                "matched_prefix": perm.s3_prefix,
            }

            if dry_run:
                print(f"  [DRY RUN] {s3_key} -> {perm.sensitivity_level} ({len(perm.allowed_groups)} groups)")
                stats["tagged"] += 1
                continue

            try:
                # Read existing tags and merge
                existing = s3.get_object_tagging(Bucket=bucket, Key=s3_key)
                tag_set = {t["Key"]: t["Value"] for t in existing.get("TagSet", [])}
                tag_set.update(new_tags)

                s3.put_object_tagging(
                    Bucket=bucket,
                    Key=s3_key,
                    Tagging={
                        "TagSet": [{"Key": k, "Value": v} for k, v in tag_set.items()],
                    },
                )
                stats["tagged"] += 1

            except Exception as exc:
                print(f"  [ERROR] {s3_key}: {exc}")
                stats["errors"] += 1

    if quarantine_list:
        print(f"\nQuarantine candidates ({len(quarantine_list)}):")
        for key in quarantine_list[:20]:
            print(f"  {key}")
        if len(quarantine_list) > 20:
            print(f"  ... and {len(quarantine_list) - 20} more")

    return stats


def main():
    parser = argparse.ArgumentParser(description="Retag existing S3 documents with permission metadata")
    parser.add_argument("--bucket", default="dynamo-ai-documents")
    parser.add_argument("--prefix", default="source/")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="Process at most N documents")
    args = parser.parse_args()

    print(f"Scanning s3://{args.bucket}/{args.prefix}...")
    stats = retag_documents(args.bucket, args.prefix, args.dry_run, args.limit)

    print(f"\nResults:")
    print(f"  Total scanned: {stats['total']}")
    print(f"  Tagged:         {stats['tagged']}")
    print(f"  Quarantine:     {stats['quarantine_candidates']}")
    print(f"  Errors:         {stats['errors']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

**Step 2: Create `scripts/reprocess_quarantined.py`**

```python
#!/usr/bin/env python3
"""Re-process quarantined documents after their prefix mapping has been added.

Lists quarantined documents, checks if a mapping now exists, and moves
them back to source/ to re-trigger the ingestion pipeline.

Usage:
    python scripts/reprocess_quarantined.py [--bucket BUCKET] [--dry-run]
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import boto3
from lib.dynamo_permissions.client import PermissionClient


def reprocess_quarantined(bucket: str, dry_run: bool = False) -> dict:
    """Move quarantined docs back to source/ if a mapping now exists."""
    s3 = boto3.client("s3", region_name="us-east-1")
    perm_client = PermissionClient()

    stats = {"reprocessed": 0, "still_unmapped": 0, "errors": 0, "total": 0}

    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix="quarantine/"):
        for obj in page.get("Contents", []):
            quarantine_key = obj["Key"]
            stats["total"] += 1

            # Reconstruct original source key
            source_key = "source/" + quarantine_key[len("quarantine/"):]

            # Check if mapping now exists
            perm = perm_client.get_allowed_groups(source_key)
            if perm is None:
                stats["still_unmapped"] += 1
                if not dry_run:
                    print(f"  [STILL UNMAPPED] {quarantine_key}")
                continue

            if dry_run:
                print(f"  [DRY RUN] {quarantine_key} -> {source_key}")
                stats["reprocessed"] += 1
                continue

            try:
                # Copy back to source/
                s3.copy_object(
                    Bucket=bucket,
                    Key=source_key,
                    CopySource={"Bucket": bucket, "Key": quarantine_key},
                )
                # Delete from quarantine/
                s3.delete_object(Bucket=bucket, Key=quarantine_key)
                stats["reprocessed"] += 1
                print(f"  [REPROCESSED] {quarantine_key} -> {source_key}")

            except Exception as exc:
                print(f"  [ERROR] {quarantine_key}: {exc}")
                stats["errors"] += 1

    return stats


def main():
    parser = argparse.ArgumentParser(description="Re-process quarantined documents")
    parser.add_argument("--bucket", default="dynamo-ai-documents")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print(f"Scanning quarantine/ in s3://{args.bucket}...")
    stats = reprocess_quarantined(args.bucket, args.dry_run)

    print(f"\nResults:")
    print(f"  Total quarantined: {stats['total']}")
    print(f"  Reprocessed:       {stats['reprocessed']}")
    print(f"  Still unmapped:    {stats['still_unmapped']}")
    print(f"  Errors:            {stats['errors']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

**Step 3: Commit**

```
git add scripts/retag_existing_documents.py scripts/reprocess_quarantined.py
git commit -m "feat: add retag and reprocess quarantine scripts"
```

---

## Task 9: Integration Tests

**Files:**
- Create: `tests/test_permission_pipeline_integration.py`

**Context:** Verify the full flow: document → permission tags → twin with metadata → chunks with permission fields. Also test the quarantine flow.

**Step 1: Write integration tests using moto**

```python
"""Integration tests for permission tagging through the full pipeline.

Uses moto for S3 + DynamoDB mocking to test:
1. Tagged document → twin includes permissions → chunks include permissions
2. Unmapped document → quarantined
3. Quarantined document → reprocessed after mapping added
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
        mgr._bucket = BUCKET
        mgr._sns_topic_arn = aws_env["topic_arn"]

        q_key = mgr.quarantine_document("source/Unknown/doc.pdf", reason="no_mapping")
        assert q_key == "quarantine/Unknown/doc.pdf"

        # Verify quarantine copy exists
        resp = s3.get_object(Bucket=BUCKET, Key="quarantine/Unknown/doc.pdf")
        assert resp["Body"].read() == b"content"

        # Verify source deleted
        with pytest.raises(s3.exceptions.NoSuchKey):
            s3.get_object(Bucket=BUCKET, Key="source/Unknown/doc.pdf")
```

**Step 2: Run integration tests**

Run: `python -m pytest tests/test_permission_pipeline_integration.py -v`
Expected: All PASS

**Step 3: Commit**

```
git add tests/test_permission_pipeline_integration.py
git commit -m "test: add permission pipeline integration tests"
```

---

## Task 10: Run Full Test Suite and Validate

**Step 1: Run all tests**

Run: `python -m pytest tests/ -v --tb=short`
Expected: All tests PASS

**Step 2: Terraform validate**

Run: `cd terraform && terraform fmt -recursive && terraform validate`
Expected: Success

**Step 3: Final commit**

If any fixes were needed, commit them.

```
git add -A
git commit -m "chore: permission tagger pipeline complete — all tests passing"
```
