"""Validate permission mappings and produce a human-readable report.

Checks for:
- Coverage: what percentage of groups/users have tag assignments
- Consistency: tags in mappings match tags defined in access_rules.yaml
- Orphans: users in Entra who aren't in any mapped group
- Gaps: groups with members but no tag assignment
- Duplicates: users appearing in conflicting groups
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from lib.entra_id_parser.models import EntraData
from lib.permission_mapper.mapper import PermissionMappings

logger = logging.getLogger(__name__)


@dataclass
class ValidationIssue:
    """A single validation finding."""

    severity: str  # "error", "warning", "info"
    category: str  # "coverage", "consistency", "orphan", "gap", "duplicate"
    message: str
    details: str = ""


@dataclass
class ValidationResult:
    """Complete validation output."""

    issues: list[ValidationIssue] = field(default_factory=list)
    coverage_stats: dict = field(default_factory=dict)
    tag_distribution: dict[str, int] = field(default_factory=dict)
    unmapped_group_summary: list[dict] = field(default_factory=list)
    orphan_users: list[dict] = field(default_factory=list)

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "warning")

    @property
    def info_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "info")

    @property
    def is_valid(self) -> bool:
        return self.error_count == 0


class MappingValidator:
    """Validate permission mappings against Entra data and access rules."""

    def __init__(
        self,
        known_tags: set[str] | None = None,
    ) -> None:
        """
        Args:
            known_tags: Set of valid access tags from access_rules.yaml.
                        If None, tag consistency checks are skipped.
        """
        self._known_tags = known_tags

    def validate(
        self,
        mappings: PermissionMappings,
        entra_data: EntraData,
    ) -> ValidationResult:
        """Run all validation checks and return results."""
        result = ValidationResult()

        self._check_coverage(result, mappings, entra_data)
        self._check_tag_consistency(result, mappings)
        self._check_orphan_users(result, mappings, entra_data)
        self._check_large_unmapped_groups(result, mappings)
        self._check_tag_distribution(result, mappings)
        self._check_guest_access(result, mappings, entra_data)

        return result

    # ------------------------------------------------------------------
    # Validation checks
    # ------------------------------------------------------------------

    def _check_coverage(
        self,
        result: ValidationResult,
        mappings: PermissionMappings,
        entra_data: EntraData,
    ) -> None:
        """Check what percentage of groups and users have mappings."""
        total_groups = len(entra_data.groups)
        mapped_groups = len(mappings.group_mappings)
        group_pct = (mapped_groups / total_groups * 100) if total_groups else 0

        total_members = len(entra_data.active_members)
        users_with_tags = len(mappings.user_permissions)
        user_pct = (users_with_tags / total_members * 100) if total_members else 0

        result.coverage_stats = {
            "total_groups": total_groups,
            "mapped_groups": mapped_groups,
            "group_coverage_pct": round(group_pct, 1),
            "total_active_members": total_members,
            "users_with_tags": users_with_tags,
            "user_coverage_pct": round(user_pct, 1),
            "total_active_guests": len(entra_data.active_guests),
        }

        if group_pct < 10:
            result.issues.append(
                ValidationIssue(
                    severity="warning",
                    category="coverage",
                    message=f"Low group coverage: {mapped_groups}/{total_groups} ({group_pct:.1f}%)",
                    details="Many groups are project/contract-specific and don't map to SharePoint library access tags. This is expected.",
                )
            )
        else:
            result.issues.append(
                ValidationIssue(
                    severity="info",
                    category="coverage",
                    message=f"Group coverage: {mapped_groups}/{total_groups} ({group_pct:.1f}%)",
                )
            )

        result.issues.append(
            ValidationIssue(
                severity="info",
                category="coverage",
                message=f"User coverage: {users_with_tags}/{total_members} active members ({user_pct:.1f}%)",
            )
        )

    def _check_tag_consistency(
        self,
        result: ValidationResult,
        mappings: PermissionMappings,
    ) -> None:
        """Check that all assigned tags exist in access_rules.yaml."""
        if not self._known_tags:
            result.issues.append(
                ValidationIssue(
                    severity="info",
                    category="consistency",
                    message="Tag consistency check skipped (no known_tags provided)",
                )
            )
            return

        assigned_tags = set()
        for m in mappings.group_mappings:
            assigned_tags.update(m.tags)

        unknown = assigned_tags - self._known_tags
        if unknown:
            result.issues.append(
                ValidationIssue(
                    severity="error",
                    category="consistency",
                    message=f"Tags not defined in access_rules.yaml: {sorted(unknown)}",
                    details="These tags are assigned to groups but don't exist in the access rules. Add them to access_rules.yaml or update the mapping rules.",
                )
            )

        unused = self._known_tags - assigned_tags - {"all-staff"}
        if unused:
            result.issues.append(
                ValidationIssue(
                    severity="warning",
                    category="consistency",
                    message=f"Tags defined but not assigned to any group: {sorted(unused)}",
                    details="These tags exist in access_rules.yaml but no Entra group maps to them.",
                )
            )

    def _check_orphan_users(
        self,
        result: ValidationResult,
        mappings: PermissionMappings,
        entra_data: EntraData,
    ) -> None:
        """Find active members not in any mapped group."""
        users_with_tags = {u.user_id for u in mappings.user_permissions}

        orphans = []
        for user in entra_data.active_members:
            if user.id not in users_with_tags:
                user_groups = entra_data.get_user_groups(user.id)
                orphans.append(
                    {
                        "user_id": user.id,
                        "display_name": user.display_name,
                        "upn": user.user_principal_name,
                        "department": user.department,
                        "group_count": len(user_groups),
                        "groups": [g.group_name for g in user_groups[:5]],
                    }
                )

        result.orphan_users = orphans

        if orphans:
            result.issues.append(
                ValidationIssue(
                    severity="warning",
                    category="orphan",
                    message=f"{len(orphans)} active members have no specific tag assignments (will get all-staff only)",
                    details="These users aren't in any mapped group. They'll only see documents tagged 'all-staff'.",
                )
            )

    def _check_large_unmapped_groups(
        self,
        result: ValidationResult,
        mappings: PermissionMappings,
    ) -> None:
        """Flag unmapped groups with many members."""
        large_unmapped = [
            g for g in mappings.unmapped_groups if g["member_count"] >= 10
        ]

        result.unmapped_group_summary = sorted(
            large_unmapped, key=lambda g: -g["member_count"]
        )

        if large_unmapped:
            result.issues.append(
                ValidationIssue(
                    severity="warning",
                    category="gap",
                    message=f"{len(large_unmapped)} unmapped groups have 10+ members",
                    details="Consider adding mapping rules for these groups if they correspond to document access patterns.",
                )
            )

    def _check_tag_distribution(
        self,
        result: ValidationResult,
        mappings: PermissionMappings,
    ) -> None:
        """Count how many users have each tag."""
        tag_counts: dict[str, int] = {}
        for user in mappings.user_permissions:
            for tag in user.tags:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1

        result.tag_distribution = dict(
            sorted(tag_counts.items(), key=lambda x: -x[1])
        )

    def _check_guest_access(
        self,
        result: ValidationResult,
        mappings: PermissionMappings,
        entra_data: EntraData,
    ) -> None:
        """Check for guests with elevated access tags."""
        sensitive_tags = {"hr", "finance", "contracts", "leadership"}
        guests_with_access = []

        for perm in mappings.user_permissions:
            if perm.user_type == "Guest":
                elevated = set(perm.tags) & sensitive_tags
                if elevated:
                    guests_with_access.append(
                        {
                            "display_name": perm.display_name,
                            "upn": perm.user_principal_name,
                            "elevated_tags": sorted(elevated),
                        }
                    )

        if guests_with_access:
            result.issues.append(
                ValidationIssue(
                    severity="warning",
                    category="consistency",
                    message=f"{len(guests_with_access)} guest users have elevated access tags",
                    details="Guest users with access to sensitive document categories. Review if this is intentional.",
                )
            )

    # ------------------------------------------------------------------
    # Report generation
    # ------------------------------------------------------------------

    @staticmethod
    def write_report(
        result: ValidationResult,
        mappings: PermissionMappings,
        output_path: str | Path,
    ) -> None:
        """Write a Markdown validation report."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        lines: list[str] = []
        _a = lines.append

        _a("# Permission Mapping Validation Report")
        _a("")
        _a(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
        _a("")

        # Summary
        _a("## Summary")
        _a("")
        status = "PASS" if result.is_valid else "FAIL"
        _a(f"**Status**: {status}")
        _a(f"- Errors: {result.error_count}")
        _a(f"- Warnings: {result.warning_count}")
        _a(f"- Info: {result.info_count}")
        _a("")

        # Coverage
        _a("## Coverage Statistics")
        _a("")
        cs = result.coverage_stats
        _a("| Metric | Count | Percentage |")
        _a("|--------|-------|------------|")
        _a(
            f"| Groups mapped | {cs.get('mapped_groups', 0)}/{cs.get('total_groups', 0)} "
            f"| {cs.get('group_coverage_pct', 0)}% |"
        )
        _a(
            f"| Active members with tags | {cs.get('users_with_tags', 0)}/{cs.get('total_active_members', 0)} "
            f"| {cs.get('user_coverage_pct', 0)}% |"
        )
        _a(f"| Active guests | {cs.get('total_active_guests', 0)} | — |")
        _a("")

        # Tag distribution
        if result.tag_distribution:
            _a("## Tag Distribution")
            _a("")
            _a("| Access Tag | Users Assigned |")
            _a("|------------|----------------|")
            for tag, count in result.tag_distribution.items():
                _a(f"| {tag} | {count} |")
            _a("")

        # Group mappings
        _a("## Mapped Groups")
        _a("")
        _a(f"Total: {len(mappings.group_mappings)} groups")
        _a("")
        _a("| Group Name | Type | Tags | Members | Rule |")
        _a("|------------|------|------|---------|------|")
        for m in sorted(mappings.group_mappings, key=lambda x: x.group_name):
            tags_str = ", ".join(m.tags)
            _a(
                f"| {m.group_name} | {m.group_type} | {tags_str} "
                f"| {m.member_count} | {m.rule_description} |"
            )
        _a("")

        # Issues
        _a("## Validation Issues")
        _a("")
        for issue in result.issues:
            icon = {"error": "ERROR", "warning": "WARN", "info": "INFO"}[
                issue.severity
            ]
            _a(f"- **[{icon}]** [{issue.category}] {issue.message}")
            if issue.details:
                _a(f"  - {issue.details}")
        _a("")

        # Large unmapped groups
        if result.unmapped_group_summary:
            _a("## Notable Unmapped Groups (10+ members)")
            _a("")
            _a("| Group Name | Type | Members | Description |")
            _a("|------------|------|---------|-------------|")
            for g in result.unmapped_group_summary[:25]:
                _a(
                    f"| {g['group_name']} | {g['group_type']} "
                    f"| {g['member_count']} | {g['description'][:60]} |"
                )
            if len(result.unmapped_group_summary) > 25:
                _a(f"| ... | ... | ... | ({len(result.unmapped_group_summary) - 25} more) |")
            _a("")

        # Orphan users
        if result.orphan_users:
            _a("## Users Without Specific Tag Assignments")
            _a("")
            _a(f"Total: {len(result.orphan_users)} active members (will receive `all-staff` only)")
            _a("")
            _a("| Name | Department | Groups |")
            _a("|------|-----------|--------|")
            for u in result.orphan_users[:30]:
                groups_str = ", ".join(u["groups"][:3])
                if u["group_count"] > 3:
                    groups_str += f" (+{u['group_count'] - 3} more)"
                _a(f"| {u['display_name']} | {u['department']} | {groups_str} |")
            if len(result.orphan_users) > 30:
                _a(f"| ... | ... | ({len(result.orphan_users) - 30} more) |")
            _a("")

        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        logger.info("Wrote validation report to %s", path)
