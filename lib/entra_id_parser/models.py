"""Data models for Entra ID exports."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class EntraUser:
    """A user from the Entra ID Users export."""

    display_name: str
    user_principal_name: str
    id: str
    job_title: str = ""
    department: str = ""
    office_location: str = ""
    account_enabled: bool = True
    user_type: str = "Member"  # "Member" or "Guest"
    created_datetime: str = ""
    company_name: str = ""
    city: str = ""
    state: str = ""
    country: str = ""
    extension_attributes: dict[str, str] = field(default_factory=dict)

    @property
    def is_member(self) -> bool:
        return self.user_type == "Member"

    @property
    def is_guest(self) -> bool:
        return self.user_type == "Guest"

    @property
    def is_active(self) -> bool:
        return self.account_enabled

    @property
    def email_domain(self) -> str:
        """Extract domain from UPN (handles EXT guests)."""
        upn = self.user_principal_name
        if "#EXT#" in upn:
            # Guest format: user_domain.com#EXT#@tenant.onmicrosoft.com
            ext_part = upn.split("#EXT#")[0]
            return ext_part.rsplit("_", 1)[-1] if "_" in ext_part else ""
        return upn.split("@")[-1] if "@" in upn else ""


@dataclass
class EntraGroup:
    """A group from the Entra ID Groups export."""

    display_name: str
    id: str
    group_types: str = ""  # "Unified", "DynamicMembership", or "" (Security)
    security_enabled: bool = False
    mail_enabled: bool = False
    membership_type: str = "Assigned"
    membership_rule: str = ""
    description: str = ""
    created_datetime: str = ""

    @property
    def is_m365_group(self) -> bool:
        return self.group_types == "Unified"

    @property
    def is_security_group(self) -> bool:
        return self.group_types == "" or self.security_enabled

    @property
    def is_dynamic(self) -> bool:
        return self.group_types == "DynamicMembership"


@dataclass
class GroupMembership:
    """A user-group membership from the Full Export."""

    group_name: str
    group_id: str
    group_type: str
    membership_type: str
    member_name: str
    member_upn: str
    member_job_title: str = ""
    member_department: str = ""
    member_enabled: bool = True
    member_id: str = ""


@dataclass
class ConditionalAccessPolicy:
    """A conditional access policy."""

    display_name: str
    state: str  # "enabled", "disabled", "enabledForReportingButNotEnforced"
    include_users: str = ""
    include_groups: str = ""
    exclude_users: str = ""
    exclude_groups: str = ""
    include_applications: str = ""
    grant_controls: str = ""
    created_datetime: str = ""
    modified_datetime: str = ""

    @property
    def is_enabled(self) -> bool:
        return self.state == "enabled"


@dataclass
class ExportError:
    """An error from the user-group relationship export."""

    resource_id: str
    error_type: str
    message: str


@dataclass
class EntraData:
    """Container for all parsed Entra ID data."""

    users: list[EntraUser] = field(default_factory=list)
    groups: list[EntraGroup] = field(default_factory=list)
    memberships: list[GroupMembership] = field(default_factory=list)
    conditional_access_policies: list[ConditionalAccessPolicy] = field(
        default_factory=list
    )
    export_errors: list[ExportError] = field(default_factory=list)

    # Indexes built after parsing
    _users_by_id: dict[str, EntraUser] = field(default_factory=dict, repr=False)
    _users_by_upn: dict[str, EntraUser] = field(default_factory=dict, repr=False)
    _groups_by_id: dict[str, EntraGroup] = field(default_factory=dict, repr=False)
    _groups_by_name: dict[str, EntraGroup] = field(default_factory=dict, repr=False)
    _members_by_group: dict[str, list[GroupMembership]] = field(
        default_factory=dict, repr=False
    )
    _groups_by_member: dict[str, list[GroupMembership]] = field(
        default_factory=dict, repr=False
    )

    def build_indexes(self) -> None:
        """Build lookup indexes after all data is loaded."""
        self._users_by_id = {u.id: u for u in self.users}
        self._users_by_upn = {u.user_principal_name.lower(): u for u in self.users}
        self._groups_by_id = {g.id: g for g in self.groups}
        self._groups_by_name = {g.display_name: g for g in self.groups}

        self._members_by_group.clear()
        self._groups_by_member.clear()
        for m in self.memberships:
            self._members_by_group.setdefault(m.group_id, []).append(m)
            self._groups_by_member.setdefault(m.member_id, []).append(m)

    def get_user_by_id(self, user_id: str) -> EntraUser | None:
        return self._users_by_id.get(user_id)

    def get_user_by_upn(self, upn: str) -> EntraUser | None:
        return self._users_by_upn.get(upn.lower())

    def get_group_by_id(self, group_id: str) -> EntraGroup | None:
        return self._groups_by_id.get(group_id)

    def get_group_by_name(self, name: str) -> EntraGroup | None:
        return self._groups_by_name.get(name)

    def get_group_members(self, group_id: str) -> list[GroupMembership]:
        return self._members_by_group.get(group_id, [])

    def get_user_groups(self, member_id: str) -> list[GroupMembership]:
        return self._groups_by_member.get(member_id, [])

    @property
    def active_members(self) -> list[EntraUser]:
        return [u for u in self.users if u.is_member and u.is_active]

    @property
    def active_guests(self) -> list[EntraUser]:
        return [u for u in self.users if u.is_guest and u.is_active]

    @property
    def m365_groups(self) -> list[EntraGroup]:
        return [g for g in self.groups if g.is_m365_group]

    @property
    def security_groups(self) -> list[EntraGroup]:
        return [g for g in self.groups if g.is_security_group]

    def summary(self) -> dict:
        """Return a summary of parsed data."""
        return {
            "total_users": len(self.users),
            "active_members": len(self.active_members),
            "active_guests": len(self.active_guests),
            "disabled_users": len([u for u in self.users if not u.is_active]),
            "total_groups": len(self.groups),
            "m365_groups": len(self.m365_groups),
            "security_groups": len(self.security_groups),
            "dynamic_groups": len([g for g in self.groups if g.is_dynamic]),
            "total_memberships": len(self.memberships),
            "conditional_access_policies": len(self.conditional_access_policies),
            "export_errors": len(self.export_errors),
        }
