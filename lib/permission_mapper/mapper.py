"""Map Entra ID groups to S3 access-control tags and prefixes.

This module bridges the gap between Entra ID group memberships and the
existing ``access_rules.yaml``-based access control system.  It produces a
``permission_mappings.json`` file that records:

- Which Entra groups map to which access tags
- Which users belong to which groups (and therefore which tags)
- Which S3 prefixes each group / user can access

The mapping is driven by a configurable set of rules that match Entra group
names to access tags.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from lib.entra_id_parser.models import EntraData, EntraGroup, GroupMembership

logger = logging.getLogger(__name__)


# Default group-to-tag mapping rules.
# Each rule maps an Entra ID group name pattern (case-insensitive substring)
# to one or more access tags from the existing access_rules.yaml system.
DEFAULT_GROUP_TAG_RULES: list[dict] = [
    # --- Functional / department groups ---
    {
        "pattern": "HR",
        "match_type": "prefix",
        "tags": ["hr"],
        "description": "HR groups → hr tag",
    },
    {
        "pattern": "Human Capital",
        "match_type": "contains",
        "tags": ["hr"],
        "description": "Human Capital groups → hr tag",
    },
    {
        "pattern": "Recruiting",
        "match_type": "contains",
        "tags": ["hr"],
        "description": "Recruiting groups → hr tag",
    },
    {
        "pattern": "Finance",
        "match_type": "contains",
        "tags": ["finance"],
        "description": "Finance groups → finance tag",
    },
    {
        "pattern": "Accounting",
        "match_type": "contains",
        "tags": ["finance"],
        "description": "Accounting groups → finance tag",
    },
    {
        "pattern": "BD",
        "match_type": "prefix",
        "tags": ["bd", "capture"],
        "description": "BD groups → bd + capture tags",
    },
    {
        "pattern": "Business Development",
        "match_type": "contains",
        "tags": ["bd", "capture"],
        "description": "Business Development → bd + capture tags",
    },
    {
        "pattern": "Capture",
        "match_type": "contains",
        "tags": ["bd", "capture"],
        "description": "Capture groups → bd + capture tags",
    },
    {
        "pattern": "Contracts",
        "match_type": "contains",
        "tags": ["contracts"],
        "description": "Contracts groups → contracts tag",
    },
    {
        "pattern": "Engineering",
        "match_type": "contains",
        "tags": ["engineering"],
        "description": "Engineering groups → engineering tag",
    },
    {
        "pattern": "Technical",
        "match_type": "contains",
        "tags": ["engineering"],
        "description": "Technical groups → engineering tag",
    },
    {
        "pattern": "Technology",
        "match_type": "contains",
        "tags": ["engineering"],
        "description": "Technology groups → engineering tag",
    },
    # --- Leadership / management ---
    {
        "pattern": "Leadership",
        "match_type": "contains",
        "tags": ["leadership"],
        "description": "Leadership groups → leadership tag",
    },
    {
        "pattern": "Managers",
        "match_type": "suffix",
        "tags": ["leadership"],
        "description": "Manager groups → leadership tag",
    },
    {
        "pattern": "Delivery Managers",
        "match_type": "exact",
        "tags": ["leadership"],
        "description": "Delivery Managers → leadership tag",
    },
    # --- Security / legal ---
    {
        "pattern": "SG - Legal",
        "match_type": "exact",
        "tags": ["contracts"],
        "description": "Legal security group → contracts tag",
    },
    {
        "pattern": "Security",
        "match_type": "exact",
        "tags": ["all-staff"],
        "description": "Security team → all-staff tag",
    },
    {
        "pattern": "SG - FSO Security",
        "match_type": "prefix",
        "tags": ["all-staff"],
        "description": "FSO Security → all-staff tag",
    },
]


@dataclass
class GroupTagMapping:
    """A mapping from an Entra group to access tags."""

    group_id: str
    group_name: str
    group_type: str
    tags: list[str]
    rule_description: str
    member_count: int = 0


@dataclass
class UserPermissionRecord:
    """A user's aggregated permissions derived from group memberships."""

    user_id: str
    display_name: str
    user_principal_name: str
    department: str
    job_title: str
    user_type: str
    groups: list[str]  # Group names
    tags: list[str]  # Aggregated, deduplicated access tags


@dataclass
class PermissionMappings:
    """Complete permission mappings output."""

    version: str = "1.0"
    group_mappings: list[GroupTagMapping] = field(default_factory=list)
    user_permissions: list[UserPermissionRecord] = field(default_factory=list)
    unmapped_groups: list[dict] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible dict."""
        return {
            "version": self.version,
            "generated_at": "",  # Filled at write time
            "stats": self.stats,
            "group_mappings": [
                {
                    "group_id": m.group_id,
                    "group_name": m.group_name,
                    "group_type": m.group_type,
                    "access_tags": m.tags,
                    "rule": m.rule_description,
                    "member_count": m.member_count,
                }
                for m in self.group_mappings
            ],
            "user_permissions": [
                {
                    "user_id": u.user_id,
                    "display_name": u.display_name,
                    "upn": u.user_principal_name,
                    "department": u.department,
                    "job_title": u.job_title,
                    "user_type": u.user_type,
                    "groups": u.groups,
                    "access_tags": u.tags,
                }
                for u in self.user_permissions
            ],
            "unmapped_groups": self.unmapped_groups,
        }


class PermissionMapper:
    """Map Entra ID groups to S3 access tags.

    Uses configurable rules to match Entra group names to access tags
    from the existing ``access_rules.yaml`` system.
    """

    def __init__(
        self,
        rules: list[dict] | None = None,
    ) -> None:
        self._rules = rules or DEFAULT_GROUP_TAG_RULES

    def generate_mappings(self, data: EntraData) -> PermissionMappings:
        """Generate complete permission mappings from parsed Entra data.

        Args:
            data: Parsed Entra ID data with indexes built.

        Returns:
            PermissionMappings with group mappings, user permissions, and stats.
        """
        result = PermissionMappings()

        # Step 1: Map groups to tags
        mapped_group_ids: set[str] = set()
        for group in data.groups:
            tags = self._match_group(group.display_name)
            if tags:
                members = data.get_group_members(group.id)
                result.group_mappings.append(
                    GroupTagMapping(
                        group_id=group.id,
                        group_name=group.display_name,
                        group_type=group.group_types or "Security",
                        tags=sorted(tags),
                        rule_description=self._get_rule_description(
                            group.display_name
                        ),
                        member_count=len(members),
                    )
                )
                mapped_group_ids.add(group.id)

        # Step 2: Identify unmapped groups
        for group in data.groups:
            if group.id not in mapped_group_ids:
                members = data.get_group_members(group.id)
                result.unmapped_groups.append(
                    {
                        "group_id": group.id,
                        "group_name": group.display_name,
                        "group_type": group.group_types or "Security",
                        "member_count": len(members),
                        "description": group.description[:100] if group.description else "",
                    }
                )

        # Step 3: Build user permission records
        # Build a map of group_id → tags for quick lookup
        group_tags: dict[str, list[str]] = {
            m.group_id: m.tags for m in result.group_mappings
        }

        users_with_tags: dict[str, UserPermissionRecord] = {}
        for membership in data.memberships:
            if membership.group_id not in group_tags:
                continue

            user_id = membership.member_id
            if user_id not in users_with_tags:
                user = data.get_user_by_id(user_id)
                users_with_tags[user_id] = UserPermissionRecord(
                    user_id=user_id,
                    display_name=membership.member_name,
                    user_principal_name=membership.member_upn,
                    department=user.department if user else membership.member_department,
                    job_title=user.job_title if user else membership.member_job_title,
                    user_type=user.user_type if user else "Unknown",
                    groups=[],
                    tags=[],
                )

            record = users_with_tags[user_id]
            record.groups.append(membership.group_name)
            record.tags.extend(group_tags[membership.group_id])

        # Deduplicate and sort tags/groups per user
        for record in users_with_tags.values():
            record.groups = sorted(set(record.groups))
            # Always include all-staff for active members
            tag_set = set(record.tags)
            tag_set.add("all-staff")
            record.tags = sorted(tag_set)

        result.user_permissions = sorted(
            users_with_tags.values(), key=lambda u: u.display_name
        )

        # Step 4: Compute stats
        result.stats = {
            "total_groups": len(data.groups),
            "mapped_groups": len(result.group_mappings),
            "unmapped_groups": len(result.unmapped_groups),
            "total_users": len(data.users),
            "users_with_tag_assignments": len(result.user_permissions),
            "unique_tags_assigned": len(
                set(t for m in result.group_mappings for t in m.tags)
            ),
        }

        return result

    def _match_group(self, group_name: str) -> list[str]:
        """Match a group name against rules, return aggregated tags."""
        all_tags: set[str] = set()
        name_lower = group_name.lower()

        for rule in self._rules:
            pattern_lower = rule["pattern"].lower()
            match_type = rule.get("match_type", "contains")

            matched = False
            if match_type == "exact":
                matched = name_lower == pattern_lower
            elif match_type == "prefix":
                matched = name_lower.startswith(pattern_lower)
            elif match_type == "suffix":
                matched = name_lower.endswith(pattern_lower)
            elif match_type == "contains":
                matched = pattern_lower in name_lower

            if matched:
                all_tags.update(rule["tags"])

        return sorted(all_tags)

    def _get_rule_description(self, group_name: str) -> str:
        """Return descriptions of all matching rules for a group."""
        descriptions = []
        name_lower = group_name.lower()

        for rule in self._rules:
            pattern_lower = rule["pattern"].lower()
            match_type = rule.get("match_type", "contains")

            matched = False
            if match_type == "exact":
                matched = name_lower == pattern_lower
            elif match_type == "prefix":
                matched = name_lower.startswith(pattern_lower)
            elif match_type == "suffix":
                matched = name_lower.endswith(pattern_lower)
            elif match_type == "contains":
                matched = pattern_lower in name_lower

            if matched:
                descriptions.append(rule["description"])

        return "; ".join(descriptions)

    @staticmethod
    def write_mappings(
        mappings: PermissionMappings,
        output_path: str | Path,
    ) -> None:
        """Write permission mappings to a JSON file."""
        from datetime import datetime, timezone

        data = mappings.to_dict()
        data["generated_at"] = datetime.now(timezone.utc).isoformat()

        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        logger.info("Wrote permission mappings to %s", path)
