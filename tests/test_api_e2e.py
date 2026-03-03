"""End-to-end test: authenticated query flow through authorizer + query handler."""

from __future__ import annotations

import json
import os

import pytest
from unittest.mock import MagicMock, patch

API_KEYS = "e2e-key-1"
KEY_USER_MAP = json.dumps({
    "e2e-key-1": {"user_id": "u-e2e", "upn": "e2e@test.com", "groups": ["g-exec", "g-finance"]},
})


@pytest.fixture
def _env(monkeypatch):
    monkeypatch.setenv("API_KEYS", API_KEYS)
    monkeypatch.setenv("API_KEY_USER_MAP", KEY_USER_MAP)
    monkeypatch.setenv("KNOWLEDGE_BASE_ID", "kb-e2e")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "anthropic.claude-3-sonnet-20240229-v1:0")
    monkeypatch.setenv("GUARDRAIL_ID", "gr-e2e")
    monkeypatch.setenv("GUARDRAIL_VERSION", "1")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")


class TestE2EAuthenticatedQuery:
    @patch("query_handler.QueryMiddleware")
    def test_full_authenticated_flow(self, MockMiddleware, _env):
        """Authorizer → query handler → LLM router → QueryMiddleware → response."""
        mock_mw = MagicMock()
        mock_mw.query.return_value = {
            "response_text": "Q4 revenue targets are $5M.",
            "citations": [{"document_id": "d1", "chunk_id": "c1"}],
            "result_type": "success",
            "chunks_retrieved": 3,
        }
        MockMiddleware.return_value = mock_mw

        # Step 1: Authorizer validates token
        from api_authorizer import handler as auth_handler
        auth_event = {
            "type": "REQUEST",
            "routeArn": "arn:aws:execute-api:us-east-1:123:api/stage/POST/query",
            "headers": {"authorization": "Bearer e2e-key-1"},
            "requestContext": {"http": {"method": "POST", "path": "/query"}},
        }
        auth_result = auth_handler(auth_event, None)
        assert auth_result["isAuthorized"] is True

        # Step 2: Query handler receives authorized context
        from query_handler import handler as query_handler_fn
        query_event = {
            "requestContext": {
                "http": {"method": "POST", "path": "/query"},
                "authorizer": {"lambda": auth_result["context"]},
            },
            "body": json.dumps({
                "query": "What are our Q4 revenue targets?",
                "complexity_hint": "auto",
            }),
        }
        result = query_handler_fn(query_event, None)

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["result_type"] == "success"
        assert "revenue" in body["response_text"].lower()

        # Verify QueryMiddleware was called with correct user context
        call_kwargs = mock_mw.query.call_args.kwargs
        assert call_kwargs["user_id"] == "u-e2e"
        assert "g-exec" in call_kwargs["user_groups"]
        assert "g-finance" in call_kwargs["user_groups"]

    def test_unauthorized_query_returns_privacy_safe_denial(self, _env):
        """Invalid token → authorizer denies → query handler returns 401."""
        from api_authorizer import handler as auth_handler
        auth_result = auth_handler({
            "type": "REQUEST",
            "routeArn": "arn:aws:execute-api:us-east-1:123:api/stage/POST/query",
            "headers": {"authorization": "Bearer invalid-key"},
            "requestContext": {"http": {"method": "POST", "path": "/query"}},
        }, None)

        assert auth_result["isAuthorized"] is False

        # Query handler without auth context
        from query_handler import handler as query_handler_fn
        result = query_handler_fn({
            "requestContext": {
                "http": {"method": "POST", "path": "/query"},
            },
            "body": json.dumps({"query": "Show me everything"}),
        }, None)

        assert result["statusCode"] == 401
        body = json.loads(result["body"])
        # Should not leak any data — just an error
        assert "error" in body
        assert "Unauthorized" in body["error"]

    @patch("query_handler.QueryMiddleware")
    def test_permissions_route_returns_resolved_groups(self, MockMiddleware, _env):
        """GET /user/permissions returns the user's resolved group list."""
        from api_authorizer import handler as auth_handler
        auth_result = auth_handler({
            "type": "REQUEST",
            "routeArn": "arn:aws:execute-api:us-east-1:123:api/stage/GET/user/permissions",
            "headers": {"authorization": "Bearer e2e-key-1"},
            "requestContext": {"http": {"method": "GET", "path": "/user/permissions"}},
        }, None)
        assert auth_result["isAuthorized"] is True

        with patch("query_handler.GroupResolver") as MockResolver:
            from lib.query_middleware.group_resolver import ResolvedUser
            mock_inst = MagicMock()
            mock_inst.resolve.return_value = ResolvedUser(
                user_id="u-e2e",
                upn="e2e@test.com",
                groups=["g-exec", "g-finance", "g-all-employees"],
                sensitivity_ceiling="confidential",
            )
            MockResolver.return_value = mock_inst

            from query_handler import handler as query_handler_fn
            result = query_handler_fn({
                "requestContext": {
                    "http": {"method": "GET", "path": "/user/permissions"},
                    "authorizer": {"lambda": auth_result["context"]},
                },
            }, None)

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["user_id"] == "u-e2e"
        assert "g-exec" in body["groups"]
        assert body["sensitivity_ceiling"] == "confidential"
