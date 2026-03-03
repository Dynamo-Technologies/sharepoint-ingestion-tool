"""Tests for token validator — API key + JWT validation."""

from __future__ import annotations

import os

import pytest
from unittest.mock import patch

from lib.auth.token_validator import TokenValidator, AuthError


class TestApiKeyValidation:
    def test_valid_api_key(self):
        validator = TokenValidator(api_keys=["key-abc-123", "key-def-456"])
        user = validator.validate_api_key("key-abc-123", key_user_map={
            "key-abc-123": {"user_id": "u-1", "upn": "alice@test.com", "groups": ["g1"]},
        })
        assert user.user_id == "u-1"
        assert user.upn == "alice@test.com"
        assert user.groups == ["g1"]

    def test_invalid_api_key_raises(self):
        validator = TokenValidator(api_keys=["key-abc-123"])
        with pytest.raises(AuthError, match="Invalid API key"):
            validator.validate_api_key("wrong-key", key_user_map={})

    def test_empty_api_key_raises(self):
        validator = TokenValidator(api_keys=["key-abc-123"])
        with pytest.raises(AuthError, match="Missing API key"):
            validator.validate_api_key("", key_user_map={})

    def test_api_key_not_in_map_returns_default_user(self):
        validator = TokenValidator(api_keys=["key-abc-123"])
        user = validator.validate_api_key("key-abc-123", key_user_map={})
        assert user.user_id == "api-key-user"
        assert user.upn == ""
        assert user.groups == []


class TestExtractBearerToken:
    def test_valid_bearer_header(self):
        token = TokenValidator.extract_bearer_token("Bearer my-token-123")
        assert token == "my-token-123"

    def test_missing_header_raises(self):
        with pytest.raises(AuthError, match="Missing Authorization header"):
            TokenValidator.extract_bearer_token("")

    def test_none_header_raises(self):
        with pytest.raises(AuthError, match="Missing Authorization header"):
            TokenValidator.extract_bearer_token(None)

    def test_wrong_scheme_raises(self):
        with pytest.raises(AuthError, match="Invalid Authorization scheme"):
            TokenValidator.extract_bearer_token("Basic dXNlcjpwYXNz")

    def test_bearer_only_no_token_raises(self):
        with pytest.raises(AuthError, match="Missing token"):
            TokenValidator.extract_bearer_token("Bearer ")
