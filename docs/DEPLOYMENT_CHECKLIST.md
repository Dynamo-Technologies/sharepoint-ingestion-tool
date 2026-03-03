# Deployment Checklist

Ordered steps to deploy the SharePoint Document Ingestion Pipeline from scratch to production.

---

## Step 1: Prerequisites

Verify the following before starting deployment.

- [ ] AWS account with IAM admin access
- [ ] Microsoft Entra ID (Azure AD) with Global Administrator or Application Administrator role
- [ ] Terraform >= 1.5 installed
  ```bash
  terraform version
  # Expected: Terraform v1.5.x or higher
  ```
- [ ] Python 3.11 installed
  ```bash
  python3.11 --version
  # Expected: Python 3.11.x
  ```
- [ ] AWS CLI installed and configured with valid credentials
  ```bash
  aws sts get-caller-identity
  # Expected: JSON with Account, UserId, Arn
  ```
- [ ] Azure app registration created in Entra ID for SharePoint Graph API access. Record the following values:
  - Application (client) ID
  - Directory (tenant) ID
  - Client secret (generate under Certificates & secrets)
- [ ] Azure app registration has the following Microsoft Graph API **application** permissions granted (with admin consent):
  - `Sites.Read.All`
  - `Files.Read.All`

---

## Step 2: Configure Environment File

Create the `.env` file from the provided example. The deploy script reads Azure credentials from this file.

```bash
cd /path/to/dynamo-sharepoint-ingest
cp .env.example .env
```

Edit `.env` and populate the Azure AD credentials and site configuration:

```
AZURE_CLIENT_ID=<your-application-client-id>
AZURE_TENANT_ID=<your-directory-tenant-id>
AZURE_CLIENT_SECRET=<your-client-secret-value>
SHAREPOINT_SITE_NAME=Dynamo
EXCLUDED_FOLDERS=Drafts,drafts
S3_BUCKET=dynamo-ai-documents
```

**Verification:**
- [ ] `.env` file exists at project root
- [ ] `AZURE_CLIENT_ID` is set (not `PLACEHOLDER` or empty)
- [ ] `AZURE_TENANT_ID` is set
- [ ] `AZURE_CLIENT_SECRET` is set
- [ ] `SHAREPOINT_SITE_NAME` matches the target SharePoint site

---

## Step 3: Configure Terraform Variables

Edit the Terraform variables for your environment.

```bash
cd terraform
```

Edit `variables.tf` defaults or create a `terraform.tfvars` file:

```hcl
aws_region                = "us-east-1"
s3_bucket_name            = "dynamo-ai-documents"
sharepoint_site_name      = "Dynamo"
excluded_folders          = "Drafts,drafts"
alert_email               = "ops-team@example.com"
governance_alerts_email   = "governance@example.com"
environment               = "prod"
```

Key variables to review:

| Variable | Description | Default |
|----------|-------------|---------|
| `aws_region` | AWS region for all resources | `us-east-1` |
| `s3_bucket_name` | S3 bucket for document storage | `dynamo-ai-documents` |
| `sharepoint_site_name` | SharePoint site to crawl | (empty) |
| `excluded_folders` | Comma-separated folders to skip | (empty) |
| `alert_email` | Email for SNS pipeline alerts | (empty) |
| `governance_alerts_email` | Email for governance drift alerts | (empty) |

**Verification:**
- [ ] `terraform.tfvars` file created (or defaults in `variables.tf` are acceptable)
- [ ] `alert_email` is set to a valid email address

---

## Step 4: Configure Terraform Backend

The project uses an S3 backend for state storage. Edit `terraform/main.tf` if you need a different state bucket:

```hcl
backend "s3" {
  bucket = "dynamo-terraform-state-760560299079"
  key    = "sharepoint-ingest/terraform.tfstate"
  region = "us-east-1"
}
```

- [ ] S3 state bucket exists and is accessible from your AWS account
- [ ] Backend configuration matches your environment

---

## Step 5: Build Lambda Artifacts

Build the Lambda layer (shared Python dependencies) and code package.

```bash
./scripts/build-lambda.sh
```

**Expected output:**
```
[1/5] Cleaning previous builds...
[2/5] Installing layer dependencies...
[3/5] Packaging Lambda layer...
  Layer archive: ~15M
[4/5] Packaging Lambda function code...
  Code archive: ~120K
[5/5] Cleaning up build directory...

Build complete!
  dist/lambda-layer.zip  (15M)
  dist/lambda-code.zip   (120K)
```

**Verification:**
- [ ] `dist/lambda-layer.zip` exists and is non-empty
- [ ] `dist/lambda-code.zip` exists and is non-empty

---

## Step 6: Deploy Infrastructure with Terraform

Initialize and apply the Terraform configuration.

**Option A: Use the deploy script (recommended)**

The deploy script handles build, Terraform, secrets, and verification in one step:

```bash
./scripts/deploy.sh
```

Or run with `--plan-only` to review before applying:

```bash
./scripts/deploy.sh --plan-only
# Review the plan, then:
cd terraform && terraform apply plan.out
```

**Option B: Manual Terraform commands**

```bash
cd terraform
terraform init
terraform plan -out=plan.out
# Review the plan output carefully
terraform apply plan.out
```

**Resources created by Terraform:**

| Resource | Name |
|----------|------|
| S3 bucket | `dynamo-ai-documents` |
| DynamoDB table | `sp-ingest-delta-tokens` |
| DynamoDB table | `sp-ingest-document-registry` |
| DynamoDB table | `doc-permission-mappings` |
| DynamoDB table | `user-group-cache` |
| Lambda function | `sp-ingest-daily-sync` (512 MB, 15 min timeout) |
| Lambda function | `sp-ingest-textract-trigger` (1 GB, 5 min timeout) |
| Lambda function | `sp-ingest-textract-complete` (1 GB, 5 min timeout) |
| Lambda function | `sp-ingest-group-cache-refresh` (256 MB, 5 min timeout) |
| Lambda function | `sp-ingest-permission-drift-detector` (256 MB, 5 min timeout) |
| Lambda function | `sp-ingest-stale-account-cleanup` (256 MB, 5 min timeout) |
| Lambda function | `sp-ingest-compliance-report` (256 MB, 5 min timeout) |
| Lambda layer | `sp-ingest-shared-deps` (msal, requests, python-pptx, openpyxl) |
| EventBridge rule | `sp-ingest-daily-sync-schedule` (daily at 7:00 AM UTC) |
| EventBridge rule | `sp-ingest-group-cache-refresh` (every 15 minutes) |
| EventBridge rule | `sp-ingest-permission-drift-detector` (weekly, Sunday 2:00 AM UTC) |
| EventBridge rule | `sp-ingest-stale-account-cleanup` (daily at 3:00 AM UTC) |
| EventBridge rule | `sp-ingest-compliance-report` (monthly, 1st at 6:00 AM UTC) |
| SNS topic | `sp-ingest-alerts` |
| SNS topic | `sp-ingest-textract-notifications` |
| SNS topic | `sp-ingest-quarantine-alerts` |
| SNS topic | `sp-ingest-governance-alerts` |
| Secrets Manager | `sp-ingest/azure-client-id` |
| Secrets Manager | `sp-ingest/azure-tenant-id` |
| Secrets Manager | `sp-ingest/azure-client-secret` |
| CloudWatch dashboard | `SP-Ingest-Pipeline` |
| SQS DLQ | `sp-ingest-group-cache-refresh-dlq` |
| SQS DLQ | `sp-ingest-permission-drift-detector-dlq` |
| SQS DLQ | `sp-ingest-stale-account-cleanup-dlq` |
| SQS DLQ | `sp-ingest-compliance-report-dlq` |

**Verification:**
- [ ] `terraform apply` completed without errors
- [ ] All Lambda functions show state `Active`:
  ```bash
  aws lambda get-function --function-name sp-ingest-daily-sync --query "Configuration.State" --output text
  # Expected: Active
  ```
- [ ] S3 bucket exists:
  ```bash
  aws s3api head-bucket --bucket dynamo-ai-documents
  # Expected: no output (success)
  ```
- [ ] DynamoDB tables are active:
  ```bash
  aws dynamodb describe-table --table-name sp-ingest-delta-tokens --query "Table.TableStatus" --output text
  aws dynamodb describe-table --table-name sp-ingest-document-registry --query "Table.TableStatus" --output text
  aws dynamodb describe-table --table-name doc-permission-mappings --query "Table.TableStatus" --output text
  aws dynamodb describe-table --table-name user-group-cache --query "Table.TableStatus" --output text
  # Expected: ACTIVE (for each)
  ```

---

## Step 7: Populate Secrets Manager

If you used `./scripts/deploy.sh`, secrets are populated automatically from `.env`. Otherwise, populate them manually:

```bash
aws secretsmanager put-secret-value \
    --secret-id "sp-ingest/azure-client-id" \
    --secret-string "<your-client-id>"

aws secretsmanager put-secret-value \
    --secret-id "sp-ingest/azure-tenant-id" \
    --secret-string "<your-tenant-id>"

aws secretsmanager put-secret-value \
    --secret-id "sp-ingest/azure-client-secret" \
    --secret-string "<your-client-secret>"
```

**Verification:**
- [ ] All three secrets contain real values (not `PLACEHOLDER`):
  ```bash
  aws secretsmanager get-secret-value --secret-id "sp-ingest/azure-client-id" --query "SecretString" --output text
  aws secretsmanager get-secret-value --secret-id "sp-ingest/azure-tenant-id" --query "SecretString" --output text
  # Expected: actual credential values (not PLACEHOLDER)
  ```

---

## Step 8: Confirm SNS Email Subscriptions

If you specified `alert_email` and/or `governance_alerts_email`, AWS will send confirmation emails to those addresses.

- [ ] Check the `alert_email` inbox for an AWS SNS confirmation email
- [ ] Click the "Confirm subscription" link in the email
- [ ] If `governance_alerts_email` was set, confirm that subscription as well
- [ ] Verify subscriptions are confirmed:
  ```bash
  aws sns list-subscriptions-by-topic \
      --topic-arn "$(cd terraform && terraform output -raw alerts_sns_topic_arn)" \
      --query "Subscriptions[].{Endpoint:Endpoint,Status:SubscriptionArn}"
  # Expected: SubscriptionArn should NOT be "PendingConfirmation"
  ```

---

## Step 9: Seed Permission Mappings

Populate the `doc-permission-mappings` DynamoDB table with S3 prefix to Entra group ID mappings.

First, generate the permission mappings configuration:

```bash
python scripts/generate_permission_mappings.py
```

Then seed the DynamoDB table:

```bash
# Dry run first to review
python scripts/seed_permission_mappings.py --dry-run

# Seed for real
python scripts/seed_permission_mappings.py
```

**Verification:**
- [ ] Permission mappings are populated:
  ```bash
  aws dynamodb scan --table-name doc-permission-mappings --select COUNT
  # Expected: Count > 0
  ```

---

## Step 10: SCIM Configuration (Entra ID to AWS IAM Identity Center)

This step provisions users and groups from Microsoft Entra ID into AWS IAM Identity Center via SCIM. Follow the detailed runbook at:

> **[docs/ENTRA_ID_SCIM_SETUP_RUNBOOK.md](ENTRA_ID_SCIM_SETUP_RUNBOOK.md)**

Summary of steps:

1. **Enable IAM Identity Center** in AWS Console (if not already enabled)
2. Choose **External identity provider** as the identity source
3. Note the **SCIM endpoint URL** and generate a **SCIM API token**
4. In **Microsoft Entra admin center**, create an enterprise application for **AWS IAM Identity Center**
5. Configure **automatic provisioning** with the SCIM endpoint and token
6. Configure **attribute mappings** for users and groups
7. **Assign users and groups** that correspond to the permission mappings
8. **Start provisioning** -- initial sync takes 20-40 minutes

**Verification:**
- [ ] IAM Identity Center shows provisioned users (AWS Console > IAM Identity Center > Users)
- [ ] IAM Identity Center shows provisioned groups (AWS Console > IAM Identity Center > Groups)
- [ ] Entra provisioning logs show no errors

---

## Step 11: Verify SCIM Sync

Trigger the group-cache-refresh Lambda to populate the user-group-cache table from IAM Identity Center.

```bash
aws lambda invoke \
    --function-name sp-ingest-group-cache-refresh \
    --cli-binary-format raw-in-base64-out \
    --payload '{}' \
    /dev/stdout
```

**Expected output:** JSON with cache refresh statistics (users cached, groups resolved).

**Verification:**
- [ ] Lambda invocation returns HTTP 200 with no `FunctionError`
- [ ] `user-group-cache` table has been populated:
  ```bash
  aws dynamodb scan --table-name user-group-cache --select COUNT
  # Expected: Count > 0 (matches number of provisioned users)
  ```
- [ ] EventBridge rule is enabled (auto-refreshes every 15 minutes):
  ```bash
  aws events describe-rule --name sp-ingest-group-cache-refresh --query "State" --output text
  # Expected: ENABLED
  ```

---

## Step 12: Initial Data Load

Choose one of the two approaches for the initial SharePoint document ingestion.

### Option A: Daily Sync (incremental, slow for large sites)

Manually trigger the daily sync Lambda:

```bash
aws lambda invoke \
    --function-name sp-ingest-daily-sync \
    --cli-binary-format raw-in-base64-out \
    --payload '{}' \
    /dev/stdout
```

This performs a delta crawl via the Microsoft Graph API. The first run will download all documents since there is no prior delta token. Subsequent runs are incremental.

- Timeout: 15 minutes (may not complete for large document libraries)
- Best for: Small sites with fewer than ~1,000 documents

### Option B: Bulk Ingestion via EC2 (recommended for initial load)

Launch a temporary EC2 instance (t3.xlarge, 100 GB gp3) for bulk ingestion:

```bash
./scripts/run-bulk-ingest.sh launch --key-pair <your-key-pair-name> --admin-cidr <your-ip>/32
```

This creates a temporary VPC, security group, and EC2 instance. The instance automatically downloads the application code from S3 and begins ingesting all documents.

**Monitor progress:**

```bash
# Check instance status and completion marker
./scripts/run-bulk-ingest.sh status

# View ingestion logs from S3
./scripts/run-bulk-ingest.sh logs

# Or SSH directly (if key pair was provided)
ssh -i ~/.ssh/<key-pair>.pem ec2-user@<instance-ip>
sudo tail -f /var/log/bulk-ingest.log
```

**After ingestion completes, tear down the EC2 instance:**

```bash
./scripts/run-bulk-ingest.sh teardown
```

**Verification:**
- [ ] Documents appear in S3 under the `source/` prefix:
  ```bash
  aws s3 ls s3://dynamo-ai-documents/source/ --summarize --recursive | tail -3
  # Expected: Total Objects > 0
  ```
- [ ] Document registry has entries:
  ```bash
  aws dynamodb scan --table-name sp-ingest-document-registry --select COUNT
  # Expected: Count > 0
  ```
- [ ] Extracted text twins appear in S3:
  ```bash
  aws s3 ls s3://dynamo-ai-documents/extracted/ --summarize --recursive | tail -3
  # Expected: Total Objects > 0
  ```

---

## Step 13: Validate Deployment

Run the comprehensive validation script to check all infrastructure, connectivity, and the end-to-end pipeline:

```bash
./scripts/validate-deployment.sh
```

This script checks:
- S3 bucket existence and encryption
- S3 event notifications
- DynamoDB table status, keys, and GSIs
- All Lambda functions (state, IAM role, environment variables, layers)
- EventBridge rules
- SNS topics and subscriptions
- Secrets Manager values
- Lambda dry-run invocation
- End-to-end PDF test (uploads a test PDF, waits for Textract processing, verifies JSON twin)
- Bulk load readiness

**Expected output:** All checks pass with status `READY FOR BULK INGESTION` (or `READY` if bulk was already completed).

To skip the end-to-end test (faster):

```bash
./scripts/validate-deployment.sh --skip-e2e
```

To check infrastructure only (no Lambda invocations):

```bash
./scripts/validate-deployment.sh --infra-only
```

---

## Step 14: Bedrock Knowledge Base Setup

Create a Bedrock Knowledge Base to enable RAG queries over the extracted documents.

1. Open **AWS Console > Amazon Bedrock > Knowledge bases**
2. Click **Create knowledge base**
3. Configure:
   - **Name**: `sp-ingest-knowledge-base`
   - **Data source type**: Amazon S3
   - **S3 URI**: `s3://dynamo-ai-documents/extracted/`
   - **Chunking strategy**: Default (or customize as needed)
   - **Embedding model**: Select an available model (e.g., Titan Embeddings)
4. Create and sync the knowledge base
5. Note the **Knowledge Base ID** from the console

Update Terraform variables with the Knowledge Base ID:

```hcl
knowledge_base_id = "XXXXXXXXXX"
```

- [ ] Knowledge Base created and first sync completed
- [ ] Knowledge Base ID recorded for use in Step 15

---

## Step 15: WebUI Deployment (Optional)

Deploy Open WebUI on ECS Fargate with an API Gateway for RAG queries. This requires a Bedrock Knowledge Base (Step 14).

Prerequisites:
- [ ] Knowledge Base ID from Step 14
- [ ] Open WebUI Docker image pushed to ECR
- [ ] API keys generated for query authentication

```bash
cd terraform
terraform apply \
    -var="enable_webui=true" \
    -var="knowledge_base_id=<KB_ID>" \
    -var="open_webui_image=<ECR_IMAGE_URI>" \
    -var="api_keys=<KEY1,KEY2>" \
    -var='api_key_user_map={"KEY1":"user1@example.com","KEY2":"user2@example.com"}'
```

**Additional resources created:**

| Resource | Name |
|----------|------|
| Lambda function | `sp-ingest-query-handler` |
| Lambda function | `sp-ingest-api-authorizer` |
| ECS cluster | `sp-ingest-webui` |
| ECS service | `sp-ingest-webui` (Fargate) |
| ALB | `sp-ingest-webui-alb` |
| API Gateway | Query API endpoint |
| Bedrock Guardrail | RAG guardrail |

**Verification:**
- [ ] ECS service is running:
  ```bash
  aws ecs describe-services --cluster sp-ingest-webui --services sp-ingest-webui \
      --query "services[0].runningCount" --output text
  # Expected: 1
  ```
- [ ] ALB is accessible:
  ```bash
  cd terraform && terraform output alb_dns_name
  # Test with: curl -s http://<alb-dns>/health
  ```
- [ ] API Gateway responds to queries:
  ```bash
  cd terraform && terraform output api_gateway_url
  ```

---

## Step 16: Monitoring Verification

Verify all monitoring components are operational.

### CloudWatch Dashboard

- [ ] Open the dashboard in the AWS Console:
  ```bash
  cd terraform && terraform output dashboard_url
  ```
  Or navigate to: **CloudWatch > Dashboards > SP-Ingest-Pipeline**

### CloudWatch Alarms

- [ ] Verify no alarms are in `ALARM` state:
  ```bash
  aws cloudwatch describe-alarms \
      --alarm-name-prefix "sp-ingest" \
      --state-value ALARM \
      --query "MetricAlarms[].AlarmName"
  # Expected: [] (empty list)
  ```

### CloudWatch Log Groups

- [ ] Verify log groups exist:
  ```bash
  aws logs describe-log-groups --log-group-name-prefix "/sp-ingest" \
      --query "logGroups[].logGroupName" --output table
  aws logs describe-log-groups --log-group-name-prefix "/aws/lambda/sp-ingest" \
      --query "logGroups[].logGroupName" --output table
  ```

### Custom Metrics

- [ ] Trigger a group-cache-refresh and verify the `SCIMSyncSuccess` metric appears:
  ```bash
  aws lambda invoke --function-name sp-ingest-group-cache-refresh \
      --cli-binary-format raw-in-base64-out --payload '{}' /dev/stdout

  # Wait 1-2 minutes, then check:
  aws cloudwatch get-metric-statistics \
      --namespace SP-Ingest \
      --metric-name SCIMSyncSuccess \
      --start-time "$(date -u -v-1H +%Y-%m-%dT%H:%M:%SZ)" \
      --end-time "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
      --period 300 \
      --statistics Sum
  # Expected: Datapoints with Sum >= 1
  ```

### Scheduled EventBridge Rules

- [ ] Verify all schedules are enabled:
  ```bash
  aws events list-rules --name-prefix "sp-ingest" \
      --query "Rules[].{Name:Name,State:State,Schedule:ScheduleExpression}" --output table
  ```

  Expected rules:

  | Rule | Schedule | Purpose |
  |------|----------|---------|
  | `sp-ingest-daily-sync-schedule` | `cron(0 7 * * ? *)` | Daily SharePoint delta sync (7 AM UTC) |
  | `sp-ingest-group-cache-refresh` | `rate(15 minutes)` | SCIM user/group cache refresh |
  | `sp-ingest-permission-drift-detector` | `cron(0 2 ? * SUN *)` | Weekly permission drift report |
  | `sp-ingest-stale-account-cleanup` | `cron(0 3 * * ? *)` | Daily stale account pruning |
  | `sp-ingest-compliance-report` | `cron(0 6 1 * ? *)` | Monthly compliance report |

---

## Step 17: Production Go-Live Checklist

Final verification before declaring production-ready.

### Core Pipeline

- [ ] All Lambda functions have been invoked at least once without errors
- [ ] Daily sync Lambda ran successfully (check CloudWatch logs at `/sp-ingest/daily-sync`)
- [ ] Textract trigger processes uploaded documents (S3 event notification working)
- [ ] Textract complete builds JSON twins in `extracted/` prefix
- [ ] Document registry tracks all ingested files

### Permissions and Access Control

- [ ] SCIM sync running every 15 minutes (EventBridge + group-cache-refresh Lambda)
- [ ] `user-group-cache` DynamoDB table populated with user-to-group mappings
- [ ] `doc-permission-mappings` DynamoDB table populated with prefix-to-group mappings
- [ ] Permission drift detector has run at least once (check governance reports in S3)
- [ ] Drift report shows no unmapped prefixes

### Monitoring and Alerting

- [ ] No CloudWatch alarms in `ALARM` state
- [ ] SNS alert email subscription confirmed (not `PendingConfirmation`)
- [ ] CloudWatch dashboard `SP-Ingest-Pipeline` shows data
- [ ] DLQ queues are empty (no failed invocations):
  ```bash
  for QUEUE in sp-ingest-group-cache-refresh-dlq sp-ingest-permission-drift-detector-dlq sp-ingest-stale-account-cleanup-dlq sp-ingest-compliance-report-dlq; do
      COUNT=$(aws sqs get-queue-attributes \
          --queue-url "$(aws sqs get-queue-url --queue-name $QUEUE --query QueueUrl --output text)" \
          --attribute-names ApproximateNumberOfMessages \
          --query "Attributes.ApproximateNumberOfMessages" --output text)
      echo "$QUEUE: $COUNT messages"
  done
  # Expected: 0 messages for each queue
  ```

### Optional (if WebUI deployed)

- [ ] Open WebUI ECS service is running with desired count
- [ ] ALB health check passes (`/health` returns 200)
- [ ] Test query returns expected results via API Gateway
- [ ] Bedrock guardrail is active
- [ ] Query latency p99 is under 10 seconds

---

## Rollback Procedure

If issues are encountered during deployment:

### Destroy all infrastructure

```bash
cd terraform
terraform destroy
```

### Destroy only the bulk EC2 instance

```bash
./scripts/run-bulk-ingest.sh teardown
```

### Disable WebUI without destroying core pipeline

```bash
cd terraform
terraform apply -var="enable_webui=false"
```

### Revert Lambda code to a previous version

Rebuild from a previous git commit and redeploy:

```bash
git checkout <previous-commit>
./scripts/build-lambda.sh
cd terraform && terraform apply
```

---

## Post-Deployment Maintenance

| Task | Frequency | Automated? |
|------|-----------|------------|
| SharePoint delta sync | Daily at 7:00 AM UTC | Yes (EventBridge) |
| SCIM user/group cache refresh | Every 15 minutes | Yes (EventBridge) |
| Permission drift detection | Weekly (Sunday 2:00 AM UTC) | Yes (EventBridge) |
| Stale account cleanup | Daily at 3:00 AM UTC | Yes (EventBridge) |
| Compliance report generation | Monthly (1st at 6:00 AM UTC) | Yes (EventBridge) |
| SCIM token rotation | Annually | No -- see [ENTRA_ID_SCIM_SETUP_RUNBOOK.md](ENTRA_ID_SCIM_SETUP_RUNBOOK.md#maintenance) |
| Bedrock Knowledge Base sync | As configured | No -- manual or scheduled in Console |
| Review governance reports | Weekly | No -- check `s3://BUCKET/governance-reports/` |
| Review DLQ messages | On alarm | No -- investigate and replay or discard |
