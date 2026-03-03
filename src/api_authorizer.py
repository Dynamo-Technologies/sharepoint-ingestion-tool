"""API Gateway Lambda authorizer -- validates bearer tokens.

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
