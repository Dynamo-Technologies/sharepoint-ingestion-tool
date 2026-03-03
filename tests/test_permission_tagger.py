"""Tests for PermissionTagger — wraps PermissionClient with S3-tag helpers."""

from unittest.mock import MagicMock, patch

import pytest

from permission_tagger import PermissionTagger


# ===================================================================
# Helpers
# ===================================================================

def _make_prefix_permission(**overrides):
    """Build a mock PrefixPermission with sensible defaults."""
    defaults = {
        "s3_prefix": "source/Dynamo/HR",
        "allowed_groups": ["grp-hr-1", "grp-hr-2"],
        "sensitivity_level": "confidential",
        "custom_filters": {},
        "last_updated": "2025-01-01T00:00:00Z",
        "updated_by": "seed",
    }
    defaults.update(overrides)
    obj = MagicMock()
    for k, v in defaults.items():
        setattr(obj, k, v)
    return obj


# ===================================================================
# Fixtures
# ===================================================================

@pytest.fixture
def mock_permission_client():
    """Return a MagicMock standing in for PermissionClient."""
    return MagicMock()


@pytest.fixture
def tagger(mock_permission_client):
    """Create a PermissionTagger with an injected mock client."""
    with patch("permission_tagger.PermissionClient", return_value=mock_permission_client):
        t = PermissionTagger(permission_table_name="test-table")
    return t


# ===================================================================
# get_permission_tags tests
# ===================================================================

class TestGetPermissionTags:
    def test_get_permission_tags_returns_dict(self, tagger, mock_permission_client):
        """Tags dict has allowed_groups (comma-separated), sensitivity_level, matched_prefix."""
        perm = _make_prefix_permission(
            allowed_groups=["grp-hr-1", "grp-hr-2"],
            sensitivity_level="confidential",
            s3_prefix="source/Dynamo/HR",
        )
        mock_permission_client.get_allowed_groups.return_value = perm

        result = tagger.get_permission_tags("source/Dynamo/HR/doc.pdf")

        assert isinstance(result, dict)
        assert result["allowed_groups"] == "grp-hr-1,grp-hr-2"
        assert result["sensitivity_level"] == "confidential"
        assert result["matched_prefix"] == "source/Dynamo/HR"
        mock_permission_client.get_allowed_groups.assert_called_once_with(
            "source/Dynamo/HR/doc.pdf"
        )

    def test_get_permission_tags_no_mapping_returns_none(self, tagger, mock_permission_client):
        """When PermissionClient returns None (no mapping), tagger returns None."""
        mock_permission_client.get_allowed_groups.return_value = None

        result = tagger.get_permission_tags("unknown-bucket/file.txt")

        assert result is None

    def test_get_permission_tags_with_custom_filters(self, tagger, mock_permission_client):
        """Custom filters are encoded as 'key=value' comma-separated string."""
        perm = _make_prefix_permission(
            custom_filters={"project_code": "P001", "region": "us-east"},
        )
        mock_permission_client.get_allowed_groups.return_value = perm

        result = tagger.get_permission_tags("source/Dynamo/HR/doc.pdf")

        assert "custom_filters" in result
        # The value should contain both key=value pairs, order may vary
        parts = result["custom_filters"].split(",")
        assert len(parts) == 2
        assert "project_code=P001" in parts
        assert "region=us-east" in parts

    def test_get_permission_tags_no_custom_filters_omits_key(self, tagger, mock_permission_client):
        """When custom_filters is empty, the key should not appear in the tags dict."""
        perm = _make_prefix_permission(custom_filters={})
        mock_permission_client.get_allowed_groups.return_value = perm

        result = tagger.get_permission_tags("source/Dynamo/HR/doc.pdf")

        assert "custom_filters" not in result

    def test_empty_allowed_groups(self, tagger, mock_permission_client):
        """Empty allowed_groups list produces an empty string value."""
        perm = _make_prefix_permission(allowed_groups=[])
        mock_permission_client.get_allowed_groups.return_value = perm

        result = tagger.get_permission_tags("source/Dynamo/Public/doc.pdf")

        assert result is not None
        assert result["allowed_groups"] == ""


# ===================================================================
# get_permission_metadata tests
# ===================================================================

class TestGetPermissionMetadata:
    def test_get_permission_metadata_returns_full_dict(self, tagger, mock_permission_client):
        """Metadata dict has native Python types suitable for JSON embedding."""
        perm = _make_prefix_permission(
            allowed_groups=["grp-hr-1", "grp-hr-2"],
            sensitivity_level="confidential",
            s3_prefix="source/Dynamo/HR",
            custom_filters={"project_code": "P001"},
        )
        mock_permission_client.get_allowed_groups.return_value = perm

        result = tagger.get_permission_metadata("source/Dynamo/HR/doc.pdf")

        assert isinstance(result, dict)
        assert result["allowed_groups"] == ["grp-hr-1", "grp-hr-2"]
        assert result["sensitivity_level"] == "confidential"
        assert result["s3_prefix"] == "source/Dynamo/HR"
        assert result["custom_filters"] == {"project_code": "P001"}
        mock_permission_client.get_allowed_groups.assert_called_once_with(
            "source/Dynamo/HR/doc.pdf"
        )

    def test_get_permission_metadata_no_mapping_returns_none(
        self, tagger, mock_permission_client
    ):
        """When no mapping exists, metadata returns None."""
        mock_permission_client.get_allowed_groups.return_value = None

        result = tagger.get_permission_metadata("unknown-bucket/file.txt")

        assert result is None

    def test_get_permission_metadata_empty_custom_filters(
        self, tagger, mock_permission_client
    ):
        """Empty custom_filters still appears as empty dict in metadata."""
        perm = _make_prefix_permission(custom_filters={})
        mock_permission_client.get_allowed_groups.return_value = perm

        result = tagger.get_permission_metadata("source/Dynamo/HR/doc.pdf")

        assert result["custom_filters"] == {}

    def test_get_permission_metadata_allowed_groups_is_list(
        self, tagger, mock_permission_client
    ):
        """allowed_groups in metadata must be a list, not a string."""
        perm = _make_prefix_permission(allowed_groups=["grp-1"])
        mock_permission_client.get_allowed_groups.return_value = perm

        result = tagger.get_permission_metadata("source/Dynamo/HR/doc.pdf")

        assert isinstance(result["allowed_groups"], list)
