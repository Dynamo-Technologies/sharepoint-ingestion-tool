"""Permission mapper — bridge Entra ID groups to S3 access-control tags.

Maps Entra ID group memberships to the access tags used by the existing
``access_control.py`` / ``access_rules.yaml`` system.  Generates a
``permission_mappings.json`` that connects users → groups → S3 prefixes → tags.
"""

from lib.permission_mapper.mapper import PermissionMapper
from lib.permission_mapper.validator import MappingValidator

__all__ = ["PermissionMapper", "MappingValidator"]
