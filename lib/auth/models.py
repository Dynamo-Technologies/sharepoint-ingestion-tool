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
