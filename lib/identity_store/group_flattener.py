"""Resolve nested group memberships into flat per-user group lists."""

from __future__ import annotations

import logging
from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lib.identity_store.client import IdentityStoreClient

logger = logging.getLogger(__name__)


class GroupFlattener:
    """Flatten nested Identity Store group memberships.

    Builds a group-membership graph from the Identity Store, then
    resolves each user's transitive group memberships via BFS.
    Handles circular group references via visited-set cycle detection.
    """

    def __init__(self, identity_store_client: IdentityStoreClient) -> None:
        self._client = identity_store_client

    def flatten_all(self) -> dict[str, set[str]]:
        """Return ``{user_id: {all_group_ids}}`` for every user.

        Traverses all groups, builds a child->parent graph, then BFS-expands
        each user's direct memberships to include ancestor groups.
        """
        groups = list(self._client.list_groups())
        user_direct: dict[str, set[str]] = {}
        group_parents: dict[str, set[str]] = {}

        for group in groups:
            group_id = group["GroupId"]
            for membership in self._client.list_group_memberships(group_id):
                member = membership.get("MemberId", {})
                if "UserId" in member:
                    user_direct.setdefault(member["UserId"], set()).add(group_id)
                elif "GroupId" in member:
                    child_id = member["GroupId"]
                    group_parents.setdefault(child_id, set()).add(group_id)

        result: dict[str, set[str]] = {}
        for user_id, direct_groups in user_direct.items():
            result[user_id] = self._expand(direct_groups, group_parents)

        return result

    @staticmethod
    def _expand(
        direct_groups: set[str],
        group_parents: dict[str, set[str]],
    ) -> set[str]:
        """BFS-expand direct groups through parent graph."""
        all_groups = set(direct_groups)
        queue = deque(direct_groups)
        visited: set[str] = set()

        while queue:
            current = queue.popleft()
            if current in visited:
                continue
            visited.add(current)
            for parent in group_parents.get(current, set()):
                if parent not in all_groups:
                    all_groups.add(parent)
                    queue.append(parent)

        return all_groups
