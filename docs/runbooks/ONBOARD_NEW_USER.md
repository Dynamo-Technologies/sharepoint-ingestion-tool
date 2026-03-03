# Onboard New User Runbook

Onboard a new user to the SharePoint document ingestion pipeline. This adds the
user to the appropriate Entra ID security groups, provisions their identity to
AWS IAM Identity Center via SCIM, and verifies they can access documents scoped
to their groups.

## Prerequisites

- Microsoft Entra admin center access (User Administrator or higher)
- AWS CLI configured with permissions for DynamoDB, Lambda, and IAM Identity Center
- The user's email address / UPN (User Principal Name)
- Knowledge of which security groups the user should belong to (determines document access)

## Step 1: Add User to Entra ID Security Groups

1. Open **Microsoft Entra admin center** -> **Users** -> **All users**
2. Search for the user by name or email
3. Click on the user -> **Groups** -> **Add memberships**
4. Select the appropriate security groups for the user's role and department:
   - Department-specific groups (e.g., `SG - Engineering`, `SG - HR`)
   - Role-based groups (e.g., `Managers`, `Dynamo Leadership`)
   - Project-specific groups as needed
5. Click **Select**

Alternatively, add the user from the group side:

1. Navigate to **Groups** -> **All groups**
2. Select each target group -> **Members** -> **Add members**
3. Search for and select the user

**Note:** If the user's groups use Dynamic Membership rules based on attributes
(e.g., department), ensure the user's profile attributes are set correctly instead
of manually adding them.

## Step 2: Assign User to AWS SSO Enterprise Application

If the user is a member of a group that is already assigned to the AWS SSO enterprise
application, this step is automatic. Verify by checking the group assignments:

1. Navigate to **Enterprise applications** -> `Dynamo AI Platform - AWS SSO`
2. Go to **Users and groups**
3. Confirm the user's group(s) appear in the assignment list

If the user needs individual assignment (not recommended -- prefer group-based):

1. Click **Add user/group**
2. Select the user
3. Click **Assign**

## Step 3: Wait for SCIM Provisioning

SCIM incremental sync runs approximately every 40 minutes. The user and their
group memberships will be provisioned to IAM Identity Center automatically.

**Monitor provisioning status:**

1. In the enterprise application, go to **Provisioning** -> **Provisioning logs**
2. Filter by the user's display name or UPN
3. Confirm the provisioning action shows **Success**

## Step 4: Verify User in IAM Identity Center

Confirm the user was provisioned to IAM Identity Center:

```bash
aws identitystore list-users \
  --identity-store-id IDENTITY_STORE_ID \
  --filters AttributePath=UserName,AttributeValue=user@dynamotechnologies.com
```

Replace:
- `IDENTITY_STORE_ID` with your Identity Store ID (from `terraform output identity_store_id`)
- `user@dynamotechnologies.com` with the user's actual UPN

Expected output: a `Users` array containing the user with their `UserId`.

If the user does not appear, check:
- SCIM provisioning logs in Entra for errors
- That the user or their group is assigned to the enterprise application (Step 2)

## Step 5: Trigger Group Cache Refresh

Force the group-cache-refresh Lambda to populate the user's group membership
in the cache immediately:

```bash
aws lambda invoke \
  --function-name sp-ingest-group-cache-refresh \
  --payload '{}' \
  --cli-binary-format raw-in-base64-out \
  /dev/stdout 2>/dev/null | python3 -m json.tool
```

Verify the Lambda completed successfully (check for `"statusCode": 200` in the response).

## Step 6: Verify User Record in DynamoDB

Check the `user-group-cache` table to confirm the user's groups are correctly cached:

```bash
aws dynamodb get-item \
  --table-name user-group-cache \
  --key '{"user_id": {"S": "USER_ID"}}' \
  --output json | python3 -m json.tool
```

Replace `USER_ID` with the user's Identity Center User ID (from Step 4 output).

**Expected fields in the response:**

| Field | Expected Value |
|-------|---------------|
| `user_id` | The user's Identity Center User ID |
| `upn` | The user's UPN (email) |
| `groups` | List of group IDs the user belongs to |
| `status` | `active` |
| `last_synced` | Recent ISO 8601 timestamp |

If the `groups` list is empty or missing expected groups, verify the user's group
memberships in Entra ID and that those groups are assigned to the enterprise application.

## Step 7: Validate Document Access

Confirm the user can authenticate and that their queries are properly scoped
to documents matching their group permissions.

**Check which S3 prefixes the user's groups can access:**

```bash
# Get the user's groups from the cache
USER_GROUPS=$(aws dynamodb get-item \
  --table-name user-group-cache \
  --key '{"user_id": {"S": "USER_ID"}}' \
  --query "Item.groups.L[].S" --output text)

echo "User groups: $USER_GROUPS"

# Scan permission mappings to see which prefixes these groups unlock
aws dynamodb scan \
  --table-name doc-permission-mappings \
  --projection-expression "s3_prefix, allowed_groups, sensitivity_level" \
  --output table
```

Cross-reference the user's groups with the `allowed_groups` in each mapping to
confirm the user has access to the expected document prefixes.

## Verification Checklist

- [ ] User exists in Entra ID with correct attributes (name, email, department)
- [ ] User is a member of the appropriate security groups
- [ ] User's groups are assigned to the AWS SSO enterprise application
- [ ] User appears in IAM Identity Center (`identitystore list-users`)
- [ ] Group cache refresh completed successfully
- [ ] User record in `user-group-cache` shows correct groups and `active` status
- [ ] User's groups align with the expected `doc-permission-mappings` entries

## Troubleshooting

| Symptom | Cause | Resolution |
|---------|-------|------------|
| User not in IAM Identity Center after 1 hour | User/group not assigned to enterprise app | Verify group assignment in Entra enterprise app (Step 2) |
| User in Identity Center but `user-group-cache` is empty | Group cache not refreshed | Re-run `sp-ingest-group-cache-refresh`; check Lambda logs for errors |
| User's groups list is incomplete | Some groups not assigned to enterprise app | Add missing groups to the enterprise app's user/group assignments |
| SCIM provisioning shows errors | Attribute mapping conflict or token expiry | Check Entra provisioning logs; see `docs/ENTRA_ID_SCIM_SETUP_RUNBOOK.md` for token rotation |
| User can authenticate but cannot see expected documents | Permission mapping mismatch | Verify the user's cached groups match `allowed_groups` in `doc-permission-mappings` |
| User record shows `status: deleted` | User was previously offboarded | Re-add user to groups; run group-cache-refresh to update status |

## Related Runbooks

- [Entra ID SCIM Setup](../ENTRA_ID_SCIM_SETUP_RUNBOOK.md) -- Initial SCIM provisioning configuration
- [Add New Department](ADD_NEW_DEPARTMENT.md) -- Set up a new department before onboarding users to it
- [Offboard User](OFFBOARD_USER.md) -- Reverse this process when a user leaves
