# Open WebUI SSO Integration & Bedrock Guardrails — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build the user-facing query layer: API Gateway with Lambda authorizer, query handler Lambda with LLM routing, Bedrock Guardrails, and ECS Fargate infrastructure for Open WebUI.

**Architecture:** API Gateway HTTP API receives authenticated requests, a Lambda authorizer validates JWT tokens and extracts user context, a query handler Lambda routes queries through an LLM complexity router and the existing QueryMiddleware (now with Bedrock Guardrails), and ECS Fargate hosts the Open WebUI container behind an ALB. All infrastructure is defined in Terraform.

**Tech Stack:** Python 3.11, boto3, pytest + moto + unittest.mock, Terraform (AWS provider ~> 5.0), PyJWT

---

## Task 1: Auth Models (`lib/auth/models.py`)

**Files:**
- Create: `lib/auth/__init__.py`
- Create: `lib/auth/models.py`
- Create: `tests/test_auth_models.py`

**Step 1: Write the failing test**

Create `tests/test_auth_models.py`:

```python
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
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_auth_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'lib.auth'`

**Step 3: Write minimal implementation**

Create `lib/auth/__init__.py`:

```python
from lib.auth.models import AuthenticatedUser

__all__ = ["AuthenticatedUser"]
```

Create `lib/auth/models.py`:

```python
"""Authentication models for API Gateway authorizer context."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AuthenticatedUser:
    """User identity extracted from a validated JWT token.

    Parameters
    ----------
    user_id:
        Entra ID Object ID (``sub`` claim).
    upn:
        User Principal Name / email (``email`` or ``upn`` claim).
    groups:
        List of Entra ID group Object IDs from the token ``groups`` claim.
    """

    user_id: str
    upn: str
    groups: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialize to a plain dict for API Gateway authorizer context."""
        return {
            "user_id": self.user_id,
            "upn": self.upn,
            "groups": self.groups,
        }

    @classmethod
    def from_authorizer_context(cls, context: dict) -> AuthenticatedUser:
        """Reconstruct from API Gateway authorizer context.

        API Gateway flattens all context values to strings, so ``groups``
        arrives as a comma-separated string.
        """
        groups_str = context.get("groups", "")
        groups = [g for g in groups_str.split(",") if g]
        return cls(
            user_id=context.get("user_id", ""),
            upn=context.get("upn", ""),
            groups=groups,
        )
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_auth_models.py -v`
Expected: 5 PASSED

**Step 5: Commit**

```bash
git add lib/auth/__init__.py lib/auth/models.py tests/test_auth_models.py
git commit -m "feat: add AuthenticatedUser model for API auth context"
```

---

## Task 2: Token Validator (`lib/auth/token_validator.py`)

**Files:**
- Create: `lib/auth/token_validator.py`
- Create: `tests/test_token_validator.py`
- Modify: `lib/auth/__init__.py`

**Context:** The Lambda authorizer validates JWT tokens. For the initial implementation (no custom domain, no ALB OIDC), the authorizer validates API keys stored in an environment variable. When ALB OIDC is added later, this module will also validate ALB-issued JWTs signed with ES256. For now, we support two modes:
1. **API Key mode**: `Authorization: Bearer <api-key>` — validates against `API_KEYS` env var (comma-separated)
2. **JWT mode** (future): `Authorization: Bearer <jwt>` — validates JWT signature + claims

**Step 1: Write the failing test**

Create `tests/test_token_validator.py`:

```python
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
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_token_validator.py -v`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Write minimal implementation**

Create `lib/auth/token_validator.py`:

```python
"""Token validation for API Gateway Lambda authorizer.

Supports two authentication modes:

1. **API Key**: ``Authorization: Bearer <api-key>`` validated against a
   configured key list.  Used until ALB OIDC is enabled.
2. **JWT** (future): ``Authorization: Bearer <jwt>`` validated against
   ALB-issued ES256 signing keys.
"""

from __future__ import annotations

import logging
from typing import Any

from lib.auth.models import AuthenticatedUser

logger = logging.getLogger(__name__)


class AuthError(Exception):
    """Raised when token validation fails."""


class TokenValidator:
    """Validate bearer tokens (API keys or JWTs).

    Parameters
    ----------
    api_keys:
        Allowed API key strings.
    """

    def __init__(self, api_keys: list[str] | None = None) -> None:
        self._api_keys = set(api_keys or [])

    @staticmethod
    def extract_bearer_token(header: str | None) -> str:
        """Extract token from ``Authorization: Bearer <token>`` header."""
        if not header:
            raise AuthError("Missing Authorization header")

        parts = header.split(" ", 1)
        if parts[0].lower() != "bearer":
            raise AuthError("Invalid Authorization scheme — expected Bearer")

        if len(parts) < 2 or not parts[1].strip():
            raise AuthError("Missing token after Bearer")

        return parts[1].strip()

    def validate_api_key(
        self,
        api_key: str,
        key_user_map: dict[str, dict[str, Any]] | None = None,
    ) -> AuthenticatedUser:
        """Validate an API key and return the associated user.

        Parameters
        ----------
        api_key:
            The API key extracted from the Authorization header.
        key_user_map:
            Optional mapping of API key → user identity dict
            ``{"user_id": ..., "upn": ..., "groups": [...]}``.
        """
        if not api_key:
            raise AuthError("Missing API key")

        if api_key not in self._api_keys:
            raise AuthError("Invalid API key")

        key_user_map = key_user_map or {}
        user_info = key_user_map.get(api_key, {})

        return AuthenticatedUser(
            user_id=user_info.get("user_id", "api-key-user"),
            upn=user_info.get("upn", ""),
            groups=user_info.get("groups", []),
        )
```

Update `lib/auth/__init__.py`:

```python
from lib.auth.models import AuthenticatedUser
from lib.auth.token_validator import AuthError, TokenValidator

__all__ = ["AuthenticatedUser", "AuthError", "TokenValidator"]
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_token_validator.py -v`
Expected: 7 PASSED

**Step 5: Commit**

```bash
git add lib/auth/token_validator.py lib/auth/__init__.py tests/test_token_validator.py
git commit -m "feat: add TokenValidator with API key auth support"
```

---

## Task 3: LLM Router (`lib/query_middleware/llm_router.py`)

**Files:**
- Create: `lib/query_middleware/llm_router.py`
- Create: `tests/test_llm_router.py`

**Step 1: Write the failing test**

Create `tests/test_llm_router.py`:

```python
"""Tests for LLM Router — model selection by query complexity."""

from __future__ import annotations

import pytest

from lib.query_middleware.llm_router import LLMRouter


class TestLLMRouter:
    @pytest.fixture
    def router(self):
        return LLMRouter()

    # --- Explicit hints ---

    def test_simple_hint_returns_haiku(self, router):
        model = router.select_model("Any query", complexity_hint="simple")
        assert model == LLMRouter.HAIKU

    def test_complex_hint_returns_opus(self, router):
        model = router.select_model("Any query", complexity_hint="complex")
        assert model == LLMRouter.OPUS

    # --- Auto mode: short query, few chunks → Haiku ---

    def test_short_query_few_chunks_returns_haiku(self, router):
        model = router.select_model("What is X?", chunk_count=2)
        assert model == LLMRouter.HAIKU

    # --- Auto mode: medium query → Sonnet ---

    def test_medium_query_returns_sonnet(self, router):
        query = "x" * 200  # 200 chars, between 100 and 500
        model = router.select_model(query, chunk_count=5)
        assert model == LLMRouter.SONNET

    # --- Auto mode: long query → Opus ---

    def test_long_query_returns_opus(self, router):
        query = "x" * 600  # >500 chars
        model = router.select_model(query, chunk_count=3)
        assert model == LLMRouter.OPUS

    # --- Auto mode: many chunks → Opus ---

    def test_many_chunks_returns_opus(self, router):
        model = router.select_model("Short query", chunk_count=10)
        assert model == LLMRouter.OPUS

    # --- Boundary conditions ---

    def test_exactly_100_chars_not_haiku(self, router):
        query = "x" * 100  # boundary: >= 100 → not Haiku
        model = router.select_model(query, chunk_count=3)
        assert model == LLMRouter.SONNET

    def test_exactly_500_chars_not_opus(self, router):
        query = "x" * 500  # boundary: <= 500 → not Opus
        model = router.select_model(query, chunk_count=5)
        assert model == LLMRouter.SONNET

    def test_chunk_count_4_with_short_query_is_sonnet(self, router):
        model = router.select_model("Short", chunk_count=4)
        assert model == LLMRouter.SONNET

    def test_default_chunk_count_zero(self, router):
        model = router.select_model("Short query")
        assert model == LLMRouter.HAIKU
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_llm_router.py -v`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Write minimal implementation**

Create `lib/query_middleware/llm_router.py`:

```python
"""LLM complexity router — select Bedrock model based on query characteristics.

Routes queries to Haiku (fast/cheap), Sonnet (balanced), or Opus (complex)
based on query length and the number of retrieved chunks.
"""

from __future__ import annotations


class LLMRouter:
    """Select a Bedrock model ID based on query complexity."""

    HAIKU = "anthropic.claude-3-haiku-20240307-v1:0"
    SONNET = "anthropic.claude-3-sonnet-20240229-v1:0"
    OPUS = "anthropic.claude-3-opus-20240229-v1:0"

    def select_model(
        self,
        query_text: str,
        chunk_count: int = 0,
        complexity_hint: str = "auto",
    ) -> str:
        """Return the Bedrock model ID to use for this query.

        Parameters
        ----------
        query_text:
            The user's natural-language query.
        chunk_count:
            Number of retrieved KB chunks (0 if not yet known).
        complexity_hint:
            ``"simple"`` → Haiku, ``"complex"`` → Opus, ``"auto"`` →
            heuristic based on query length and chunk count.
        """
        if complexity_hint == "simple":
            return self.HAIKU
        if complexity_hint == "complex":
            return self.OPUS

        query_len = len(query_text)

        if query_len < 100 and chunk_count <= 3:
            return self.HAIKU
        if query_len > 500 or chunk_count > 7:
            return self.OPUS
        return self.SONNET
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_llm_router.py -v`
Expected: 11 PASSED

**Step 5: Commit**

```bash
git add lib/query_middleware/llm_router.py tests/test_llm_router.py
git commit -m "feat: add LLM complexity router for model selection"
```

---

## Task 4: Update QueryMiddleware with Guardrail Support

**Files:**
- Modify: `lib/query_middleware/client.py:48-65` (constructor — add `guardrail_id` and `guardrail_version`)
- Modify: `lib/query_middleware/client.py:162-194` (`_invoke_model` — pass guardrail params)
- Create: `tests/test_guardrail_integration.py`

**Context:** The existing `QueryMiddleware._invoke_model()` calls `bedrock_runtime.invoke_model(modelId=..., body=..., contentType=...)`. We need to add optional `guardrailIdentifier` and `guardrailVersion` params to this call. Reference the existing code at `lib/query_middleware/client.py`.

**Step 1: Write the failing test**

Create `tests/test_guardrail_integration.py`:

```python
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
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_guardrail_integration.py -v`
Expected: FAIL with `TypeError: QueryMiddleware.__init__() got an unexpected keyword argument 'guardrail_id'`

**Step 3: Modify QueryMiddleware**

In `lib/query_middleware/client.py`, update the `__init__` method to add `guardrail_id` and `guardrail_version` parameters:

```python
# In __init__, add these parameters after num_results:
    def __init__(
        self,
        knowledge_base_id: str,
        model_id: str = "anthropic.claude-3-sonnet-20240229-v1:0",
        group_resolver: GroupResolver | None = None,
        bedrock_agent_client: Any | None = None,
        bedrock_runtime_client: Any | None = None,
        num_results: int = _DEFAULT_NUM_RESULTS,
        guardrail_id: str | None = None,
        guardrail_version: str | None = None,
    ) -> None:
        self._kb_id = knowledge_base_id
        self._model_id = model_id
        self._resolver = group_resolver or GroupResolver()
        self._bedrock_agent = bedrock_agent_client or boto3.client("bedrock-agent-runtime")
        self._bedrock_runtime = bedrock_runtime_client or boto3.client("bedrock-runtime")
        self._num_results = num_results
        self._filter_builder = FilterBuilder()
        self._response_handler = ResponseHandler()
        self._audit = AuditLogger()
        self._guardrail_id = guardrail_id
        self._guardrail_version = guardrail_version
```

In `_invoke_model`, add guardrail params to the `invoke_model` call:

```python
    def _invoke_model(self, query_text: str, chunks: list[dict]) -> str:
        """Call Bedrock InvokeModel with retrieved chunks as context."""
        context = "\n\n---\n\n".join(
            c.get("content", {}).get("text", "") for c in chunks
        )

        prompt = (
            "You are a helpful assistant answering questions based on company "
            "documents. Use ONLY the provided context to answer. If the context "
            "doesn't contain the answer, say so.\n\n"
            f"Context:\n{context}\n\n"
            f"Question: {query_text}\n\n"
            "Answer:"
        )

        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": prompt}],
        })

        invoke_kwargs: dict[str, Any] = {
            "modelId": self._model_id,
            "body": body,
            "contentType": "application/json",
        }

        if self._guardrail_id:
            invoke_kwargs["guardrailIdentifier"] = self._guardrail_id
            invoke_kwargs["guardrailVersion"] = self._guardrail_version or "DRAFT"

        response = self._bedrock_runtime.invoke_model(**invoke_kwargs)

        response_body = json.loads(response["body"].read())
        content_blocks = response_body.get("content", [])
        if not content_blocks:
            logger.warning("LLM returned empty content blocks")
            return ""
        return content_blocks[0].get("text", "")
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_guardrail_integration.py -v`
Expected: 3 PASSED

Also run existing QueryMiddleware tests to ensure no regression:

Run: `.venv/bin/python -m pytest tests/test_query_middleware.py -v`
Expected: All PASSED

**Step 5: Commit**

```bash
git add lib/query_middleware/client.py tests/test_guardrail_integration.py
git commit -m "feat: add Bedrock Guardrails support to QueryMiddleware"
```

---

## Task 5: API Authorizer Lambda (`src/api_authorizer.py`)

**Files:**
- Create: `src/api_authorizer.py`
- Create: `tests/test_api_authorizer.py`

**Context:** This is a Lambda authorizer for API Gateway (HTTP API). It receives the request headers, extracts the `Authorization: Bearer <token>` header, validates the token, and returns a response with `isAuthorized: true/false` plus a `context` dict. **Important:** HTTP API authorizers use the simpler payload format v2 — they return `{"isAuthorized": true/false, "context": {...}}` not IAM policies.

**Step 1: Write the failing test**

Create `tests/test_api_authorizer.py`:

```python
"""Tests for API Gateway Lambda authorizer."""

from __future__ import annotations

import json
import os

import pytest
from unittest.mock import patch, MagicMock

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
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_api_authorizer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'api_authorizer'`

**Step 3: Write minimal implementation**

Create `src/api_authorizer.py`:

```python
"""API Gateway Lambda authorizer — validates bearer tokens.

HTTP API payload format v2: returns ``{"isAuthorized": bool, "context": {...}}``.

Currently supports API key authentication. JWT/OIDC validation will be
added when a custom domain + ALB HTTPS is configured.
"""

from __future__ import annotations

import json
import logging
import os

try:
    from lib.auth.token_validator import AuthError, TokenValidator
except ImportError:
    from auth.token_validator import AuthError, TokenValidator  # type: ignore[no-redef]

logger = logging.getLogger(__name__)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

_DENY = {"isAuthorized": False, "context": {}}


def handler(event: dict, context: object) -> dict:
    """API Gateway HTTP API authorizer handler (payload format v2)."""
    headers = event.get("headers", {})
    auth_header = headers.get("authorization", "")

    api_keys_str = os.getenv("API_KEYS", "")
    api_keys = [k.strip() for k in api_keys_str.split(",") if k.strip()]

    key_user_map_str = os.getenv("API_KEY_USER_MAP", "{}")
    try:
        key_user_map = json.loads(key_user_map_str)
    except json.JSONDecodeError:
        logger.error("Invalid API_KEY_USER_MAP JSON")
        key_user_map = {}

    validator = TokenValidator(api_keys=api_keys)

    try:
        token = validator.extract_bearer_token(auth_header)
        user = validator.validate_api_key(token, key_user_map=key_user_map)
    except AuthError as exc:
        logger.info("Auth denied: %s", exc)
        return _DENY

    # API Gateway HTTP API flattens context values to strings.
    return {
        "isAuthorized": True,
        "context": {
            "user_id": user.user_id,
            "upn": user.upn,
            "groups": ",".join(user.groups),
        },
    }
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_api_authorizer.py -v`
Expected: 5 PASSED

**Step 5: Commit**

```bash
git add src/api_authorizer.py tests/test_api_authorizer.py
git commit -m "feat: add API Gateway Lambda authorizer with API key auth"
```

---

## Task 6: Query Handler Lambda (`src/query_handler.py`)

**Files:**
- Create: `src/query_handler.py`
- Create: `tests/test_query_handler.py`

**Context:** Single Lambda handling 3 API Gateway routes: `POST /query`, `GET /health`, `GET /user/permissions`. Uses API Gateway HTTP API event format (v2). The authorizer context is available at `event["requestContext"]["authorizer"]["lambda"]`.

**Step 1: Write the failing test**

Create `tests/test_query_handler.py`:

```python
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
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_query_handler.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'query_handler'`

**Step 3: Write minimal implementation**

Create `src/query_handler.py`:

```python
"""Query handler Lambda — API Gateway HTTP API entry point.

Routes:
- ``POST /query`` — authenticated RAG query with LLM routing + guardrails
- ``GET /health`` — unauthenticated health check
- ``GET /user/permissions`` — authenticated user permission lookup
"""

from __future__ import annotations

import json
import logging
import os

try:
    from lib.auth.models import AuthenticatedUser
    from lib.query_middleware.client import QueryMiddleware
    from lib.query_middleware.group_resolver import GroupResolver
    from lib.query_middleware.llm_router import LLMRouter
except ImportError:
    from auth.models import AuthenticatedUser  # type: ignore[no-redef]
    from query_middleware.client import QueryMiddleware  # type: ignore[no-redef]
    from query_middleware.group_resolver import GroupResolver  # type: ignore[no-redef]
    from query_middleware.llm_router import LLMRouter  # type: ignore[no-redef]

logger = logging.getLogger(__name__)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

_VERSION = "1.0.0"


def handler(event: dict, context: object) -> dict:
    """API Gateway HTTP API v2 proxy handler."""
    http = event.get("requestContext", {}).get("http", {})
    method = http.get("method", "")
    path = http.get("path", "")

    if method == "GET" and path == "/health":
        return _health()

    if method == "GET" and path == "/user/permissions":
        return _user_permissions(event)

    if method == "POST" and path == "/query":
        return _query(event)

    return _response(404, {"error": "Not found"})


def _health() -> dict:
    return _response(200, {"status": "healthy", "version": _VERSION})


def _user_permissions(event: dict) -> dict:
    user = _extract_user(event)
    if user is None:
        return _response(401, {"error": "Unauthorized"})

    resolver = GroupResolver()
    resolved = resolver.resolve(user.user_id, saml_groups=user.groups)

    return _response(200, {
        "user_id": resolved.user_id,
        "upn": resolved.upn,
        "groups": resolved.groups,
        "sensitivity_ceiling": resolved.sensitivity_ceiling,
    })


def _query(event: dict) -> dict:
    user = _extract_user(event)
    if user is None:
        return _response(401, {"error": "Unauthorized"})

    body_str = event.get("body", "")
    if not body_str:
        return _response(400, {"error": "Missing request body"})

    try:
        body = json.loads(body_str)
    except json.JSONDecodeError:
        return _response(400, {"error": "Invalid JSON body"})

    query_text = body.get("query", "").strip()
    if not query_text:
        return _response(400, {"error": "Missing 'query' field"})

    complexity_hint = body.get("complexity_hint", "auto")

    # LLM Router: select model based on query complexity
    router = LLMRouter()
    model_id = router.select_model(
        query_text,
        complexity_hint=complexity_hint,
    )

    # QueryMiddleware: permission-filtered retrieval + generation
    kb_id = os.environ.get("KNOWLEDGE_BASE_ID", "")
    guardrail_id = os.environ.get("GUARDRAIL_ID") or None
    guardrail_version = os.environ.get("GUARDRAIL_VERSION") or None

    middleware = QueryMiddleware(
        knowledge_base_id=kb_id,
        model_id=model_id,
        guardrail_id=guardrail_id,
        guardrail_version=guardrail_version,
    )

    result = middleware.query(
        query_text=query_text,
        user_id=user.user_id,
        user_groups=user.groups,
    )

    result["model_used"] = model_id
    return _response(200, result)


def _extract_user(event: dict) -> AuthenticatedUser | None:
    """Extract the authenticated user from the authorizer context."""
    authorizer = (
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("lambda", {})
    )
    if not authorizer or not authorizer.get("user_id"):
        return None

    return AuthenticatedUser.from_authorizer_context(authorizer)


def _response(status_code: int, body: dict) -> dict:
    """Build an API Gateway HTTP API v2 proxy response."""
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
        },
        "body": json.dumps(body),
    }
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_query_handler.py -v`
Expected: 6 PASSED

**Step 5: Commit**

```bash
git add src/query_handler.py tests/test_query_handler.py
git commit -m "feat: add query handler Lambda with LLM routing and 3 API routes"
```

---

## Task 7: End-to-End Test

**Files:**
- Create: `tests/test_api_e2e.py`

**Context:** Simulate the full authenticated flow: authorizer validates token → query handler receives authorized context → LLM router selects model → QueryMiddleware performs permission-filtered retrieval → guardrails are applied → response returned. Also test unauthorized denial.

**Step 1: Write the test**

Create `tests/test_api_e2e.py`:

```python
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
```

**Step 2: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_api_e2e.py -v`
Expected: 3 PASSED

**Step 3: Commit**

```bash
git add tests/test_api_e2e.py
git commit -m "test: add end-to-end authenticated query flow tests"
```

---

## Task 8: Terraform — VPC + Networking (`terraform/vpc.tf`)

**Files:**
- Create: `terraform/vpc.tf`

**Context:** New VPC for the Open WebUI ECS deployment. 10.100.0.0/16 CIDR. 2 public subnets (ALB), 2 private subnets (ECS tasks), 1 NAT Gateway, 1 Internet Gateway. Follows the existing Terraform patterns in `terraform/main.tf` (provider uses default_tags).

**Step 1: Write the Terraform**

Create `terraform/vpc.tf`:

```hcl
# ---------------------------------------------------------------
# VPC + Networking for Open WebUI ECS Deployment
# ---------------------------------------------------------------

data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_vpc" "webui" {
  cidr_block           = "10.100.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "sp-ingest-webui" }
}

# --- Public Subnets (ALB) ---

resource "aws_subnet" "webui_public" {
  count                   = 2
  vpc_id                  = aws_vpc.webui.id
  cidr_block              = cidrsubnet(aws_vpc.webui.cidr_block, 8, count.index + 1)
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true

  tags = { Name = "sp-ingest-webui-public-${count.index}" }
}

# --- Private Subnets (ECS Tasks) ---

resource "aws_subnet" "webui_private" {
  count             = 2
  vpc_id            = aws_vpc.webui.id
  cidr_block        = cidrsubnet(aws_vpc.webui.cidr_block, 8, count.index + 10)
  availability_zone = data.aws_availability_zones.available.names[count.index]

  tags = { Name = "sp-ingest-webui-private-${count.index}" }
}

# --- Internet Gateway ---

resource "aws_internet_gateway" "webui" {
  vpc_id = aws_vpc.webui.id

  tags = { Name = "sp-ingest-webui-igw" }
}

# --- NAT Gateway (single, in first public subnet) ---

resource "aws_eip" "nat" {
  domain = "vpc"

  tags = { Name = "sp-ingest-webui-nat-eip" }
}

resource "aws_nat_gateway" "webui" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.webui_public[0].id

  tags = { Name = "sp-ingest-webui-nat" }

  depends_on = [aws_internet_gateway.webui]
}

# --- Route Tables ---

resource "aws_route_table" "webui_public" {
  vpc_id = aws_vpc.webui.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.webui.id
  }

  tags = { Name = "sp-ingest-webui-public-rt" }
}

resource "aws_route_table_association" "webui_public" {
  count          = 2
  subnet_id      = aws_subnet.webui_public[count.index].id
  route_table_id = aws_route_table.webui_public.id
}

resource "aws_route_table" "webui_private" {
  vpc_id = aws_vpc.webui.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.webui.id
  }

  tags = { Name = "sp-ingest-webui-private-rt" }
}

resource "aws_route_table_association" "webui_private" {
  count          = 2
  subnet_id      = aws_subnet.webui_private[count.index].id
  route_table_id = aws_route_table.webui_private.id
}

# --- Security Groups ---

resource "aws_security_group" "webui_alb" {
  name_prefix = "sp-ingest-webui-alb-"
  description = "Allow HTTP/HTTPS inbound to ALB"
  vpc_id      = aws_vpc.webui.id

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "HTTP"
  }

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "HTTPS"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "sp-ingest-webui-alb-sg" }
}

resource "aws_security_group" "webui_ecs" {
  name_prefix = "sp-ingest-webui-ecs-"
  description = "Allow inbound from ALB only on port 8080"
  vpc_id      = aws_vpc.webui.id

  ingress {
    from_port       = 8080
    to_port         = 8080
    protocol        = "tcp"
    security_groups = [aws_security_group.webui_alb.id]
    description     = "Open WebUI from ALB"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "sp-ingest-webui-ecs-sg" }
}
```

**Step 2: Validate Terraform**

Run: `cd terraform && terraform validate`
Expected: `Success! The configuration is valid.`

**Step 3: Commit**

```bash
git add terraform/vpc.tf
git commit -m "infra: add VPC with public/private subnets for Open WebUI"
```

---

## Task 9: Terraform — ECS Fargate + ALB (`terraform/ecs.tf`)

**Files:**
- Create: `terraform/ecs.tf`
- Modify: `terraform/variables.tf` (add `open_webui_image`, `knowledge_base_id`, `bedrock_model_id`)

**Step 1: Add new variables**

Append to `terraform/variables.tf`:

```hcl
# -------------------------------------------------------------------
# Open WebUI / API Gateway
# -------------------------------------------------------------------

variable "open_webui_image" {
  description = "ECR image URI for the Open WebUI container"
  type        = string
  default     = ""
}

variable "knowledge_base_id" {
  description = "Bedrock Knowledge Base ID for RAG queries"
  type        = string
  default     = ""
}

variable "bedrock_model_id" {
  description = "Default Bedrock model ID for LLM generation"
  type        = string
  default     = "anthropic.claude-3-sonnet-20240229-v1:0"
}

variable "api_keys" {
  description = "Comma-separated API keys for the query API (stored as env var)"
  type        = string
  default     = ""
  sensitive   = true
}

variable "api_key_user_map" {
  description = "JSON mapping of API key → user identity"
  type        = string
  default     = "{}"
  sensitive   = true
}

variable "enable_webui" {
  description = "Set to true to deploy Open WebUI ECS infrastructure"
  type        = bool
  default     = false
}
```

**Step 2: Write the ECS Terraform**

Create `terraform/ecs.tf`:

```hcl
# ---------------------------------------------------------------
# ECS Fargate + ALB for Open WebUI
# Controlled by var.enable_webui — set to true to deploy.
# ---------------------------------------------------------------

# --- ECS Cluster ---

resource "aws_ecs_cluster" "webui" {
  count = var.enable_webui ? 1 : 0
  name  = "sp-ingest-webui"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

# --- ALB ---

resource "aws_lb" "webui" {
  count              = var.enable_webui ? 1 : 0
  name               = "sp-ingest-webui-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.webui_alb.id]
  subnets            = aws_subnet.webui_public[*].id

  tags = { Name = "sp-ingest-webui-alb" }
}

resource "aws_lb_target_group" "webui" {
  count       = var.enable_webui ? 1 : 0
  name        = "sp-ingest-webui-tg"
  port        = 8080
  protocol    = "HTTP"
  vpc_id      = aws_vpc.webui.id
  target_type = "ip"

  health_check {
    path                = "/health"
    port                = "traffic-port"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    interval            = 30
    timeout             = 5
  }
}

resource "aws_lb_listener" "webui_http" {
  count             = var.enable_webui ? 1 : 0
  load_balancer_arn = aws_lb.webui[0].arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.webui[0].arn
  }
}

# --- ECS Task Definition ---

resource "aws_ecs_task_definition" "webui" {
  count                    = var.enable_webui ? 1 : 0
  family                   = "sp-ingest-webui"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.webui_task_execution[0].arn

  container_definitions = jsonencode([
    {
      name      = "open-webui"
      image     = var.open_webui_image
      essential = true

      portMappings = [
        {
          containerPort = 8080
          protocol      = "tcp"
        },
      ]

      environment = [
        { name = "WEBUI_AUTH", value = "true" },
        { name = "RAG_API_BASE_URL", value = var.enable_webui ? "https://${aws_apigatewayv2_api.query[0].id}.execute-api.${var.aws_region}.amazonaws.com" : "" },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = var.enable_webui ? aws_cloudwatch_log_group.webui[0].name : ""
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "ecs"
        }
      }
    },
  ])
}

# --- ECS Service ---

resource "aws_ecs_service" "webui" {
  count           = var.enable_webui ? 1 : 0
  name            = "sp-ingest-webui"
  cluster         = aws_ecs_cluster.webui[0].id
  task_definition = aws_ecs_task_definition.webui[0].arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.webui_private[*].id
    security_groups  = [aws_security_group.webui_ecs.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.webui[0].arn
    container_name   = "open-webui"
    container_port   = 8080
  }

  depends_on = [aws_lb_listener.webui_http]
}

# --- CloudWatch Log Group ---

resource "aws_cloudwatch_log_group" "webui" {
  count             = var.enable_webui ? 1 : 0
  name              = "/ecs/sp-ingest-webui"
  retention_in_days = 30
}

# --- ECS Task Execution Role ---

resource "aws_iam_role" "webui_task_execution" {
  count = var.enable_webui ? 1 : 0
  name  = "sp-ingest-webui-task-execution"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "webui_task_execution" {
  count      = var.enable_webui ? 1 : 0
  role       = aws_iam_role.webui_task_execution[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}
```

**Step 2: Validate**

Run: `cd terraform && terraform validate`
Expected: `Success! The configuration is valid.`

**Step 3: Commit**

```bash
git add terraform/ecs.tf terraform/variables.tf
git commit -m "infra: add ECS Fargate + ALB for Open WebUI (conditional on enable_webui)"
```

---

## Task 10: Terraform — API Gateway + Lambda Authorizer (`terraform/api_gateway.tf`)

**Files:**
- Create: `terraform/api_gateway.tf`

**Step 1: Write the Terraform**

Create `terraform/api_gateway.tf`:

```hcl
# ---------------------------------------------------------------
# API Gateway HTTP API + Lambda Authorizer
# ---------------------------------------------------------------

resource "aws_apigatewayv2_api" "query" {
  count         = var.enable_webui ? 1 : 0
  name          = "sp-ingest-query-api"
  protocol_type = "HTTP"

  cors_configuration {
    allow_origins = ["*"]
    allow_methods = ["GET", "POST", "OPTIONS"]
    allow_headers = ["Content-Type", "Authorization"]
    max_age       = 3600
  }
}

resource "aws_apigatewayv2_stage" "query" {
  count       = var.enable_webui ? 1 : 0
  api_id      = aws_apigatewayv2_api.query[0].id
  name        = "$default"
  auto_deploy = true

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_gateway[0].arn
    format = jsonencode({
      requestId      = "$context.requestId"
      ip             = "$context.identity.sourceIp"
      requestTime    = "$context.requestTime"
      httpMethod     = "$context.httpMethod"
      routeKey       = "$context.routeKey"
      status         = "$context.status"
      protocol       = "$context.protocol"
      responseLength = "$context.responseLength"
      errorMessage   = "$context.error.message"
    })
  }

  default_route_settings {
    throttling_burst_limit = 100
    throttling_rate_limit  = 50
  }
}

resource "aws_cloudwatch_log_group" "api_gateway" {
  count             = var.enable_webui ? 1 : 0
  name              = "/aws/apigateway/sp-ingest-query-api"
  retention_in_days = 30
}

# --- Lambda Authorizer ---

resource "aws_apigatewayv2_authorizer" "api_key" {
  count                             = var.enable_webui ? 1 : 0
  api_id                            = aws_apigatewayv2_api.query[0].id
  authorizer_type                   = "REQUEST"
  authorizer_uri                    = aws_lambda_function.api_authorizer[0].invoke_arn
  authorizer_payload_format_version = "2.0"
  authorizer_result_ttl_in_seconds  = 300
  identity_sources                  = ["$request.header.Authorization"]
  name                              = "api-key-authorizer"
  enable_simple_responses           = true
}

# --- Routes ---

# POST /query (authorized)
resource "aws_apigatewayv2_integration" "query_handler" {
  count              = var.enable_webui ? 1 : 0
  api_id             = aws_apigatewayv2_api.query[0].id
  integration_type   = "AWS_PROXY"
  integration_uri    = aws_lambda_function.query_handler[0].invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "post_query" {
  count     = var.enable_webui ? 1 : 0
  api_id    = aws_apigatewayv2_api.query[0].id
  route_key = "POST /query"
  target    = "integrations/${aws_apigatewayv2_integration.query_handler[0].id}"

  authorization_type = "CUSTOM"
  authorizer_id      = aws_apigatewayv2_authorizer.api_key[0].id
}

# GET /health (no auth)
resource "aws_apigatewayv2_route" "get_health" {
  count     = var.enable_webui ? 1 : 0
  api_id    = aws_apigatewayv2_api.query[0].id
  route_key = "GET /health"
  target    = "integrations/${aws_apigatewayv2_integration.query_handler[0].id}"
}

# GET /user/permissions (authorized)
resource "aws_apigatewayv2_route" "get_permissions" {
  count     = var.enable_webui ? 1 : 0
  api_id    = aws_apigatewayv2_api.query[0].id
  route_key = "GET /user/permissions"
  target    = "integrations/${aws_apigatewayv2_integration.query_handler[0].id}"

  authorization_type = "CUSTOM"
  authorizer_id      = aws_apigatewayv2_authorizer.api_key[0].id
}

# --- Lambda Permissions for API Gateway ---

resource "aws_lambda_permission" "api_gw_query_handler" {
  count         = var.enable_webui ? 1 : 0
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.query_handler[0].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.query[0].execution_arn}/*/*"
}

resource "aws_lambda_permission" "api_gw_authorizer" {
  count         = var.enable_webui ? 1 : 0
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api_authorizer[0].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.query[0].execution_arn}/authorizers/${aws_apigatewayv2_authorizer.api_key[0].id}"
}
```

**Step 2: Commit** (validation will happen after lambda_api.tf is created)

```bash
git add terraform/api_gateway.tf
git commit -m "infra: add API Gateway HTTP API with Lambda authorizer and 3 routes"
```

---

## Task 11: Terraform — API Lambdas + IAM (`terraform/lambda_api.tf`, `terraform/iam_api.tf`)

**Files:**
- Create: `terraform/lambda_api.tf`
- Create: `terraform/iam_api.tf`

**Step 1: Write the Lambda definitions**

Create `terraform/lambda_api.tf`:

```hcl
# ---------------------------------------------------------------
# API Lambdas: query-handler + api-authorizer
# ---------------------------------------------------------------

# --- CloudWatch Log Groups ---

resource "aws_cloudwatch_log_group" "query_handler" {
  count             = var.enable_webui ? 1 : 0
  name              = "/aws/lambda/sp-ingest-query-handler"
  retention_in_days = 90
}

resource "aws_cloudwatch_log_group" "api_authorizer" {
  count             = var.enable_webui ? 1 : 0
  name              = "/aws/lambda/sp-ingest-api-authorizer"
  retention_in_days = 90
}

# --- DLQs ---

resource "aws_sqs_queue" "query_handler_dlq" {
  count                     = var.enable_webui ? 1 : 0
  name                      = "sp-ingest-query-handler-dlq"
  message_retention_seconds = 1209600
}

# --- Lambda: query-handler ---

resource "aws_lambda_function" "query_handler" {
  count         = var.enable_webui ? 1 : 0
  function_name = "sp-ingest-query-handler"
  role          = aws_iam_role.query_handler[0].arn
  handler       = "src.query_handler.handler"
  runtime       = "python3.11"
  timeout       = 60
  memory_size   = 512

  filename         = "${path.module}/../dist/lambda-code.zip"
  source_code_hash = filebase64sha256("${path.module}/../dist/lambda-code.zip")

  layers = [aws_lambda_layer_version.shared_deps.arn]

  dead_letter_config {
    target_arn = aws_sqs_queue.query_handler_dlq[0].arn
  }

  environment {
    variables = {
      PYTHONPATH          = "/var/task/src:/opt/python"
      KNOWLEDGE_BASE_ID   = var.knowledge_base_id
      BEDROCK_MODEL_ID    = var.bedrock_model_id
      GUARDRAIL_ID        = var.enable_webui ? aws_bedrock_guardrail.rag[0].guardrail_id : ""
      GUARDRAIL_VERSION   = var.enable_webui ? aws_bedrock_guardrail.rag[0].version : ""
      AWS_REGION_NAME     = var.aws_region
      LOG_LEVEL           = "INFO"
    }
  }

  depends_on = [aws_cloudwatch_log_group.query_handler]
}

# --- Lambda: api-authorizer ---

resource "aws_lambda_function" "api_authorizer" {
  count         = var.enable_webui ? 1 : 0
  function_name = "sp-ingest-api-authorizer"
  role          = aws_iam_role.api_authorizer[0].arn
  handler       = "src.api_authorizer.handler"
  runtime       = "python3.11"
  timeout       = 10
  memory_size   = 128

  filename         = "${path.module}/../dist/lambda-code.zip"
  source_code_hash = filebase64sha256("${path.module}/../dist/lambda-code.zip")

  layers = [aws_lambda_layer_version.shared_deps.arn]

  environment {
    variables = {
      PYTHONPATH       = "/var/task/src:/opt/python"
      API_KEYS         = var.api_keys
      API_KEY_USER_MAP = var.api_key_user_map
      LOG_LEVEL        = "INFO"
    }
  }

  depends_on = [aws_cloudwatch_log_group.api_authorizer]
}
```

**Step 2: Write the IAM roles**

Create `terraform/iam_api.tf`:

```hcl
# ---------------------------------------------------------------
# IAM Roles for API Lambdas (query-handler + api-authorizer)
# ---------------------------------------------------------------

# --- query-handler role ---

resource "aws_iam_role" "query_handler" {
  count = var.enable_webui ? 1 : 0
  name  = "sp-ingest-query-handler-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "query_handler_basic" {
  count      = var.enable_webui ? 1 : 0
  role       = aws_iam_role.query_handler[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "query_handler" {
  count = var.enable_webui ? 1 : 0
  name  = "sp-ingest-query-handler-policy"
  role  = aws_iam_role.query_handler[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "BedrockKBRetrieve"
        Effect = "Allow"
        Action = [
          "bedrock:Retrieve",
          "bedrock:InvokeModel",
        ]
        Resource = ["*"]
      },
      {
        Sid    = "BedrockGuardrail"
        Effect = "Allow"
        Action = [
          "bedrock:ApplyGuardrail",
        ]
        Resource = var.enable_webui ? [aws_bedrock_guardrail.rag[0].guardrail_arn] : []
      },
      {
        Sid    = "DynamoDBPermissions"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:Query",
          "dynamodb:Scan",
        ]
        Resource = [
          aws_dynamodb_table.permission_mappings.arn,
          aws_dynamodb_table.user_group_cache.arn,
        ]
      },
      {
        Sid      = "DLQSend"
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = var.enable_webui ? [aws_sqs_queue.query_handler_dlq[0].arn] : []
      },
    ]
  })
}

# --- api-authorizer role ---

resource "aws_iam_role" "api_authorizer" {
  count = var.enable_webui ? 1 : 0
  name  = "sp-ingest-api-authorizer-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "api_authorizer_basic" {
  count      = var.enable_webui ? 1 : 0
  role       = aws_iam_role.api_authorizer[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}
```

**Step 3: Commit**

```bash
git add terraform/lambda_api.tf terraform/iam_api.tf
git commit -m "infra: add query-handler and api-authorizer Lambda + IAM roles"
```

---

## Task 12: Terraform — Bedrock Guardrails (`terraform/guardrails.tf`)

**Files:**
- Create: `terraform/guardrails.tf`

**Step 1: Write the Terraform**

Create `terraform/guardrails.tf`:

```hcl
# ---------------------------------------------------------------
# Amazon Bedrock Guardrails — defense-in-depth for RAG queries
# ---------------------------------------------------------------

resource "aws_bedrock_guardrail" "rag" {
  count                 = var.enable_webui ? 1 : 0
  name                  = "sp-ingest-rag-guardrail"
  blocked_input_messaging  = "Your request was blocked by our content policy. Please rephrase your question."
  blocked_outputs_messaging = "The response was blocked by our content policy. Please try a different question."
  description           = "PII redaction, topic blocking, and content filtering for RAG queries"

  # --- PII Detection: redact sensitive data ---
  sensitive_information_policy_config {
    pii_entities_config {
      action = "ANONYMIZE"
      type   = "US_SOCIAL_SECURITY_NUMBER"
    }
    pii_entities_config {
      action = "ANONYMIZE"
      type   = "CREDIT_DEBIT_CARD_NUMBER"
    }
    pii_entities_config {
      action = "ANONYMIZE"
      type   = "PHONE"
    }
    pii_entities_config {
      action = "ANONYMIZE"
      type   = "EMAIL"
    }
    pii_entities_config {
      action = "ANONYMIZE"
      type   = "US_INDIVIDUAL_TAX_IDENTIFICATION_NUMBER"
    }
  }

  # --- Topic Blocking: deny out-of-scope advice ---
  topic_policy_config {
    topics_config {
      name       = "PersonalMedicalAdvice"
      definition = "Providing personal medical advice, diagnoses, or treatment recommendations"
      type       = "DENY"
      examples   = ["What medicine should I take for my headache?", "Is this mole cancerous?"]
    }
    topics_config {
      name       = "LegalAdvice"
      definition = "Providing legal advice, interpreting laws, or recommending legal actions"
      type       = "DENY"
      examples   = ["Can I sue my employer?", "Is this contract legally binding?"]
    }
    topics_config {
      name       = "InvestmentAdvice"
      definition = "Providing investment or financial planning advice"
      type       = "DENY"
      examples   = ["Should I buy this stock?", "How should I allocate my 401k?"]
    }
  }

  # --- Content Filtering: block harmful content ---
  content_policy_config {
    filters_config {
      input_strength  = "HIGH"
      output_strength = "HIGH"
      type            = "HATE"
    }
    filters_config {
      input_strength  = "HIGH"
      output_strength = "HIGH"
      type            = "INSULTS"
    }
    filters_config {
      input_strength  = "HIGH"
      output_strength = "HIGH"
      type            = "SEXUAL"
    }
    filters_config {
      input_strength  = "HIGH"
      output_strength = "HIGH"
      type            = "VIOLENCE"
    }
    filters_config {
      input_strength  = "HIGH"
      output_strength = "HIGH"
      type            = "MISCONDUCT"
    }
    filters_config {
      input_strength  = "NONE"
      output_strength = "NONE"
      type            = "PROMPT_ATTACK"
    }
  }
}
```

**Step 2: Commit**

```bash
git add terraform/guardrails.tf
git commit -m "infra: add Bedrock Guardrails for PII, topic blocking, and content filtering"
```

---

## Task 13: Terraform — Outputs + Validation

**Files:**
- Modify: `terraform/outputs.tf` (add API Gateway, ALB, Lambda outputs)

**Step 1: Append outputs**

Add to `terraform/outputs.tf`:

```hcl
# --- Open WebUI / API Gateway ---

output "api_gateway_url" {
  description = "API Gateway URL for the query API"
  value       = var.enable_webui ? "https://${aws_apigatewayv2_api.query[0].id}.execute-api.${var.aws_region}.amazonaws.com" : ""
}

output "alb_dns_name" {
  description = "DNS name of the Open WebUI ALB"
  value       = var.enable_webui ? aws_lb.webui[0].dns_name : ""
}

output "query_handler_lambda_arn" {
  description = "ARN of the query-handler Lambda"
  value       = var.enable_webui ? aws_lambda_function.query_handler[0].arn : ""
}

output "api_authorizer_lambda_arn" {
  description = "ARN of the api-authorizer Lambda"
  value       = var.enable_webui ? aws_lambda_function.api_authorizer[0].arn : ""
}

output "guardrail_id" {
  description = "Bedrock Guardrail ID"
  value       = var.enable_webui ? aws_bedrock_guardrail.rag[0].guardrail_id : ""
}
```

**Step 2: Run full Terraform validation**

Run: `cd terraform && terraform validate`
Expected: `Success! The configuration is valid.`

**Step 3: Commit**

```bash
git add terraform/outputs.tf
git commit -m "infra: add API Gateway, ALB, and guardrail outputs"
```

---

## Task 14: Full Test Suite Validation

**Files:** None (validation only)

**Step 1: Run all new tests**

Run: `.venv/bin/python -m pytest tests/test_auth_models.py tests/test_token_validator.py tests/test_llm_router.py tests/test_guardrail_integration.py tests/test_api_authorizer.py tests/test_query_handler.py tests/test_api_e2e.py -v`

Expected: All PASSED (approximately 40 tests)

**Step 2: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v --tb=short`

Expected: All new tests pass. Pre-existing failures in `test_file_converter.py` and `test_path_mapper.py` are known and unrelated.

**Step 3: Verify validation checklist**

- ✅ Deployment IaC complete (vpc.tf, ecs.tf, api_gateway.tf, lambda_api.tf, iam_api.tf, guardrails.tf)
- ✅ Auth extraction works (token_validator + api_authorizer tests)
- ✅ `/user/permissions` returns correct groups (test_query_handler.py::TestUserPermissionsRoute)
- ✅ LLM router selects different models (test_llm_router.py — 11 parameterized tests)
- ✅ Guardrails IaC defined (guardrails.tf)
- ✅ E2E authenticated query test (test_api_e2e.py)
- ✅ Unauthorized query returns privacy-safe denial (test_api_e2e.py::test_unauthorized_query_returns_privacy_safe_denial)
- ✅ Integration tests pass

---
