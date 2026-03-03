# Entra ID SCIM Provisioning Setup Runbook

Manual steps to configure SCIM provisioning from Microsoft Entra ID
to AWS IAM Identity Center. This is a one-time setup per environment.

## Prerequisites

- AWS IAM Identity Center enabled in the target AWS account
- Microsoft Entra ID (Azure AD) Global Administrator or Application Administrator role
- Identity Store ID from Terraform output: `terraform output identity_store_id`

## Step 1: Enable IAM Identity Center (AWS Console)

1. Open **AWS Console → IAM Identity Center**
2. Click **Enable** (if not already enabled)
3. Choose **Identity source: External identity provider**
4. Note the **SCIM endpoint URL** and generate a **SCIM API token**
   - Save these values securely — the token is shown only once

## Step 2: Configure Entra ID Enterprise Application

1. Open **Microsoft Entra admin center → Enterprise applications**
2. Click **New application → AWS IAM Identity Center**
3. Name: `Dynamo AI Platform - AWS SSO`
4. Click **Create**

## Step 3: Configure Provisioning

1. In the enterprise application, go to **Provisioning → Get started**
2. Set **Provisioning Mode: Automatic**
3. Under **Admin Credentials**:
   - **Tenant URL**: Paste the SCIM endpoint URL from Step 1
   - **Secret Token**: Paste the SCIM API token from Step 1
4. Click **Test Connection** — verify success
5. Click **Save**

## Step 4: Configure Attribute Mappings

Under **Provisioning → Mappings**:

### User Mappings (Provision Azure Active Directory Users)

| Entra Attribute | IAM Identity Center Attribute |
|----------------|-------------------------------|
| userPrincipalName | userName |
| displayName | displayName |
| givenName | name.givenName |
| surname | name.familyName |
| mail | emails[type eq "work"].value |
| department | urn:ietf:params:scim:schemas:extension:enterprise:2.0:User:department |

### Group Mappings (Provision Azure Active Directory Groups)

| Entra Attribute | IAM Identity Center Attribute |
|----------------|-------------------------------|
| displayName | displayName |
| members | members |

## Step 5: Assign Users and Groups

1. Go to **Enterprise application → Users and groups**
2. Click **Add user/group**
3. Assign the security groups that should be provisioned:
   - All groups referenced in `doc-permission-mappings` DynamoDB table
   - Parent groups (for nested group resolution)

## Step 6: Start Provisioning

1. Go to **Provisioning → Overview**
2. Click **Start provisioning**
3. Initial sync takes 20-40 minutes for all users/groups
4. Subsequent incremental syncs run every ~40 minutes

## Step 7: Verify

1. Check **Provisioning logs** in Entra admin center for errors
2. In AWS Console, go to **IAM Identity Center → Users** — verify users appear
3. Run the group-cache-refresh Lambda manually:
   ```bash
   aws lambda invoke --function-name sp-ingest-group-cache-refresh /dev/stdout
   ```
4. Check the user-group-cache DynamoDB table for populated records

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Test Connection fails | Wrong SCIM URL or expired token | Regenerate token in IAM Identity Center |
| Users sync but groups don't | Groups not assigned in Step 5 | Assign groups to the enterprise app |
| Nested groups not resolved | Expected — SCIM provisions flat | The group-cache-refresh Lambda handles nesting |
| Provisioning stuck on initial | Large directory (>1000 users) | Wait — initial sync can take hours |

## Maintenance

- **SCIM token rotation**: Tokens expire after 1 year. Regenerate in IAM Identity Center and update in Entra provisioning config.
- **Adding new groups**: Assign them in the Entra enterprise app (Step 5) AND add to `doc-permission-mappings` DynamoDB table.
- **Monitoring**: Check the weekly drift report in `s3://BUCKET/governance-reports/` for unmapped prefixes.
