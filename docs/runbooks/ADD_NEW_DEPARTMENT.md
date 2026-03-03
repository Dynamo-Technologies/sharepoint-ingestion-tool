# Add New Department Runbook

Add a new department to the SharePoint document ingestion pipeline. This creates
the Entra ID security group, provisions it to AWS IAM Identity Center via SCIM,
sets up the S3 prefix structure, and configures DynamoDB permission mappings.

## Prerequisites

- Microsoft Entra admin center access (Groups Administrator or higher)
- AWS CLI configured with permissions for DynamoDB, S3, and Lambda
- The department name and its designated sensitivity level (`public`, `internal`, or `confidential`)
- Knowledge of which Entra ID security group(s) should have access

## Step 1: Create Security Group in Entra ID

1. Open **Microsoft Entra admin center** -> **Groups** -> **All groups**
2. Click **New group**
3. Configure:
   - **Group type**: Security
   - **Group name**: `SG - {DepartmentName}` (e.g., `SG - NewDepartment`)
   - **Group description**: Describe the department and its document access scope
   - **Membership type**: Assigned (or Dynamic if using attribute-based rules)
4. Click **Create**
5. Add the appropriate users as members of the new group
6. Note the **Object ID** of the created group -- you will need it for the DynamoDB mapping

## Step 2: Assign Group to AWS SSO Enterprise Application

1. In Entra admin center, navigate to **Enterprise applications**
2. Select the AWS SSO app (e.g., `Dynamo AI Platform - AWS SSO`)
3. Go to **Users and groups** -> **Add user/group**
4. Select the newly created security group
5. Click **Assign**

This ensures SCIM provisioning will sync the group and its members to IAM Identity Center.

## Step 3: Wait for SCIM Provisioning

SCIM incremental sync runs approximately every 40 minutes. You can either wait
or check provisioning status.

**Check provisioning status in Entra:**

1. In the enterprise application, go to **Provisioning** -> **Provisioning logs**
2. Filter by the group name to confirm it was provisioned successfully

**Verify group appears in IAM Identity Center:**

```bash
# List groups in IAM Identity Center matching the new group name
aws identitystore list-groups \
  --identity-store-id IDENTITY_STORE_ID \
  --filters AttributePath=DisplayName,AttributeValue="SG - NewDepartment"
```

Replace `IDENTITY_STORE_ID` with your Identity Store ID (from `terraform output identity_store_id`).

## Step 4: Create S3 Prefix Structure

Create the S3 prefix for the new department under the appropriate organization:

```bash
# Create the department prefix (S3 uses "virtual" folders via zero-byte objects)
aws s3api put-object \
  --bucket dynamo-ai-documents \
  --key "source/Dynamo/NewDepartment/"
```

If the department has known sub-folders, create those as well:

```bash
aws s3api put-object --bucket dynamo-ai-documents --key "source/Dynamo/NewDepartment/Policies/"
aws s3api put-object --bucket dynamo-ai-documents --key "source/Dynamo/NewDepartment/Reports/"
```

## Step 5: Add Permission Mapping to DynamoDB

Insert a record into the `doc-permission-mappings` table that maps the S3 prefix
to the allowed Entra ID group(s) and sensitivity level:

```bash
aws dynamodb put-item \
  --table-name doc-permission-mappings \
  --item '{
    "s3_prefix": {"S": "source/Dynamo/NewDepartment/"},
    "allowed_groups": {"L": [{"S": "ENTRA_GROUP_OBJECT_ID"}]},
    "sensitivity_level": {"S": "internal"},
    "description": {"S": "NewDepartment documents"}
  }'
```

Replace:
- `NewDepartment` with the actual department name (matching the S3 prefix)
- `ENTRA_GROUP_OBJECT_ID` with the Object ID from Step 1
- `internal` with the appropriate sensitivity level (`public`, `internal`, or `confidential`)

**To add multiple groups** (e.g., the department group plus a leadership group):

```bash
aws dynamodb put-item \
  --table-name doc-permission-mappings \
  --item '{
    "s3_prefix": {"S": "source/Dynamo/NewDepartment/"},
    "allowed_groups": {"L": [
      {"S": "DEPT_GROUP_OBJECT_ID"},
      {"S": "LEADERSHIP_GROUP_OBJECT_ID"}
    ]},
    "sensitivity_level": {"S": "internal"},
    "description": {"S": "NewDepartment documents - accessible by department and leadership"}
  }'
```

## Step 6: Trigger Group Cache Refresh

Force the group-cache-refresh Lambda to pick up the new group membership immediately
rather than waiting for the next scheduled run (every 15 minutes):

```bash
aws lambda invoke \
  --function-name sp-ingest-group-cache-refresh \
  --payload '{}' \
  --cli-binary-format raw-in-base64-out \
  /dev/stdout 2>/dev/null | python3 -m json.tool
```

Verify the Lambda completed successfully (check for `"statusCode": 200` in the response).

## Step 7: Run Permission Drift Detector

Run the drift detector to confirm there are no unmapped S3 prefixes and the new
mapping is correctly registered:

```bash
aws lambda invoke \
  --function-name sp-ingest-permission-drift-detector \
  --payload '{}' \
  --cli-binary-format raw-in-base64-out \
  /dev/stdout 2>/dev/null | python3 -m json.tool
```

The response should show no unmapped prefixes. If the new prefix appears as unmapped,
re-check the `s3_prefix` value in Step 5 -- it must exactly match the S3 prefix
(including trailing slash).

## Step 8: Validate End-to-End Access

Verify that a user in the new department group can be resolved correctly.

**Check user record in the group cache:**

```bash
aws dynamodb get-item \
  --table-name user-group-cache \
  --key '{"user_id": {"S": "USER_ID"}}' \
  --output json | python3 -m json.tool
```

Replace `USER_ID` with the user ID of a member of the new department group.

Confirm the response includes the new group in the user's `groups` list.

**Upload a test document and verify ingestion:**

```bash
# Upload a test file
aws s3 cp test-document.pdf s3://dynamo-ai-documents/source/Dynamo/NewDepartment/test-document.pdf

# Wait 30 seconds for the pipeline to process
sleep 30

# Check the document registry for the new document
aws dynamodb scan \
  --table-name sp-ingest-document-registry \
  --filter-expression "begins_with(s3_source_key, :prefix)" \
  --expression-attribute-values '{":prefix": {"S": "source/Dynamo/NewDepartment/"}}' \
  --projection-expression "s3_source_key, textract_status" \
  --output table
```

## Verification Checklist

- [ ] Security group exists in Entra ID with correct members
- [ ] Group is assigned to the AWS SSO enterprise application
- [ ] Group appears in IAM Identity Center
- [ ] S3 prefix `source/Dynamo/{Department}/` exists
- [ ] DynamoDB `doc-permission-mappings` entry is correct
- [ ] Group cache refresh completed successfully
- [ ] Drift detector shows no unmapped prefixes
- [ ] Test user's group cache includes the new group
- [ ] Test document ingested and processed successfully

## Troubleshooting

| Symptom | Cause | Resolution |
|---------|-------|------------|
| Group not appearing in IAM Identity Center after 1 hour | Group not assigned to enterprise app | Verify Step 2 -- group must be assigned to the AWS SSO enterprise app in Entra |
| Drift detector reports unmapped prefix | `s3_prefix` mismatch | Ensure the DynamoDB `s3_prefix` value exactly matches the S3 prefix, including trailing `/` |
| User's group cache missing new group | Cache not refreshed | Re-run `sp-ingest-group-cache-refresh` Lambda; verify user is a member of the group in Entra |
| SCIM provisioning errors in Entra logs | Token expired or attribute mapping issue | Check provisioning logs in Entra; regenerate SCIM token if expired (see `docs/ENTRA_ID_SCIM_SETUP_RUNBOOK.md`) |
| Test document not appearing in registry | S3 event notification not configured for new prefix | S3 event notifications trigger on `source/` prefix -- all sub-prefixes are covered automatically |

## Related Runbooks

- [Entra ID SCIM Setup](../ENTRA_ID_SCIM_SETUP_RUNBOOK.md) -- Initial SCIM provisioning configuration
- [Onboard New User](ONBOARD_NEW_USER.md) -- Add individual users to the new department
- [Add New Document Library](ADD_NEW_DOCUMENT_LIBRARY.md) -- Add a SharePoint document library
