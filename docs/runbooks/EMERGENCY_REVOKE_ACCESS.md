# EMERGENCY: Revoke User Access

## Description

A user's access must be revoked immediately. This may be due to a terminated employee,
a compromised account, a security incident, or a compliance requirement. This runbook
prioritizes speed -- execute the **IMMEDIATE** steps first, then proceed with
verification and audit.

## Severity

**CRITICAL -- Time-sensitive. Begin Step 1 within minutes of notification.**

Do not wait for approvals before executing Steps 1-2. Document the authorization
after the fact if necessary.

## Prerequisites

- AWS CLI v2 configured with credentials that have access to DynamoDB, Lambda, and
  CloudWatch Logs.
- Permissions: `dynamodb:DeleteItem`, `dynamodb:GetItem`, `lambda:InvokeFunction`,
  `lambda:UpdateFunctionConfiguration`, `logs:StartQuery`, `logs:GetQueryResults`.
- The affected user's `user_id` (from the `user-group-cache` table or the incident report).
- Access to the Microsoft Entra admin center (for disabling the user in the identity
  provider).

---

## Steps

---

### Step 1: IMMEDIATE -- Delete the user's cached group entry

**Timeline: Execute within the first 1-2 minutes.**

This prevents the query handler from resolving any groups for the user, effectively
blocking all document access immediately.

```bash
aws dynamodb delete-item \
  --table-name user-group-cache \
  --key '{"user_id": {"S": "USER_ID_HERE"}}'
```

Verify the deletion:

```bash
aws dynamodb get-item \
  --table-name user-group-cache \
  --key '{"user_id": {"S": "USER_ID_HERE"}}' \
  --output text
```

Expected output: no item returned (empty response). If the item still exists, re-run
the delete command.

---

### Step 2: IMMEDIATE -- Revoke API key if compromised

**Timeline: Execute within the first 1-2 minutes if an API key is involved.**

If the user had a dedicated API key or if an API key has been compromised, remove it from
the authorizer Lambda's environment variables:

```bash
# First, retrieve the current API keys
aws lambda get-function-configuration \
  --function-name sp-ingest-api-authorizer \
  --query "Environment.Variables" \
  --output json
```

Note the current `API_KEYS` value. Remove the compromised key from the comma-separated
list, then update:

```bash
aws lambda update-function-configuration \
  --function-name sp-ingest-api-authorizer \
  --environment "Variables={API_KEYS=remaining_key_1,remaining_key_2,OTHER_ENV_VAR=value}"
```

**IMPORTANT:** The `--environment` flag replaces ALL environment variables on the
function. You must include every existing variable, not just `API_KEYS`. Retrieve the
full set from the first command above and only remove the compromised key.

If the API key is not relevant to this incident, skip to Step 3.

---

### Step 3: Disable the user in Entra ID

**Timeline: Execute within the first 5 minutes.**

1. Open the **Microsoft Entra admin center** (https://entra.microsoft.com).
2. Navigate to **Users > All users**.
3. Search for the user by name or UPN.
4. Open the user's profile and click **Edit properties**.
5. Set **Account enabled** to **No**.
6. Click **Save**.

This triggers SCIM deprovisioning, which will propagate to IAM Identity Center. The SCIM
incremental sync runs approximately every 40 minutes, but the user is already blocked at
the application layer by Step 1.

If you have the Microsoft Graph PowerShell module available, you can also disable via CLI:

```powershell
# PowerShell (if available)
Update-MgUser -UserId "user@dynamo.com" -AccountEnabled:$false
```

---

### Step 4: Trigger group-cache-refresh to propagate the revocation

**Timeline: Execute within 10 minutes.**

Even though the user's cache entry was deleted in Step 1, trigger a full cache refresh to
ensure consistency. This also updates any other users whose group memberships may have
changed as part of the incident response.

```bash
aws lambda invoke \
  --function-name sp-ingest-group-cache-refresh \
  --cli-binary-format raw-in-base64-out \
  --payload '{}' \
  /dev/stdout 2>/dev/null | python3 -m json.tool
```

Verify the user was NOT re-added to the cache (because they are now disabled in Entra):

```bash
aws dynamodb get-item \
  --table-name user-group-cache \
  --key '{"user_id": {"S": "USER_ID_HERE"}}' \
  --output text
```

If the user reappears in the cache (SCIM has not yet propagated the disable), delete the
entry again:

```bash
aws dynamodb delete-item \
  --table-name user-group-cache \
  --key '{"user_id": {"S": "USER_ID_HERE"}}'
```

---

### Step 5: Verify the user is blocked

Attempt a query as the user (or check audit logs) to confirm access is denied:

```bash
START_TIME=$(date -v-10M +%s 2>/dev/null || date -d '10 minutes ago' +%s)
END_TIME=$(date +%s)

QUERY_ID=$(aws logs start-query \
  --log-group-name /aws/lambda/sp-ingest-query-handler \
  --start-time "$START_TIME" \
  --end-time "$END_TIME" \
  --query-string 'filter user_id = "USER_ID_HERE"
    | fields @timestamp, result_type, resolved_groups, filters_applied
    | sort @timestamp desc
    | limit 10' \
  --query 'queryId' --output text)

# Wait a few seconds, then:
aws logs get-query-results --query-id "$QUERY_ID" --output table
```

Confirm that:
- No new `result_type = "success"` entries appear after the revocation time.
- If any queries were attempted, `resolved_groups` should be empty or the request
  should have been rejected.

---

### Step 6: Audit the user's recent query history

**Timeline: Execute within 1 hour.**

Pull the full history of the user's queries over the past 30 days to assess exposure:

```bash
START_TIME=$(date -v-30d +%s 2>/dev/null || date -d '30 days ago' +%s)
END_TIME=$(date +%s)

QUERY_ID=$(aws logs start-query \
  --log-group-name /aws/lambda/sp-ingest-query-handler \
  --start-time "$START_TIME" \
  --end-time "$END_TIME" \
  --query-string 'filter user_id = "USER_ID_HERE"
    | fields @timestamp, result_type, resolved_groups, filters_applied, query_text_hash, response_latency_ms
    | sort @timestamp desc
    | limit 1000' \
  --query 'queryId' --output text)

echo "Audit query ID: $QUERY_ID"
```

Wait for the query to complete, then retrieve results:

```bash
aws logs get-query-results --query-id "$QUERY_ID" --output json > /tmp/user-audit-$(date +%Y%m%d).json
echo "Audit results saved to /tmp/user-audit-$(date +%Y%m%d).json"
```

Review the output for:
- Total number of queries executed by the user.
- Any `result_type = "success"` entries -- these indicate documents the user successfully
  accessed.
- The `filters_applied` field to understand what document categories were queried.
- Any unusual patterns (bulk queries, queries outside business hours, queries for
  sensitive prefixes the user should not have accessed).

---

### Step 7: Generate an ad-hoc compliance report

**Timeline: Execute within 1 hour.**

Produce a compliance report that captures the current state of permissions and access
patterns:

```bash
aws lambda invoke \
  --function-name sp-ingest-compliance-report \
  --cli-binary-format raw-in-base64-out \
  --payload '{}' \
  /dev/stdout 2>/dev/null | python3 -m json.tool
```

The report is written to `s3://dynamo-ai-documents/governance-reports/`. Retrieve it:

```bash
# List recent governance reports
aws s3 ls s3://dynamo-ai-documents/governance-reports/ --recursive | sort | tail -5

# Download the latest report
LATEST=$(aws s3 ls s3://dynamo-ai-documents/governance-reports/ --recursive | sort | tail -1 | awk '{print $4}')
aws s3 cp "s3://dynamo-ai-documents/$LATEST" /tmp/compliance-report.json
```

---

### Step 8: Post-incident review

**Timeline: Execute within 24 hours.**

1. **Review the weekly drift report.** Check the most recent output of the
   `sp-ingest-permission-drift-detector` Lambda for any anomalies:

   ```bash
   aws logs tail /aws/lambda/sp-ingest-permission-drift-detector \
     --since 7d --format short | head -100
   ```

2. **Check for unauthorized document access.** Cross-reference the user's successful
   queries (from Step 6) against the permission mappings to determine if any access was
   beyond what the user's groups should have allowed.

3. **Verify SCIM deprovisioning completed.** Confirm the user no longer appears in IAM
   Identity Center:

   ```bash
   # List Identity Store users matching the revoked user
   aws identitystore list-users \
     --identity-store-id "YOUR_IDENTITY_STORE_ID" \
     --filters '[{"AttributePath": "UserName", "AttributeValue": "user@dynamo.com"}]' \
     --output table
   ```

4. **Check for stale-account-cleanup.** The `sp-ingest-stale-account-cleanup` Lambda
   runs daily and should have flagged or cleaned up this user. Verify:

   ```bash
   aws logs tail /aws/lambda/sp-ingest-stale-account-cleanup \
     --since 24h --format short | head -50
   ```

5. **Document the incident timeline.** Record:
   - When the revocation was requested.
   - When each step was executed (timestamps).
   - What documents the user accessed (from Step 6).
   - Whether any unauthorized access was detected.
   - What corrective actions were taken.

6. **Notify stakeholders.** Send the incident summary to:
   - The security team (via the `sp-ingest-governance-alerts` SNS topic or direct email).
   - The user's manager (if an employee termination).
   - Compliance/legal (if a data breach is suspected).

   ```bash
   aws sns publish \
     --topic-arn arn:aws:sns:us-east-1:ACCOUNT_ID:sp-ingest-governance-alerts \
     --subject "Access Revocation Incident Report - USER_ID" \
     --message "Access revoked for USER_ID at TIMESTAMP. See compliance report at s3://dynamo-ai-documents/governance-reports/REPORT_KEY. Audit log saved locally."
   ```

---

## Verification Checklist

- [ ] User's `user-group-cache` entry is deleted and does not reappear after cache refresh.
- [ ] API key revoked (if applicable).
- [ ] User disabled in Microsoft Entra ID.
- [ ] Group-cache-refresh Lambda invoked successfully.
- [ ] No new successful queries from the user appear in the audit log after revocation.
- [ ] 30-day query audit completed and saved.
- [ ] Compliance report generated.
- [ ] Incident timeline documented.
- [ ] Stakeholders notified.

---

## Troubleshooting

| Symptom | Likely Cause | Resolution |
|---------|-------------|------------|
| User's cache entry reappears after deletion | SCIM has not yet propagated the Entra disable; the cache refresh re-created the entry | Delete the cache entry again. Repeat after each cache refresh cycle until SCIM propagates (up to 40 minutes). |
| `lambda update-function-configuration` fails | Insufficient IAM permissions or malformed environment variable JSON | Verify your IAM role has `lambda:UpdateFunctionConfiguration`. Double-check that all existing env vars are included in the update. |
| CloudWatch Logs Insights query returns 0 results | Time window too narrow, or the user never queried the system | Expand the time window. Verify the log group name is `/aws/lambda/sp-ingest-query-handler`. |
| Compliance report Lambda returns an error | Missing permissions or configuration issue | Check CloudWatch logs at `/aws/lambda/sp-ingest-compliance-report` for the specific error. |
| User still appears in IAM Identity Center after Entra disable | SCIM deprovisioning can take up to 40 minutes | Wait for the next SCIM sync cycle. If the user persists after 2 hours, check the SCIM provisioning logs in the Entra admin center for errors. |
| Need to revoke access to a specific document, not a user | This runbook covers user-level revocation | To restrict a specific document, update the `doc-permission-mappings` table to remove the user's group from `allowed_groups`, or move the document to a more restrictive prefix. |

---

## Quick Reference: Emergency Commands

For rapid copy-paste during an incident, here are the critical commands in sequence:

```bash
# 1. Delete user cache (IMMEDIATE)
aws dynamodb delete-item --table-name user-group-cache --key '{"user_id": {"S": "USER_ID_HERE"}}'

# 2. Trigger cache refresh
aws lambda invoke --function-name sp-ingest-group-cache-refresh --cli-binary-format raw-in-base64-out --payload '{}' /dev/stdout

# 3. Verify deletion held
aws dynamodb get-item --table-name user-group-cache --key '{"user_id": {"S": "USER_ID_HERE"}}' --output text

# 4. Audit last 30 days
aws logs start-query --log-group-name /aws/lambda/sp-ingest-query-handler --start-time $(date -v-30d +%s 2>/dev/null || date -d '30 days ago' +%s) --end-time $(date +%s) --query-string 'filter user_id = "USER_ID_HERE" | fields @timestamp, result_type, resolved_groups, filters_applied' --query 'queryId' --output text

# 5. Generate compliance report
aws lambda invoke --function-name sp-ingest-compliance-report --cli-binary-format raw-in-base64-out --payload '{}' /dev/stdout
```
