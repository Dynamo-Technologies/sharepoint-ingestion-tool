"""Tests for the permission-filtered query middleware."""

from unittest.mock import MagicMock

import pytest

from lib.query_middleware.group_resolver import GroupResolver


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
