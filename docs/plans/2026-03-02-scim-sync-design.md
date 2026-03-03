# IAM Identity Center SCIM Integration & Automated Sync — Design

> **Prompt 6 of 8** — SCIM Integration and Automated Sync

**Goal:** Set up the automated sync pipeline that keeps AWS-side permission data in sync with Entra ID via IAM Identity Center SCIM provisioning, plus group flattening, cache refresh, drift detection, and stale account cleanup.

---

## Architecture

```
Microsoft Entra ID
    │
    │  SCIM provisioning (automatic)
    │  Pushes users + groups every 40 min
    │
    ▼
AWS IAM Identity Center (Identity Store)
    │
    ├─── EventBridge (every 15 min) ──► group-cache-refresh Lambda
    │       ├─ identitystore:ListUsers/ListGroupMemberships
    │       ├─ Flatten nested group memberships
    │       ├─ Compare with previous cache → log changes
    │       └─ Write to user-group-cache DynamoDB
    │
    ├─── EventBridge (weekly, Sun 02:00 UTC) ──► permission-drift-detector Lambda
    │       ├─ S3 ListObjectsV2 (enumerate source/ prefixes)
    │       ├─ DynamoDB Scan doc-permission-mappings
    │       ├─ identitystore:ListGroups (validate group IDs)
    │       ├─ Write drift-report JSON to s3://bucket/governance-reports/
    │       └─ SNS alert if unmapped prefixes found
    │
    └─── EventBridge (daily, 03:00 UTC) ──► stale-account-cleanup Lambda
            ├─ DynamoDB Scan user-group-cache
            ├─ identitystore:DescribeUser (check status)
            ├─ Disabled → empty groups list (revoke RAG access)
            └─ Deleted → status:deleted + 90-day TTL
```

**Key decision:** IAM Identity Center must be enabled manually in the AWS console. Terraform references it via data source. SCIM provisioning from Entra ID must be configured manually in the Entra portal (documented in runbook).

---

## Module Structure

```
lib/identity_store/
├── __init__.py
├── client.py              # IdentityStoreClient — paginated API wrapper
└── group_flattener.py     # GroupFlattener — nested group resolution

src/
├── group_cache_refresh.py          # Lambda handler
├── permission_drift_detector.py    # Lambda handler
└── stale_account_cleanup.py        # Lambda handler

tests/
├── test_identity_store.py          # IdentityStoreClient + GroupFlattener tests
├── test_group_cache_refresh.py     # Lambda handler tests
├── test_permission_drift_detector.py  # Lambda handler tests
├── test_stale_account_cleanup.py   # Lambda handler tests
└── test_scim_e2e.py                # End-to-end simulation test

terraform/
├── sso.tf                 # IAM Identity Center data source
├── lambda_scim.tf         # 3 Lambdas + EventBridge rules + DLQs
└── iam_scim.tf            # 3 IAM roles (least privilege)

docs/
└── ENTRA_ID_SCIM_SETUP_RUNBOOK.md  # Manual Entra portal steps
```

---

## Components

### 1. IdentityStoreClient (`lib/identity_store/client.py`)

Paginated wrapper around the `identitystore` boto3 API.

Methods:
- `list_users()` → yields all users (handles pagination)
- `list_groups()` → yields all groups
- `list_group_memberships(group_id)` → yields members of a group
- `list_group_memberships_for_member(member_id)` → yields groups a member belongs to
- `describe_user(user_id)` → returns user details including active status

All methods handle pagination via `NextToken` patterns.

### 2. GroupFlattener (`lib/identity_store/group_flattener.py`)

Resolves nested group memberships into flat per-user lists.

Algorithm:
1. Build group membership graph: `{group_id: set(parent_group_ids)}`
2. For each user, get direct group memberships
3. BFS/DFS upward through the graph to collect all ancestor groups
4. Return `{user_id: {all_group_ids}}`

Handles:
- Direct group membership
- Nested groups (group-in-group, 3+ levels deep)
- Circular references (cycle detection via visited set)

### 3. group-cache-refresh Lambda (`src/group_cache_refresh.py`)

- **Trigger:** EventBridge schedule every 15 minutes
- **Flow:**
  1. Use `IdentityStoreClient` to list all users and groups
  2. Use `GroupFlattener` to produce flat group lists
  3. For each user, read current cache from `user-group-cache`
  4. If groups changed: log added/removed groups for audit
  5. Write updated `user-group-cache` record with: user_id, upn, groups, custom_attributes, last_synced, source="scim", ttl_expiry (24h from now)
- **Idempotent:** safe to re-run; overwrites with latest data

### 4. permission-drift-detector Lambda (`src/permission_drift_detector.py`)

- **Trigger:** EventBridge schedule weekly (Sunday 02:00 UTC)
- **Flow:**
  1. List all S3 prefixes under `source/` containing objects
  2. Scan `doc-permission-mappings` table for all mapped prefixes
  3. List all groups from identitystore for validation
  4. Identify:
     - **Unmapped prefixes:** S3 prefix with docs but no permission mapping
     - **Stale mappings:** mapping exists but no S3 objects
     - **Orphaned groups:** group ID in mapping but not in Identity Store
  5. Write `governance-reports/drift-report-YYYY-MM-DD.json` to S3
  6. If unmapped prefixes found, publish SNS alert to governance topic

### 5. stale-account-cleanup Lambda (`src/stale_account_cleanup.py`)

- **Trigger:** EventBridge schedule daily (03:00 UTC)
- **Flow:**
  1. Scan `user-group-cache` for all records without `status: deleted`
  2. For each user, call `identitystore:DescribeUser`
  3. If user not found (deleted from Entra): mark `status: deleted`, set `ttl_expiry` to 90 days from now
  4. If user found but inactive: empty `groups` list, keep record for audit
  5. If user active: skip (no change)
  6. Log all account status changes

### 6. Infrastructure (Terraform)

**New resources:**
- `data.aws_ssoadmin_instances` — reference existing IAM Identity Center
- Variable `identity_store_id` — Identity Store ID
- 3 Lambda functions with proper env vars
- 3 EventBridge schedule rules (15 min, weekly, daily)
- 3 SQS dead-letter queues
- 3 CloudWatch log groups (90-day retention)
- 3 IAM roles with least-privilege policies
- 1 SNS topic: `governance-alerts`

**IAM permissions per Lambda:**

| Lambda | DynamoDB | S3 | identitystore | SNS | SQS |
|--------|----------|------|---------------|-----|-----|
| group-cache-refresh | RW user-group-cache | — | ListUsers, ListGroups, ListGroupMemberships, ListGroupMembershipsForMember | — | — |
| permission-drift-detector | R permission-mappings | ListBucket, PutObject (governance-reports/) | ListGroups | Publish governance-alerts | — |
| stale-account-cleanup | RW user-group-cache | — | DescribeUser | — | — |

---

## Testing Strategy

Unit tests with mocked identitystore + DynamoDB (moto):

- **IdentityStoreClient:** pagination, empty results, API errors
- **GroupFlattener:** flat groups, 2-level nesting, 3-level nesting, circular reference, empty groups
- **group-cache-refresh:** full flow with group changes detected, no changes, new user, removed user
- **permission-drift-detector:** mixed mapped/unmapped prefixes, stale mappings, orphaned groups
- **stale-account-cleanup:** disabled user (groups emptied), deleted user (status+TTL set), active user (no change)
- **End-to-end simulation:** modify group → refresh cache → verify cache → query middleware with new permissions

---

## Drift Report Format

```json
{
  "report_date": "2026-03-02",
  "summary": {
    "total_s3_prefixes": 26,
    "mapped_prefixes": 24,
    "unmapped_prefixes": 2,
    "stale_mappings": 1,
    "orphaned_groups": 0
  },
  "unmapped_prefixes": [
    "source/Dynamo/NewFolder"
  ],
  "stale_mappings": [
    "source/Dynamo/OldFolder"
  ],
  "orphaned_groups": []
}
```
