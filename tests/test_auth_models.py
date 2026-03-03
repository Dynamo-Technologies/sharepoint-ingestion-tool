"""Tests for auth models — AuthenticatedUser dataclass."""

from __future__ import annotations

import pytest

from lib.auth.models import AuthenticatedUser


class TestAuthenticatedUser:
    def test_basic_construction(self):
        user = AuthenticatedUser(
            user_id="u-123",
            upn="alice@contoso.com",
            groups=["g1", "g2"],
        )
        assert user.user_id == "u-123"
        assert user.upn == "alice@contoso.com"
        assert user.groups == ["g1", "g2"]

    def test_defaults(self):
        user = AuthenticatedUser(user_id="u-1", upn="a@b.com")
        assert user.groups == []

    def test_to_dict(self):
        user = AuthenticatedUser(
            user_id="u-1", upn="a@b.com", groups=["g1"],
        )
        d = user.to_dict()
        assert d == {"user_id": "u-1", "upn": "a@b.com", "groups": ["g1"]}

    def test_from_authorizer_context(self):
        ctx = {"user_id": "u-1", "upn": "a@b.com", "groups": "g1,g2"}
        user = AuthenticatedUser.from_authorizer_context(ctx)
        assert user.user_id == "u-1"
        assert user.upn == "a@b.com"
        assert user.groups == ["g1", "g2"]

    def test_from_authorizer_context_empty_groups(self):
        ctx = {"user_id": "u-1", "upn": "a@b.com", "groups": ""}
        user = AuthenticatedUser.from_authorizer_context(ctx)
        assert user.groups == []
