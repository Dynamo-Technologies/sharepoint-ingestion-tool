# ---------------------------------------------------------------
# IAM Identity Center — data source
# ---------------------------------------------------------------
# IAM Identity Center must be enabled manually in the AWS Console.
# SCIM provisioning from Entra ID is configured per the runbook at
# docs/ENTRA_ID_SCIM_SETUP_RUNBOOK.md.

data "aws_ssoadmin_instances" "this" {}

locals {
  identity_store_id = (
    var.identity_store_id != ""
    ? var.identity_store_id
    : tolist(data.aws_ssoadmin_instances.this.identity_store_ids)[0]
  )
}
