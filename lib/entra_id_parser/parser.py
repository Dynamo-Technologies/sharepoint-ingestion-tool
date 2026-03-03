"""Parse Microsoft Entra ID CSV exports into structured data.

Handles the following export files:
- Users export (Dynamo_EntraID_Users_Export.csv)
- Groups export (Dynamo_EntraID_Groups_Export.csv)
- Full export with group-member relationships (Dynamo_EntraID_Full_Export.csv)
- Custom attributes (Dynamo_CustomAttributes.csv)
- Conditional access policies (Dynamo_ConditionalAccess.csv)
- Error logs from export (errors_on_User_Group_relationship_run.txt)
"""

import csv
import logging
import re
from pathlib import Path

from lib.entra_id_parser.models import (
    ConditionalAccessPolicy,
    EntraData,
    EntraGroup,
    EntraUser,
    ExportError,
    GroupMembership,
)

logger = logging.getLogger(__name__)


class EntraIDParser:
    """Parse Entra ID CSV exports from a directory."""

    # Default file name patterns
    USERS_PATTERN = "*Users_Export*.csv"
    GROUPS_PATTERN = "*Groups_Export*.csv"
    FULL_EXPORT_PATTERN = "*Full_Export*.csv"
    CUSTOM_ATTRS_PATTERN = "*CustomAttributes*.csv"
    CONDITIONAL_ACCESS_PATTERN = "*ConditionalAccess*.csv"
    ERROR_LOG_PATTERN = "errors_*.txt"

    def __init__(self, export_dir: str | Path) -> None:
        self._dir = Path(export_dir)
        if not self._dir.is_dir():
            raise FileNotFoundError(f"Export directory not found: {self._dir}")

    def parse_all(self) -> EntraData:
        """Parse all available export files and return structured data."""
        data = EntraData()

        # Parse each file type (order matters: users first, then groups, then memberships)
        users_file = self._find_file(self.USERS_PATTERN)
        if users_file:
            data.users = self._parse_users(users_file)
            logger.info("Parsed %d users from %s", len(data.users), users_file.name)

        groups_file = self._find_file(self.GROUPS_PATTERN)
        if groups_file:
            data.groups = self._parse_groups(groups_file)
            logger.info("Parsed %d groups from %s", len(data.groups), groups_file.name)

        full_file = self._find_file(self.FULL_EXPORT_PATTERN)
        if full_file:
            data.memberships = self._parse_full_export(full_file)
            logger.info(
                "Parsed %d memberships from %s",
                len(data.memberships),
                full_file.name,
            )

        # Merge custom attributes into users
        attrs_file = self._find_file(self.CUSTOM_ATTRS_PATTERN)
        if attrs_file:
            self._merge_custom_attributes(data, attrs_file)
            logger.info("Merged custom attributes from %s", attrs_file.name)

        ca_file = self._find_file(self.CONDITIONAL_ACCESS_PATTERN)
        if ca_file:
            data.conditional_access_policies = self._parse_conditional_access(ca_file)
            logger.info(
                "Parsed %d conditional access policies from %s",
                len(data.conditional_access_policies),
                ca_file.name,
            )

        error_file = self._find_file(self.ERROR_LOG_PATTERN)
        if error_file:
            data.export_errors = self._parse_error_log(error_file)
            logger.info(
                "Parsed %d export errors from %s",
                len(data.export_errors),
                error_file.name,
            )

        data.build_indexes()
        return data

    # ------------------------------------------------------------------
    # File discovery
    # ------------------------------------------------------------------

    def _find_file(self, pattern: str) -> Path | None:
        """Find a single file matching the glob pattern."""
        matches = sorted(self._dir.glob(pattern))
        if not matches:
            logger.warning("No file matching '%s' in %s", pattern, self._dir)
            return None
        if len(matches) > 1:
            logger.warning(
                "Multiple files matching '%s', using %s", pattern, matches[0].name
            )
        return matches[0]

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------

    def _parse_users(self, path: Path) -> list[EntraUser]:
        users = []
        for row in self._read_csv(path):
            users.append(
                EntraUser(
                    display_name=row.get("DisplayName", ""),
                    user_principal_name=row.get("UserPrincipalName", ""),
                    id=row.get("Id", ""),
                    job_title=row.get("JobTitle", ""),
                    department=row.get("Department", ""),
                    office_location=row.get("OfficeLocation", ""),
                    account_enabled=row.get("AccountEnabled", "True") == "True",
                    user_type=row.get("UserType", "Member"),
                    created_datetime=row.get("CreatedDateTime", ""),
                    company_name=row.get("CompanyName", ""),
                    city=row.get("City", ""),
                    state=row.get("State", ""),
                    country=row.get("Country", ""),
                )
            )
        return users

    # ------------------------------------------------------------------
    # Groups
    # ------------------------------------------------------------------

    def _parse_groups(self, path: Path) -> list[EntraGroup]:
        groups = []
        for row in self._read_csv(path):
            groups.append(
                EntraGroup(
                    display_name=row.get("DisplayName", ""),
                    id=row.get("Id", ""),
                    group_types=row.get("GroupTypes", ""),
                    security_enabled=row.get("SecurityEnabled", "False") == "True",
                    mail_enabled=row.get("MailEnabled", "False") == "True",
                    membership_type=row.get("MembershipType", "Assigned"),
                    membership_rule=row.get("MembershipRule", ""),
                    description=row.get("Description", ""),
                    created_datetime=row.get("CreatedDateTime", ""),
                )
            )
        return groups

    # ------------------------------------------------------------------
    # Full export (group-member relationships)
    # ------------------------------------------------------------------

    def _parse_full_export(self, path: Path) -> list[GroupMembership]:
        memberships = []
        for row in self._read_csv(path):
            memberships.append(
                GroupMembership(
                    group_name=row.get("GroupName", ""),
                    group_id=row.get("GroupId", ""),
                    group_type=row.get("GroupType", ""),
                    membership_type=row.get("MembershipType", "Assigned"),
                    member_name=row.get("MemberName", ""),
                    member_upn=row.get("MemberUPN", ""),
                    member_job_title=row.get("MemberJobTitle", ""),
                    member_department=row.get("MemberDept", ""),
                    member_enabled=row.get("MemberEnabled", "True") == "True",
                    member_id=row.get("MemberId", ""),
                )
            )
        return memberships

    # ------------------------------------------------------------------
    # Custom attributes
    # ------------------------------------------------------------------

    def _merge_custom_attributes(self, data: EntraData, path: Path) -> None:
        """Merge extension attributes from the custom attributes CSV into users."""
        attrs_by_id: dict[str, dict[str, str]] = {}
        for row in self._read_csv(path):
            user_id = row.get("Id", "")
            if not user_id:
                continue
            ext_attrs = {}
            for i in range(1, 16):
                val = row.get(f"ExtAttr{i}", "")
                if val:
                    ext_attrs[f"extensionAttribute{i}"] = val
            if ext_attrs:
                attrs_by_id[user_id] = ext_attrs

        merged = 0
        for user in data.users:
            if user.id in attrs_by_id:
                user.extension_attributes = attrs_by_id[user.id]
                merged += 1

        logger.info(
            "Merged extension attributes for %d/%d users", merged, len(attrs_by_id)
        )

    # ------------------------------------------------------------------
    # Conditional access
    # ------------------------------------------------------------------

    def _parse_conditional_access(self, path: Path) -> list[ConditionalAccessPolicy]:
        policies = []
        for row in self._read_csv(path):
            policies.append(
                ConditionalAccessPolicy(
                    display_name=row.get("DisplayName", ""),
                    state=row.get("State", ""),
                    include_users=row.get("IncludeUsers", ""),
                    include_groups=row.get("IncludeGroups", ""),
                    exclude_users=row.get("ExcludeUsers", ""),
                    exclude_groups=row.get("ExcludeGroups", ""),
                    include_applications=row.get("IncludeApplications", ""),
                    grant_controls=row.get("GrantControls", ""),
                    created_datetime=row.get("CreatedDateTime", ""),
                    modified_datetime=row.get("ModifiedDateTime", ""),
                )
            )
        return policies

    # ------------------------------------------------------------------
    # Error log
    # ------------------------------------------------------------------

    def _parse_error_log(self, path: Path) -> list[ExportError]:
        """Parse PowerShell Graph API error logs."""
        errors = []
        text = path.read_text(encoding="utf-8", errors="replace")

        # Pattern: "Resource '<id>' does not exist..."
        pattern = re.compile(
            r"Resource '([a-f0-9-]+)' does not exist.*?"
            r"Status:\s*(\d+ \([^)]+\))",
            re.DOTALL,
        )
        for match in pattern.finditer(text):
            errors.append(
                ExportError(
                    resource_id=match.group(1),
                    error_type=match.group(2),
                    message=f"Resource {match.group(1)} not found",
                )
            )

        return errors

    # ------------------------------------------------------------------
    # CSV helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _read_csv(path: Path) -> list[dict[str, str]]:
        """Read a CSV file and return rows as dicts."""
        rows = []
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Strip whitespace from keys and values
                cleaned = {
                    k.strip(): v.strip() if v else "" for k, v in row.items() if k
                }
                rows.append(cleaned)
        return rows
