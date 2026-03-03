"""Tests for query-handler Lambda — POST /query, GET /health, GET /user/permissions."""

from __future__ import annotations

import json
import os

import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture
def _env(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_BASE_ID", "kb-test-123")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "anthropic.claude-3-sonnet-20240229-v1:0")
    monkeypatch.setenv("GUARDRAIL_ID", "gr-test-abc")
    monkeypatch.setenv("GUARDRAIL_VERSION", "1")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")


def _make_event(
    method: str,
    path: str,
    body: dict | None = None,
    auth_context: dict | None = None,
) -> dict:
    """Build a minimal HTTP API v2 proxy event."""
    event = {
        "requestContext": {
            "http": {"method": method, "path": path},
        },
    }
    if auth_context:
        event["requestContext"]["authorizer"] = {"lambda": auth_context}
    if body is not None:
        event["body"] = json.dumps(body)
    return event


class TestHealthRoute:
    def test_health_returns_ok(self, _env):
        from query_handler import handler
        result = handler(
            _make_event("GET", "/health"), None,
        )
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["status"] == "healthy"


class TestUserPermissionsRoute:
    @patch("query_handler.GroupResolver")
    def test_returns_user_permissions(self, MockResolver, _env):
        from lib.query_middleware.group_resolver import ResolvedUser
        mock_inst = MagicMock()
        mock_inst.resolve.return_value = ResolvedUser(
            user_id="u-1",
            upn="alice@test.com",
            groups=["g1", "g2"],
            sensitivity_ceiling="confidential",
        )
        MockResolver.return_value = mock_inst

        from query_handler import handler
        result = handler(
            _make_event("GET", "/user/permissions", auth_context={
                "user_id": "u-1", "upn": "alice@test.com", "groups": "g1,g2",
            }),
            None,
        )

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["user_id"] == "u-1"
        assert body["upn"] == "alice@test.com"
        assert "g1" in body["groups"]
        assert body["sensitivity_ceiling"] == "confidential"


class TestQueryRoute:
    @patch("query_handler.QueryMiddleware")
    @patch("query_handler.LLMRouter")
    def test_query_returns_response(self, MockRouter, MockMiddleware, _env):
        mock_router_inst = MagicMock()
        mock_router_inst.select_model.return_value = "anthropic.claude-3-sonnet-20240229-v1:0"
        MockRouter.return_value = mock_router_inst

        mock_mw_inst = MagicMock()
        mock_mw_inst.query.return_value = {
            "response_text": "Revenue is $1M",
            "citations": [],
            "result_type": "success",
            "chunks_retrieved": 3,
        }
        MockMiddleware.return_value = mock_mw_inst

        from query_handler import handler
        result = handler(
            _make_event("POST", "/query",
                body={"query": "What is revenue?"},
                auth_context={
                    "user_id": "u-1", "upn": "alice@test.com", "groups": "g1",
                },
            ),
            None,
        )

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["response_text"] == "Revenue is $1M"

    @patch("query_handler.QueryMiddleware")
    @patch("query_handler.LLMRouter")
    def test_query_missing_body_returns_400(self, MockRouter, MockMiddleware, _env):
        from query_handler import handler
        result = handler(
            _make_event("POST", "/query",
                auth_context={"user_id": "u-1", "upn": "a@b.com", "groups": ""},
            ),
            None,
        )
        assert result["statusCode"] == 400

    def test_query_without_auth_returns_401(self, _env):
        from query_handler import handler
        result = handler(
            _make_event("POST", "/query", body={"query": "test"}),
            None,
        )
        assert result["statusCode"] == 401


class TestUnknownRoute:
    def test_unknown_route_returns_404(self, _env):
        from query_handler import handler
        result = handler(
            _make_event("GET", "/unknown"), None,
        )
        assert result["statusCode"] == 404
