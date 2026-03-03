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
