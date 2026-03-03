"""Tests for the permission-filtered query middleware."""

import json
import hashlib
import logging
from unittest.mock import MagicMock

import pytest

from lib.query_middleware.group_resolver import GroupResolver
from lib.query_middleware.filter_builder import FilterBuilder
from lib.query_middleware.audit_logger import AuditLogger


# ===================================================================
# Helpers
# ===================================================================

def _make_user_group_result(**overrides):
    """Build a mock UserGroupResult with sensible defaults."""
    defaults = {
        "user_id": "user-001",
        "groups": ["grp-hr-1", "grp-finance-1"],
        "upn": "alice@dynamo.com",
        "custom_attributes": {"ext_ClearanceLevel": "confidential"},
        "last_synced": "2026-03-01T00:00:00Z",
        "source": "scim",
        "cache_hit": True,
        "cache_expired": False,
    }
    defaults.update(overrides)
    obj = MagicMock()
    for k, v in defaults.items():
        setattr(obj, k, v)
    return obj


# ===================================================================
# GroupResolver tests
# ===================================================================

class TestGroupResolver:
    def _make_resolver(self, cache_result=None):
        """Create a GroupResolver with a mocked PermissionClient."""
        mock_client = MagicMock()
        if cache_result is not None:
            mock_client.get_user_groups.return_value = cache_result
        else:
            mock_client.get_user_groups.return_value = _make_user_group_result(
                cache_hit=False, groups=[]
            )
        mock_client.get_user_sensitivity_ceiling.return_value = "internal"
        resolver = GroupResolver(permission_client=mock_client)
        return resolver, mock_client

    def test_saml_groups_only_cache_miss(self):
        """When cache misses, return SAML groups as-is."""
        resolver, mock_client = self._make_resolver(
            cache_result=_make_user_group_result(cache_hit=False, groups=[])
        )
        result = resolver.resolve("user-001", saml_groups=["grp-a", "grp-b"])

        assert set(result.groups) == {"grp-a", "grp-b"}
        assert result.cache_hit is False

    def test_cache_hit_merges_with_saml(self):
        """When cache hits, merge SAML + cache groups and deduplicate."""
        resolver, _ = self._make_resolver(
            cache_result=_make_user_group_result(
                groups=["grp-hr-1", "grp-finance-1"], cache_hit=True
            )
        )
        result = resolver.resolve("user-001", saml_groups=["grp-hr-1", "grp-new"])

        assert "grp-hr-1" in result.groups
        assert "grp-finance-1" in result.groups
        assert "grp-new" in result.groups
        # No duplicates
        assert len(result.groups) == len(set(result.groups))

    def test_cache_expired_still_uses_groups(self):
        """Expired cache still returns groups but flags cache_expired."""
        resolver, _ = self._make_resolver(
            cache_result=_make_user_group_result(
                groups=["grp-old"], cache_hit=True, cache_expired=True
            )
        )
        result = resolver.resolve("user-001", saml_groups=[])

        assert "grp-old" in result.groups
        assert result.cache_expired is True

    def test_empty_saml_falls_back_to_cache(self):
        """With no SAML groups, use cache groups only."""
        resolver, _ = self._make_resolver(
            cache_result=_make_user_group_result(groups=["grp-cached"], cache_hit=True)
        )
        result = resolver.resolve("user-001", saml_groups=[])

        assert result.groups == ["grp-cached"]

    def test_both_empty_returns_empty(self):
        """No SAML groups and no cache -> empty groups list."""
        resolver, _ = self._make_resolver(
            cache_result=_make_user_group_result(
                cache_hit=False, groups=[]
            )
        )
        result = resolver.resolve("user-001", saml_groups=[])

        assert result.groups == []

    def test_upn_from_cache(self):
        """UPN is taken from cache result when available."""
        resolver, _ = self._make_resolver(
            cache_result=_make_user_group_result(upn="alice@dynamo.com", cache_hit=True)
        )
        result = resolver.resolve("user-001", saml_groups=[])

        assert result.upn == "alice@dynamo.com"

    def test_custom_attributes_from_cache(self):
        """Custom attributes are taken from cache result."""
        resolver, _ = self._make_resolver(
            cache_result=_make_user_group_result(
                custom_attributes={"ext_ClearanceLevel": "confidential"},
                cache_hit=True,
            )
        )
        result = resolver.resolve("user-001", saml_groups=[])

        assert result.custom_attributes == {"ext_ClearanceLevel": "confidential"}

    def test_sensitivity_ceiling_from_permission_client(self):
        """Sensitivity ceiling is fetched from PermissionClient."""
        resolver, mock_client = self._make_resolver(
            cache_result=_make_user_group_result(cache_hit=True)
        )
        mock_client.get_user_sensitivity_ceiling.return_value = "confidential"
        result = resolver.resolve("user-001", saml_groups=[])

        assert result.sensitivity_ceiling == "confidential"


# ===================================================================
# FilterBuilder tests
# ===================================================================

class TestFilterBuilder:
    def test_single_group_produces_list_contains(self):
        """One group → single listContains (no orAll wrapper needed)."""
        builder = FilterBuilder()
        f = builder.build_filter(groups=["grp-hr-1"], sensitivity_ceiling="confidential")

        # Should have andAll with group filter + sensitivity filter
        assert "andAll" in f
        conditions = f["andAll"]
        assert len(conditions) == 2

    def test_multiple_groups_produces_or_all(self):
        """Multiple groups → orAll of listContains entries."""
        builder = FilterBuilder()
        f = builder.build_filter(
            groups=["grp-hr-1", "grp-finance-1"],
            sensitivity_ceiling="confidential",
        )

        and_conditions = f["andAll"]
        # First condition is the group filter (orAll)
        group_filter = and_conditions[0]
        assert "orAll" in group_filter
        list_contains = group_filter["orAll"]
        assert len(list_contains) == 2

        # Each should be a listContains
        for lc in list_contains:
            assert "listContains" in lc
            assert lc["listContains"]["key"] == "allowed_groups"

        values = {lc["listContains"]["value"] for lc in list_contains}
        assert values == {"grp-hr-1", "grp-finance-1"}

    def test_sensitivity_ceiling_maps_to_numeric(self):
        """Sensitivity ceiling is converted to numeric for lessThanOrEquals."""
        builder = FilterBuilder()

        for level, expected_num in [
            ("public", 0),
            ("internal", 1),
            ("confidential", 2),
            ("restricted", 3),
        ]:
            f = builder.build_filter(groups=["grp-a"], sensitivity_ceiling=level)
            sensitivity_filter = f["andAll"][1]
            assert sensitivity_filter["lessThanOrEquals"]["key"] == "sensitivity_level_numeric"
            assert sensitivity_filter["lessThanOrEquals"]["value"] == expected_num

    def test_combined_filter_structure(self):
        """Full filter has andAll wrapping [group_filter, sensitivity_filter]."""
        builder = FilterBuilder()
        f = builder.build_filter(
            groups=["grp-hr-1", "grp-finance-1"],
            sensitivity_ceiling="confidential",
        )

        assert "andAll" in f
        assert len(f["andAll"]) == 2

        # Group filter
        group_filter = f["andAll"][0]
        assert "orAll" in group_filter

        # Sensitivity filter
        sens_filter = f["andAll"][1]
        assert "lessThanOrEquals" in sens_filter
        assert sens_filter["lessThanOrEquals"]["value"] == 2

    def test_empty_groups_returns_impossible_filter(self):
        """No groups → filter that matches nothing (empty orAll)."""
        builder = FilterBuilder()
        f = builder.build_filter(groups=[], sensitivity_ceiling="internal")

        # With no groups, the group filter should ensure nothing matches.
        # We use a listContains with a UUID that no document will have.
        and_conditions = f["andAll"]
        group_filter = and_conditions[0]
        assert "listContains" in group_filter
        assert group_filter["listContains"]["value"] == "__no_access__"

    def test_unknown_sensitivity_defaults_to_public(self):
        """Unknown sensitivity string defaults to public (0)."""
        builder = FilterBuilder()
        f = builder.build_filter(groups=["grp-a"], sensitivity_ceiling="unknown_level")

        sens_filter = f["andAll"][1]
        assert sens_filter["lessThanOrEquals"]["value"] == 0


# ===================================================================
# AuditLogger tests
# ===================================================================

class TestAuditLogger:
    def test_log_entry_has_required_fields(self, caplog):
        """Audit log must contain all required fields."""
        logger_instance = AuditLogger()

        with caplog.at_level(logging.INFO, logger="query_middleware.audit"):
            logger_instance.log_query(
                user_id="user-001",
                user_upn="alice@dynamo.com",
                resolved_groups=["grp-hr-1"],
                filters_applied={"andAll": []},
                chunk_ids=["abc_0", "abc_1"],
                document_ids=["abc"],
                sensitivity_levels=["confidential"],
                query_text="What is the PTO policy?",
                latency_ms=150,
                result_type="success",
            )

        assert len(caplog.records) == 1
        entry = json.loads(caplog.records[0].message)

        required_fields = {
            "timestamp", "user_id", "user_upn", "resolved_groups",
            "filters_applied", "chunk_ids_retrieved", "source_document_ids",
            "sensitivity_levels", "query_text_hash", "response_latency_ms",
            "result_type",
        }
        assert required_fields.issubset(set(entry.keys()))

    def test_query_text_is_hashed_not_stored(self, caplog):
        """Query text must be SHA-256 hashed, not stored in plaintext."""
        logger_instance = AuditLogger()
        query = "What is the PTO policy?"

        with caplog.at_level(logging.INFO, logger="query_middleware.audit"):
            logger_instance.log_query(
                user_id="user-001",
                user_upn="",
                resolved_groups=[],
                filters_applied={},
                chunk_ids=[],
                document_ids=[],
                sensitivity_levels=[],
                query_text=query,
                latency_ms=100,
                result_type="no_results",
            )

        entry = json.loads(caplog.records[0].message)

        # Must NOT contain the original query text
        assert query not in json.dumps(entry)

        # Must contain the SHA-256 hash
        expected_hash = hashlib.sha256(query.encode()).hexdigest()
        assert entry["query_text_hash"] == expected_hash

    def test_permission_scoped_null_logged_distinctly(self, caplog):
        """permission_scoped_null result type appears in log."""
        logger_instance = AuditLogger()

        with caplog.at_level(logging.INFO, logger="query_middleware.audit"):
            logger_instance.log_query(
                user_id="user-001",
                user_upn="bob@dynamo.com",
                resolved_groups=["grp-general"],
                filters_applied={"andAll": []},
                chunk_ids=[],
                document_ids=[],
                sensitivity_levels=[],
                query_text="Tell me about HR policies",
                latency_ms=200,
                result_type="no_results",
            )

        entry = json.loads(caplog.records[0].message)
        assert entry["result_type"] == "no_results"

    def test_log_entry_is_valid_json(self, caplog):
        """Log message must be valid JSON (for CloudWatch parsing)."""
        logger_instance = AuditLogger()

        with caplog.at_level(logging.INFO, logger="query_middleware.audit"):
            logger_instance.log_query(
                user_id="user-001",
                user_upn="",
                resolved_groups=[],
                filters_applied={},
                chunk_ids=[],
                document_ids=[],
                sensitivity_levels=[],
                query_text="test",
                latency_ms=50,
                result_type="success",
            )

        # Should not raise
        entry = json.loads(caplog.records[0].message)
        assert isinstance(entry, dict)
