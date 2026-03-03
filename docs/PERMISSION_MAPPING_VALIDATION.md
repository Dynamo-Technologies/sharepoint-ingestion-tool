# Permission Mapping Validation Report

Generated: 2026-03-03 01:27 UTC

## Summary

**Status**: PASS
- Errors: 0
- Warnings: 3
- Info: 2

## Coverage Statistics

| Metric | Count | Percentage |
|--------|-------|------------|
| Groups mapped | 49/421 | 11.6% |
| Active members with tags | 124/341 | 36.4% |
| Active guests | 195 | — |

## Tag Distribution

| Access Tag | Users Assigned |
|------------|----------------|
| all-staff | 124 |
| engineering | 72 |
| leadership | 53 |
| contracts | 37 |
| bd | 20 |
| capture | 20 |
| hr | 18 |
| finance | 7 |

## Mapped Groups

Total: 49 groups

| Group Name | Type | Tags | Members | Rule |
|------------|------|------|---------|------|
| 2025 TM & Recruiting Goals | Unified | hr | 2 | Recruiting groups → hr tag |
| Accounting | Unified | finance | 6 | Accounting groups → finance tag |
| BD Events | Unified | bd, capture | 1 | BD groups → bd + capture tags |
| BD Lessons Learned | Unified | bd, capture | 5 | BD groups → bd + capture tags |
| BD PROPOSAL TEST | Unified | bd, capture | 1 | BD groups → bd + capture tags |
| BD Pipeline Test | Unified | bd, capture | 1 | BD groups → bd + capture tags |
| Business Development | Security | bd, capture | 11 | Business Development → bd + capture tags |
| Business Development - Archive DO NOT USE | Unified | bd, capture | 16 | Business Development → bd + capture tags |
| Cloud Adoption & Solution Engineering | Unified | engineering | 3 | Engineering groups → engineering tag |
| Contracts | Unified | contracts | 19 | Contracts groups → contracts tag |
| Contracts | Security | contracts | 3 | Contracts groups → contracts tag |
| Contracts Team Initiatives | Unified | contracts | 2 | Contracts groups → contracts tag |
| Contracts Team Initiatives 2025 | Unified | contracts | 3 | Contracts groups → contracts tag |
| Contracts Test | Unified | contracts | 1 | Contracts groups → contracts tag |
| DG - Technical CoP | Security | engineering | 64 | Technical groups → engineering tag |
| DOD DHS LE Managers | Security | leadership | 14 | Manager groups → leadership tag |
| Delivery Managers | Security | leadership | 32 | Manager groups → leadership tag; Delivery Managers → leadership tag |
| Dynamo Contracts Activity Status | Unified | contracts | 28 | Contracts groups → contracts tag |
| Dynamo Leadership | Unified | leadership | 10 | Leadership groups → leadership tag |
| Dynamo Managers | Unified | leadership | 12 | Manager groups → leadership tag |
| Dynamo Recruiting Distro | Security | hr | 3 | Recruiting groups → hr tag |
| Dynamo Technology Community of Interest (COI) | Unified | engineering | 2 | Technology groups → engineering tag |
| Emerging Technology | Unified | engineering | 3 | Technology groups → engineering tag |
| Fed Civ Managers | Security | leadership | 6 | Manager groups → leadership tag |
| Finance | Unified | finance | 2 | Finance groups → finance tag |
| Finance | Security | finance | 2 | Finance groups → finance tag |
| HR CASE | Unified | hr | 3 | HR groups → hr tag |
| HR Case | Unified | hr | 4 | HR groups → hr tag |
| HR Transitions | Unified | hr | 5 | HR groups → hr tag |
| HRCASE Management | Unified | hr | 1 | HR groups → hr tag |
| Human Capital and Transformation Management COP | Unified | hr | 1 | Human Capital groups → hr tag |
| ISO Leadership | Unified | leadership | 11 | Leadership groups → leadership tag |
| Leadership | Unified | leadership | 12 | Leadership groups → leadership tag |
| Managers | Security | leadership | 46 | Manager groups → leadership tag |
| Recruiting | Unified | hr | 1 | Recruiting groups → hr tag |
| Recruiting | Unified | hr | 7 | Recruiting groups → hr tag |
| SG - Account Managers | Security | leadership | 8 | Manager groups → leadership tag |
| SG - FSO Security Team | Security | all-staff | 2 | FSO Security → all-staff tag |
| SG - Legal | Security | contracts | 6 | Legal security group → contracts tag |
| SG - Women In Technology | Security | engineering | 4 | Technology groups → engineering tag |
| SG - jira-accounting-members | Security | finance | 5 | Accounting groups → finance tag |
| SG - jira-contracts-members | Security | contracts | 3 | Contracts groups → contracts tag |
| SG - jira-leadership-members | Security | leadership | 11 | Leadership groups → leadership tag |
| SG - jira-recruiting-members | Security | hr | 2 | Recruiting groups → hr tag |
| Security | Unified | all-staff | 5 | Security team → all-staff tag |
| Technical CoE | Unified | engineering | 6 | Technical groups → engineering tag |
| Technology | Unified | engineering | 2 | Technology groups → engineering tag |
| USCG Managers | Security | leadership | 6 | Manager groups → leadership tag |
| USDA Managers | Security | leadership | 12 | Manager groups → leadership tag |

## Validation Issues

- **[INFO]** [coverage] Group coverage: 49/421 (11.6%)
- **[INFO]** [coverage] User coverage: 124/341 active members (36.4%)
- **[WARN]** [consistency] Tags defined but not assigned to any group: ['admin', 'tech-leads']
  - These tags exist in access_rules.yaml but no Entra group maps to them.
- **[WARN]** [orphan] 225 active members have no specific tag assignments (will get all-staff only)
  - These users aren't in any mapped group. They'll only see documents tagged 'all-staff'.
- **[WARN]** [gap] 47 unmapped groups have 10+ members
  - Consider adding mapping rules for these groups if they correspond to document access patterns.

## Notable Unmapped Groups (10+ members)

| Group Name | Type | Members | Description |
|------------|------|---------|-------------|
| SG - All Dynamo Users | DynamicMembership | 377 |  |
| SG - Microsoft 365 F3 | Security | 186 | Group for users that require an F3 license. |
| SG - Microsoft 365 E3 | Security | 127 | Licensing group for Microsoft 365 E3 |
| SG - Intune Autopilot Devices | DynamicMembership | 102 | Group for devices registered during Autopilot process. |
| Dynamo Women | Security | 78 | Dynamo Women |
| DMV Dynamo Team Only | Security | 56 | Happy hour evite invitation from Dynamo Technologies |
| SG - 1Password Users | Security | 45 | Mail enabled security group for 1Password users |
| Dynamo HQ [Vienna] | Security | 39 | Distro list for users in the Tysons, VA region. |
| Projects | Unified | 26 | Projects |
| Corporate Certifications | Unified | 25 | Working folder for corporate certifications such as ISO 2700 |
| SG - Microsoft 365 Audio Conferencing | Security | 25 | Licensing group for audio conferencing. |
| Security - Cleared Personnel and Contractors | Security | 25 |  |
| SG - Perplexity Users | Security | 22 | Security Group for Perplexity users. |
| VSxDynamo | Unified | 21 | Group for integration planning and calendar sync. |
| Emergency Response Team | Unified | 21 | Emergency Response Team |
| Dynamo_DMV_All | Security | 20 |  |
| Delivery & Operations | Unified | 19 | Collaboration for Operations and Delivery |
| CMMI 3.0 | Unified | 19 | CMMI 3.0 |
| SG - Atari CR Allowed | Security | 17 | Security group for users that are allowed to book the Atari  |
| Proposals | Security | 17 | A rule in place to forward emails coming to proposals@dynamo |
| Timesheet Approvers | Security | 17 | Timesheet Approvers |
| Proposal Pricing | Unified | 17 | Proposal Pricing Documentation for Proposals |
| Proposal Archive | Unified | 17 | Repository for Archived Proposals |
| Corporate | Unified | 15 | Corporate |
| Lattice Reviewers | Security | 15 | Lattice Reviewers |
| ... | ... | ... | (22 more) |

## Users Without Specific Tag Assignments

Total: 225 active members (will receive `all-staff` only)

| Name | Department | Groups |
|------|-----------|--------|
| Tim Clise |  | SG - All Dynamo Users, Holokai |
| About Her |  | SG - All Dynamo Users |
| Accounts Payable |  | SG - All Dynamo Users |
| Accounts Receivable |  | SG - All Dynamo Users |
| Accounts - Payable (Dynamo) |  | SG - All Dynamo Users, Invoices |
| Accounts - Receivable (Dynamo) |  | SG - All Dynamo Users |
| Adam Mazurek |  | SG - Microsoft 365 F3, SG - All Dynamo Users |
| Alaun Buckley |  | SG - Microsoft 365 E3, SG - All Dynamo Users |
| Alex Ramirez |  | SG - Microsoft 365 F3, SG - All Dynamo Users |
| Alexander Curnow | Innovation | SG - Microsoft 365 E3, SG - All Dynamo Users |
| Andrea Begley |  | Corporate Certifications, SG - Microsoft 365 E3, SG - All Dynamo Users (+5 more) |
| Andrea Rippe |  | SG - Microsoft 365 F3, SG - All Dynamo Users, DMV Dynamo Team Only (+2 more) |
| Annie Ma (CTR) | 1099 Contractor | SG - All Dynamo Contractors, SG - Microsoft 365 F3, SG - All Dynamo Users |
| Anthony Handy |  | SG - Microsoft 365 F3, SG - All Dynamo Users, DMV Dynamo Team Only |
| Anthony Laboy | USCG C5ISC C2PL | SG - Microsoft 365 E3, SG - All Dynamo Users |
| Apple (Talent Management Use Only) | TM | SG - All Dynamo Users |
| Arjun Kunduru |  | SG - Microsoft 365 F3, SG - All Dynamo Users |
| Arthur Rippy (CTR) | 1099 Contractor | SG - All Dynamo Contractors, SG - Microsoft 365 F3, SG - All Dynamo Users |
| Ashley Nguyen | Human Resources | SG - Atari CR Allowed, Corporate Certifications, SG - Microsoft 365 E3 (+8 more) |
| Atari |  | SG - All Dynamo Users |
| Ballalaine Davies |  | SG - Microsoft 365 F3, SG - All Dynamo Users, Dynamo Women |
| Benjamin Campbell | USDA FS ADAS | ABOUT Us CDATDI, SG - Microsoft 365 E3, SG - All Dynamo Users |
| Betty Lanham |  | INSCOMGroupDistro, All Projects Distro, SG - Microsoft 365 E3 (+4 more) |
| Bloomsfield Parbey (CTR) | 1099 Contractor | SG - All Dynamo Contractors, SG - Microsoft 365 F3, SG - All Dynamo Users |
| Brendan Burger | USDA FS CIO ADAS | SG - Microsoft 365 F3, SG - All Dynamo Users |
| Brian McGinnis |  | SG - Microsoft 365 E3, SG - All Dynamo Users |
| Brian Vanderburg |  | SG - All Dynamo Users, NCUA TO8, Dynamo |
| Brian Winterbottom |  | SG - Microsoft 365 E3, SG - All Dynamo Users |
| Brianna Sobota |  | SG - Microsoft 365 E3, SG - All Dynamo Users, DLA PIEE PMO Dynamo Team (+1 more) |
| Brittany Stapleton |  | SG - Microsoft 365 F3, SG - All Dynamo Users, Dynamo Women |
| ... | ... | (195 more) |
