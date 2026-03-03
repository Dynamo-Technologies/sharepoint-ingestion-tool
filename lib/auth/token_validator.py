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
            Optional mapping of API key -> user identity dict
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
