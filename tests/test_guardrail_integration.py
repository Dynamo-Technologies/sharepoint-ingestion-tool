"""Tests for Bedrock Guardrails integration in QueryMiddleware."""

from __future__ import annotations

import json

import pytest
from unittest.mock import MagicMock, patch

from lib.query_middleware.client import QueryMiddleware


class TestGuardrailIntegration:
    @pytest.fixture
    def mock_bedrock_runtime(self):
        client = MagicMock()
        response_body = json.dumps({
            "content": [{"text": "Answer text"}],
        }).encode()
        mock_body = MagicMock()
        mock_body.read.return_value = response_body
        client.invoke_model.return_value = {"body": mock_body}
        return client

    @pytest.fixture
    def mock_bedrock_agent(self):
        client = MagicMock()
        client.retrieve.return_value = {
            "retrievalResults": [
                {
                    "content": {"text": "chunk text"},
                    "metadata": {"chunk_id": "c1", "document_id": "d1"},
                    "score": 0.9,
                },
            ],
        }
        return client

    @pytest.fixture
    def mock_resolver(self):
        resolver = MagicMock()
        from lib.query_middleware.group_resolver import ResolvedUser
        resolver.resolve.return_value = ResolvedUser(
            user_id="u-1",
            upn="alice@test.com",
            groups=["g1"],
            sensitivity_ceiling="internal",
        )
        return resolver

    def test_invoke_model_passes_guardrail_params(
        self, mock_bedrock_runtime, mock_bedrock_agent, mock_resolver,
    ):
        mw = QueryMiddleware(
            knowledge_base_id="kb-123",
            model_id="anthropic.claude-3-sonnet-20240229-v1:0",
            group_resolver=mock_resolver,
            bedrock_agent_client=mock_bedrock_agent,
            bedrock_runtime_client=mock_bedrock_runtime,
            guardrail_id="gr-abc123",
            guardrail_version="1",
        )

        mw.query("What is revenue?", user_id="u-1", user_groups=["g1"])

        call_kwargs = mock_bedrock_runtime.invoke_model.call_args.kwargs
        assert call_kwargs["guardrailIdentifier"] == "gr-abc123"
        assert call_kwargs["guardrailVersion"] == "1"

    def test_invoke_model_without_guardrail(
        self, mock_bedrock_runtime, mock_bedrock_agent, mock_resolver,
    ):
        mw = QueryMiddleware(
            knowledge_base_id="kb-123",
            group_resolver=mock_resolver,
            bedrock_agent_client=mock_bedrock_agent,
            bedrock_runtime_client=mock_bedrock_runtime,
        )

        mw.query("What is revenue?", user_id="u-1", user_groups=["g1"])

        call_kwargs = mock_bedrock_runtime.invoke_model.call_args.kwargs
        assert "guardrailIdentifier" not in call_kwargs
        assert "guardrailVersion" not in call_kwargs

    def test_guardrail_intervention_returns_safe_response(
        self, mock_bedrock_agent, mock_resolver,
    ):
        """When guardrail blocks a response, the API returns a GUARDRAIL_INTERVENED
        stop reason. The middleware should handle this gracefully."""
        mock_runtime = MagicMock()
        response_body = json.dumps({
            "content": [{"text": "Sorry, I can't provide personal medical advice."}],
            "stop_reason": "guardrail_intervened",
            "amazon-bedrock-guardrailAction": "INTERVENED",
        }).encode()
        mock_body = MagicMock()
        mock_body.read.return_value = response_body
        mock_runtime.invoke_model.return_value = {"body": mock_body}

        mw = QueryMiddleware(
            knowledge_base_id="kb-123",
            group_resolver=mock_resolver,
            bedrock_agent_client=mock_bedrock_agent,
            bedrock_runtime_client=mock_runtime,
            guardrail_id="gr-abc123",
            guardrail_version="1",
        )

        result = mw.query("Give me medical advice", user_id="u-1")

        # Should still return a valid response (the guardrail's replacement text)
        assert result["result_type"] == "success"
        assert "medical advice" in result["response_text"].lower()
