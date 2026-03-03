"""Construct Bedrock Knowledge Base RetrievalFilter dicts.

Translates resolved user groups and sensitivity ceiling into the filter
format expected by the Bedrock ``Retrieve`` API's
``vectorSearchConfiguration.filter`` parameter.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Map sensitivity level strings to integers for numeric comparison.
SENSITIVITY_MAP: dict[str, int] = {
    "public": 0,
    "internal": 1,
    "confidential": 2,
    "restricted": 3,
}


class FilterBuilder:
    """Build Bedrock KB retrieval filters from user permissions."""

    def build_filter(
        self,
        groups: list[str],
        sensitivity_ceiling: str,
    ) -> dict:
        """Construct a RetrievalFilter dict.

        Parameters
        ----------
        groups:
            Merged, deduplicated list of the user's group IDs.
        sensitivity_ceiling:
            Maximum sensitivity level the user may access.

        Returns
        -------
        dict
            A Bedrock KB ``RetrievalFilter`` dict ready to pass to the
            ``Retrieve`` API.
        """
        group_filter = self._build_group_filter(groups)
        sensitivity_filter = self._build_sensitivity_filter(sensitivity_ceiling)

        return {
            "andAll": [group_filter, sensitivity_filter],
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_group_filter(groups: list[str]) -> dict:
        """Build the group membership filter.

        Uses ``listContains`` on ``allowed_groups`` — the chunk's
        ``allowed_groups`` list must contain at least one of the user's
        groups.
        """
        if not groups:
            # No groups → impossible filter (matches nothing)
            return {"listContains": {"key": "allowed_groups", "value": "__no_access__"}}

        if len(groups) == 1:
            return {"listContains": {"key": "allowed_groups", "value": groups[0]}}

        return {
            "orAll": [
                {"listContains": {"key": "allowed_groups", "value": g}}
                for g in groups
            ],
        }

    @staticmethod
    def _build_sensitivity_filter(ceiling: str) -> dict:
        """Build the sensitivity ceiling filter.

        Uses ``lessThanOrEquals`` on ``sensitivity_level_numeric``.
        """
        ceiling = ceiling or "public"
        numeric = SENSITIVITY_MAP.get(ceiling.lower(), 0)
        return {
            "lessThanOrEquals": {
                "key": "sensitivity_level_numeric",
                "value": numeric,
            },
        }
