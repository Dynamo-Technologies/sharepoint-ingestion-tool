"""Tests for API Gateway Lambda authorizer."""

from __future__ import annotations

import json
import os

import pytest

API_KEYS = "test-key-1,test-key-2"
KEY_USER_MAP = json.dumps({
    "test-key-1": {"user_id": "u-1", "upn": "alice@test.com", "groups": ["g1", "g2"]},
    "test-key-2": {"user_id": "u-2", "upn": "bob@test.com", "groups": ["g3"]},
})


@pytest.fixture
def _env(monkeypatch):
    monkeypatch.setenv("API_KEYS", API_KEYS)
    monkeypatch.setenv("API_KEY_USER_MAP", KEY_USER_MAP)
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")


def _make_event(auth_header: str | None = None) -> dict:
    """Build a minimal API Gateway HTTP API authorizer event."""
    headers = {}
    if auth_header is not None:
        headers["authorization"] = auth_header
    return {
        "type": "REQUEST",
        "routeArn": "arn:aws:execute-api:us-east-1:123:api-id/stage/POST/query",
        "headers": headers,
        "requestContext": {
            "http": {"method": "POST", "path": "/query"},
        },
    }


class TestApiAuthorizer:
    def test_valid_api_key_returns_authorized(self, _env):
        from api_authorizer import handler
        result = handler(_make_event("Bearer test-key-1"), None)

        assert result["isAuthorized"] is True
        assert result["context"]["user_id"] == "u-1"
        assert result["context"]["upn"] == "alice@test.com"
        assert result["context"]["groups"] == "g1,g2"

    def test_invalid_api_key_returns_unauthorized(self, _env):
        from api_authorizer import handler
        result = handler(_make_event("Bearer wrong-key"), None)

        assert result["isAuthorized"] is False

    def test_missing_auth_header_returns_unauthorized(self, _env):
        from api_authorizer import handler
        result = handler(_make_event(), None)

        assert result["isAuthorized"] is False

    def test_wrong_scheme_returns_unauthorized(self, _env):
        from api_authorizer import handler
        result = handler(_make_event("Basic dXNlcjpwYXNz"), None)

        assert result["isAuthorized"] is False

    def test_second_api_key_maps_correctly(self, _env):
        from api_authorizer import handler
        result = handler(_make_event("Bearer test-key-2"), None)

        assert result["isAuthorized"] is True
        assert result["context"]["user_id"] == "u-2"
        assert result["context"]["groups"] == "g3"
