# Investigate Permission Denial

## Description

A user reports they cannot find or access a document they expect to see in search
results. This runbook guides you through diagnosing why the query returned no results,
by tracing the user's group memberships, the document's permission mappings, and the
audit trail in CloudWatch.

## Severity

**Low to Medium** -- Unless the user is blocked from time-sensitive material, this is a
standard access-control investigation. Escalate to **High** if multiple users are
affected or if a systemic mapping issue is discovered.

## Prerequisites

- AWS CLI v2 configured with credentials that have access to CloudWatch Logs, DynamoDB,
  and Lambda.
- Permissions: `logs:StartQuery`, `logs:GetQueryResults`, `dynamodb:GetItem`,
  `dynamodb:Scan`, `lambda:InvokeFunction`.
- The affected user's `user_id` or `user_upn` (email address).
- The document or prefix the user expects to access.

---

## Steps

### Step 1: Query the audit logs for the user's denied or empty-result queries

Use CloudWatch Logs Insights to find recent queries from the user that returned no
results:

```bash
# Define the time window (adjust as needed -- example: last 24 hours)
START_TIME=$(date -v-24H +%s 2>/dev/null || date -d '24 hours ago' +%s)
END_TIME=$(date +%s)

QUERY_ID=$(aws logs start-query \
  --log-group-name /aws/lambda/sp-ingest-query-handler \
  --start-time "$START_TIME" \
  --end-time "$END_TIME" \
  --query-string 'filter result_type = "no_results"
    | fields @timestamp, user_id, user_upn, resolved_groups, filters_applied, query_text_hash, response_latency_ms
    | sort @timestamp desc
    | limit 50' \
  --query 'queryId' --output text)

echo "Query ID: $QUERY_ID"
```

Wait a few seconds for the query to complete, then retrieve results:

```bash
aws logs get-query-results --query-id "$QUERY_ID" --output table
```

Look for the user's `user_id` or `user_upn` in the results. Note the `resolved_groups`
and `filters_applied` fields -- these tell you exactly what groups the system resolved
for the user at query time.

If you know the specific user, filter directly:

```bash
QUERY_ID=$(aws logs start-query \
  --log-group-name /aws/lambda/sp-ingest-query-handler \
  --start-time "$START_TIME" \
  --end-time "$END_TIME" \
  --query-string 'filter user_id = "USER_ID_HERE"
    | fields @timestamp, user_upn, resolved_groups, filters_applied, result_type, response_latency_ms
    | sort @timestamp desc
    | limit 20' \
  --query 'queryId' --output text)
```

---

### Step 2: Look up the user's cached group memberships

```bash
aws dynamodb get-item \
  --table-name user-group-cache \
  --key '{"user_id": {"S": "USER_ID_HERE"}}' \
  --output table
```

Record the following from the response:

| Field | What to Check |
|-------|---------------|
| `user_id` | Confirm this matches the expected user |
| `user_upn` | Confirm the UPN (email) is correct |
| `groups` | List of groups the system believes the user belongs to |
| `last_synced` | When the cache was last refreshed -- stale if older than 30 minutes |
| `status` | Should be `active` -- `disabled` or `stale` indicates a problem |

If no item is returned, the user has never been cached. This means the group-cache-refresh
Lambda has not processed this user yet. Skip to Step 5 (resolution: trigger cache refresh).

---

### Step 3: Look up the permission mapping for the target document prefix

If you know the specific document prefix the user is trying to access:

```bash
aws dynamodb get-item \
  --table-name doc-permission-mappings \
  --key '{"s3_prefix": {"S": "source/Dynamo/HR/"}}' \
  --output table
```

If you are unsure which prefix is relevant, scan all mappings:

```bash
aws dynamodb scan \
  --table-name doc-permission-mappings \
  --projection-expression "s3_prefix, allowed_groups, sensitivity_level" \
  --output table
```

Record the `allowed_groups` and `sensitivity_level` for the relevant prefix.

---

### Step 4: Compare the user's groups with the mapping's allowed groups

Lay out the two sets side by side:

| User's Groups (from Step 2) | Mapping's allowed_groups (from Step 3) |
|-----|-----|
| e.g., `Engineering`, `All-Staff` | e.g., `HR`, `Leadership` |

**The user can access the document only if at least one of their groups appears in the
mapping's `allowed_groups` set.**

If there is no overlap, you have identified the cause of the denial.

---

### Step 5: Resolve the issue

Identify which of the following root causes applies and follow the corresponding
resolution:

#### Cause A: User is missing from the correct Entra ID group

The user should be a member of one of the `allowed_groups` but is not.

**Resolution:**
1. Add the user to the appropriate group in the Microsoft Entra admin center.
2. Wait for SCIM provisioning to sync (incremental sync runs every ~40 minutes).
3. After SCIM sync completes, trigger a group-cache-refresh to update the local cache:

   ```bash
   aws lambda invoke \
     --function-name sp-ingest-group-cache-refresh \
     --cli-binary-format raw-in-base64-out \
     --payload '{}' \
     /dev/stdout 2>/dev/null | python3 -m json.tool
   ```

#### Cause B: SCIM provisioning has not synced recent group changes

The user was added to the group in Entra ID but SCIM has not yet propagated the change
to IAM Identity Center.

**Resolution:**
1. Check SCIM provisioning status in the Microsoft Entra admin center under
   **Enterprise applications > Dynamo AI Platform - AWS SSO > Provisioning > Provisioning logs**.
2. If provisioning is delayed or errored, trigger a manual provisioning cycle from the
   Entra admin center.
3. Once SCIM has synced, trigger the group-cache-refresh Lambda (same command as Cause A).

#### Cause C: Stale user-group-cache entry

The `last_synced` timestamp in the cache is older than expected (more than 30 minutes).

**Resolution:**
Trigger the group-cache-refresh Lambda to refresh all user caches:

```bash
aws lambda invoke \
  --function-name sp-ingest-group-cache-refresh \
  --cli-binary-format raw-in-base64-out \
  --payload '{}' \
  /dev/stdout 2>/dev/null | python3 -m json.tool
```

Then verify the cache was updated:

```bash
aws dynamodb get-item \
  --table-name user-group-cache \
  --key '{"user_id": {"S": "USER_ID_HERE"}}' \
  --projection-expression "groups, last_synced" \
  --output table
```

#### Cause D: No permission mapping exists for the document's prefix

The document prefix has no entry in `doc-permission-mappings`.

**Resolution:**
Create the mapping with the correct groups and sensitivity level:

```bash
aws dynamodb put-item \
  --table-name doc-permission-mappings \
  --item '{
    "s3_prefix": {"S": "source/Dynamo/TARGET_PREFIX/"},
    "allowed_groups": {"SS": ["GroupA", "GroupB"]},
    "sensitivity_level": {"S": "internal"},
    "created_at": {"S": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"},
    "created_by": {"S": "OPERATOR_NAME - permission denial investigation"}
  }'
```

#### Cause E: Sensitivity level mismatch

The mapping exists and the user has the right group, but the `sensitivity_level` on the
mapping is higher than what the user is cleared for.

**Resolution:**
1. Verify the user's clearance level with the data owner or compliance team.
2. Either upgrade the user's access level or confirm the denial is correct (document the
   decision).

---

### Step 6: Verify the fix

Ask the user to re-run their query, or simulate it yourself. Then check the audit logs
for a successful result:

```bash
QUERY_ID=$(aws logs start-query \
  --log-group-name /aws/lambda/sp-ingest-query-handler \
  --start-time $(date -v-5M +%s 2>/dev/null || date -d '5 minutes ago' +%s) \
  --end-time $(date +%s) \
  --query-string 'filter user_id = "USER_ID_HERE" and result_type = "success"
    | fields @timestamp, user_upn, resolved_groups, result_type
    | sort @timestamp desc
    | limit 5' \
  --query 'queryId' --output text)

# Wait a few seconds, then:
aws logs get-query-results --query-id "$QUERY_ID" --output table
```

Confirm `result_type` is `success` and the `resolved_groups` now include the expected
group.

---

## Verification Checklist

- [ ] Root cause identified (Cause A through E).
- [ ] User's `user-group-cache` entry shows the correct groups.
- [ ] Permission mapping for the document prefix includes the user's group in `allowed_groups`.
- [ ] User's query now returns `result_type = "success"` in the audit log.
- [ ] User confirms they can access the expected documents.

---

## Troubleshooting

| Symptom | Likely Cause | Resolution |
|---------|-------------|------------|
| No audit log entries for the user at all | User has never queried the system, or the log group name is wrong | Verify the log group is `/aws/lambda/sp-ingest-query-handler`. Ask the user to attempt a query. |
| `resolved_groups` is empty in the audit log | Cache entry missing or cache refresh failed | Check if the user exists in `user-group-cache`. Trigger a cache refresh. |
| User has the right group but still gets `no_results` | The `s3_prefix` in the mapping may not match the document's actual key | Run a scan of `doc-permission-mappings` and compare prefixes character-by-character with the document's S3 key. |
| Cache refresh Lambda times out | Too many users to process in a single invocation | Check CloudWatch logs at `/aws/lambda/sp-ingest-group-cache-refresh`. May need to increase Lambda timeout or memory. |
| SCIM provisioning shows errors in Entra | Expired SCIM token or IAM Identity Center misconfiguration | Refer to `docs/ENTRA_ID_SCIM_SETUP_RUNBOOK.md` for token rotation instructions. |
| Multiple users in the same group are all denied | The group name in `doc-permission-mappings` does not exactly match the group name in Entra ID / SCIM | Compare group names exactly (case-sensitive). Update the mapping or the Entra group name to match. |
