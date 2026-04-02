# Leafletter PM Agent Memory

## Label Taxonomy

Standard labels in sefk/leafletter-app:

| Label | Meaning |
|---|---|
| bug | Something isn't working |
| enhancement | New feature or request |
| documentation | Improvements or additions to documentation |
| good first issue | Good for newcomers |
| help wanted | Extra attention is needed |
| duplicate | Already exists |
| wontfix | Will not be worked on |
| P0 | Highest priority (exists, color #D93F0B) |
| P1 | High priority |
| P2 | Medium priority |
| P3 | Low priority |
| Performance | Performance-related issues |
| ux | User experience and usability |
| mobile | Mobile-specific UX issues |
| prod | Production incident or production-affecting issue (color #B60205, created 2026-03-07) |

## Shell Constraints

- Never use `$()` command substitution in gh or git commands
- Always hardcode repo as `sefk/leafletter-app` in all gh commands
- For multi-line issue bodies, write body to a temp file first and pass with `--body-file /tmp/body.md`, OR pipe via stdin. Do NOT use `$(cat <<'EOF'...)` — that is `$()` substitution.
- For `gh issue comment`, use `--body-file - <<'EOF'` (stdin heredoc) to avoid zsh warnings

## Known Production Incidents

- **2026-03-14 MySQL redo log exhaustion** (Issue #83): Railway default innodb_redo_log_capacity=100MB; OSM Street imports burst ~1.3GB/min causing write stalls. Immediate fix: raise to 1GB via MYSQL_INNODB_REDO_LOG_CAPACITY env var. Long-term fix tracked in #83: use bulk_create() with batch_size in Celery import task.

## Issue Quality Standards

Every open issue should have:
- Clear, specific title
- Description with problem statement and recommended fix
- At least one label
- Milestone or backlog designation for substantial work

## Board Audit History

- **2026-04-01**: Full audit — 34 open issues, all present on board. All closed issues correctly marked Done. No corrections needed; board was fully in sync.

## Milestone Structure

(To be documented as milestones are created/reviewed)

## Project Board

- Board: "Leafletter dev", project number 1, owner sefk
- Project ID: `PVT_kwHOAAl7_c4BQfAR`
- Priority field ID: `PVTSSF_lAHOAAl7_c4BQfARzg-lv4s`
  - P0 option: `79628723`
  - P1 option: `0a877460`
  - P2 option: `da944a9c`
  - P3 option: `9f826784`
- Status field ID: `PVTSSF_lAHOAAl7_c4BQfARzg-lv24` (options: Backlog, Ready, Done)
- New issues ARE auto-added to the board via GHA workflow (merged in #117, 2026-03-29)
- Priority field is synced from labels by a separate workflow that fires on label changes
- Issues created before the auto-add workflow existed (#99–#116 range) may have been missing — audit found #101, #103, #104, #108, #109, #113, #115, #116 were missing and were added manually
- Issues with BLANK priority despite having P-labels are a known debt pattern; fix via `gh project item-edit --single-select-option-id`
