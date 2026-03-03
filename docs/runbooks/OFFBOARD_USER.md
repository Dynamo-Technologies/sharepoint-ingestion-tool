# Offboard User Runbook

Revoke a user's access to the SharePoint document ingestion pipeline. This removes
or disables the user in Entra ID, propagates the change via SCIM to IAM Identity
Center, clears their cached group memberships, and audits their recent activity.

## Prerequisites

- Microsoft Entra admin center access (User Administrator or higher)
- AWS CLI configured with permissions for DynamoDB, Lambda, and CloudWatch Logs
- The user's email address / UPN and their Identity Center User ID
- Confirmation from HR or management that the user should be offboarded

## Step 1: Remove User from Entra ID Security Groups

Choose the appropriate action based on the offboarding scenario:

### Option A: Remove from specific groups (role change, not leaving org)

1. Open **Microsoft Entra admin center** -> **Users** -> **All users**
2. Search for and select the user
3. Go to **Groups**
4. Select the groups to remove the user from
5. Click **Remove membership**

### Option B: Disable user account (leaving organization)

1. Open **Microsoft Entra admin center** -> **Users** -> **All users**
2. Search for and select the user
3. Click **Properties** -> **Edit**
4. Set **Account enabled** to **No**
5. Click **Save**

Disabling the account blocks sign-in and triggers SCIM to deprovision the user
from all downstream applications.

### Option C: Delete user account (permanent removal)

1. Open **Microsoft Entra admin center** -> **Users** -> **All users**
2. Search for and select the user
3. Click **Delete user**
4. Confirm deletion

**Note:** Deleted users move to the Entra ID recycle bin for 30 days before permanent
deletion. SCIM deprovisioning is triggered immediately.

## Step 2: Wait for SCIM Propagation

SCIM incremental sync runs approximately every 40 minutes. The user's removal,
disabling, or deletion will be propagated to IAM Identity Center automatically.

**Verify provisioning status in Entra:**

1. In the enterprise application, go to **Provisioning** -> **Provisioning logs**
2. Filter by the user's display name
3. Confirm the action shows **Disable** or **Delete** with status **Success**

## Step 3: Trigger Group Cache Refresh

Force an immediate update of the group cache to revoke access without waiting
for the next scheduled refresh (every 15 minutes):

```bash
aws lambda invoke \
  --function-name sp-ingest-group-cache-refresh \
  --payload '{}' \
  --cli-binary-format raw-in-base64-out \
  /dev/stdout 2>/dev/null | python3 -m json.tool
```

Verify the Lambda completed successfully (check for `"statusCode": 200`).

## Step 4: Verify User Record Updated in DynamoDB

Check that the user's cached record reflects the revocation:

```bash
aws dynamodb get-item \
  --table-name user-group-cache \
  --key '{"user_id": {"S": "USER_ID"}}' \
  --output json | python3 -m json.tool
```

Replace `USER_ID` with the user's Identity Center User ID.

**Expected state after offboarding:**

| Scenario | Expected `status` | Expected `groups` |
|----------|-------------------|-------------------|
| Removed from groups | `active` | Empty list or reduced list |
| Account disabled | `disabled` or `deleted` | Cleared |
| Account deleted | `deleted` | Cleared |

If the user's groups are still populated, re-run the group-cache-refresh and check
again. If the issue persists, check the Lambda's CloudWatch logs:

```bash
aws logs tail /aws/lambda/sp-ingest-group-cache-refresh --since 1h --format short
```

## Step 5: Run Stale Account Cleanup

The stale-account-cleanup Lambda runs daily at 03:00 UTC and sets TTL on
records for deleted/disabled users. You can trigger it immediately:

```bash
aws lambda invoke \
  --function-name sp-ingest-stale-account-cleanup \
  --payload '{}' \
  --cli-binary-format raw-in-base64-out \
  /dev/stdout 2>/dev/null | python3 -m json.tool
```

This sets a DynamoDB TTL on the user's record so it is automatically purged
after the retention period.

**Verify TTL was set:**

```bash
aws dynamodb get-item \
  --table-name user-group-cache \
  --key '{"user_id": {"S": "USER_ID"}}' \
  --projection-expression "user_id, #s, expiry_ttl" \
  --expression-attribute-names '{"#s": "status"}' \
  --output json | python3 -m json.tool
```

The `expiry_ttl` field should contain a future Unix timestamp after which DynamoDB
will automatically delete the record.

## Step 6: Audit Recent User Activity

Review the user's recent query activity via CloudWatch Logs Insights to identify
any data they accessed before offboarding.

**Start an async query for the past 7 days:**

```bash
QUERY_ID=$(aws logs start-query \
  --log-group-name /aws/lambda/sp-ingest-query-handler \
  --start-time $(python3 -c "import time; print(int(time.time() - 7*86400))") \
  --end-time $(python3 -c "import time; print(int(time.time()))") \
  --query-string 'filter user_id = "USER_ID" | fields @timestamp, result_type, query_text_hash | sort @timestamp desc | limit 100' \
  --output text)

echo "Query started: $QUERY_ID"
```

**Wait a few seconds, then retrieve results:**

```bash
aws logs get-query-results --query-id "$QUERY_ID" --output json | python3 -m json.tool
```

If the query status is `Running`, wait a few more seconds and retry. Results show
the user's query history including timestamps and result types.

**For a broader audit across all pipeline Lambdas:**

```bash
# Check if the user triggered any Lambda invocations
for LOG_GROUP in \
  /aws/lambda/sp-ingest-daily-sync \
  /aws/lambda/sp-ingest-query-handler \
  /aws/lambda/sp-ingest-group-cache-refresh; do
  echo "--- $LOG_GROUP ---"
  aws logs filter-log-events \
    --log-group-name "$LOG_GROUP" \
    --start-time $(python3 -c "import time; print(int((time.time() - 7*86400) * 1000))") \
    --filter-pattern '"USER_ID"' \
    --max-items 10 \
    --query "events[].message" --output text
done
```

## Step 7: Document the Offboarding

Record the following for compliance:

- Date and time of offboarding
- User's UPN and Identity Center User ID
- Groups removed
- Audit query results (save the CloudWatch Logs Insights output)
- Who authorized the offboarding

## Verification Checklist

- [ ] User removed from Entra ID groups / account disabled / account deleted
- [ ] SCIM provisioning log shows successful deprovisioning
- [ ] Group cache refresh completed successfully
- [ ] User's `user-group-cache` record shows `deleted`/`disabled` status with empty groups
- [ ] Stale account cleanup ran and set TTL on the user's record
- [ ] Recent activity audit completed and results saved
- [ ] Offboarding documented for compliance

## Troubleshooting

| Symptom | Cause | Resolution |
|---------|-------|------------|
| User still shows `active` in group cache after 1 hour | SCIM deprovisioning not triggered | Verify the user was actually removed/disabled in Entra; check provisioning logs |
| Groups not cleared after cache refresh | Lambda did not process this user | Check Lambda CloudWatch logs for errors; verify Identity Center reflects the change |
| CloudWatch Logs Insights query returns no results | User never used the query handler, or log group does not exist | Verify the log group name; user may not have any query activity to audit |
| Stale account cleanup did not set TTL | User status not recognized as stale | Check Lambda logic; manually verify user's `status` field in DynamoDB |
| User can still authenticate after offboarding | Account not fully disabled in Entra | Verify account status in Entra; revoke active sessions in Entra -> Users -> Revoke sessions |

## Related Runbooks

- [Onboard New User](ONBOARD_NEW_USER.md) -- Reverse of this process
- [Entra ID SCIM Setup](../ENTRA_ID_SCIM_SETUP_RUNBOOK.md) -- SCIM provisioning configuration
