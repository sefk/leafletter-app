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
- For multi-line issue bodies, use: `--body "$(cat <<'EOF' ... EOF)"` pattern (heredoc inside subshell is fine per user preference; `--body-file` also works as a fallback)
- For `gh issue comment`, use `--body-file - <<'EOF'` (stdin heredoc) to avoid zsh warnings

## Known Production Incidents

- **2026-03-14 MySQL redo log exhaustion** (Issue #83): Railway default innodb_redo_log_capacity=100MB; OSM Street imports burst ~1.3GB/min causing write stalls. Immediate fix: raise to 1GB via MYSQL_INNODB_REDO_LOG_CAPACITY env var. Long-term fix tracked in #83: use bulk_create() with batch_size in Celery import task.

## Issue Quality Standards

Every open issue should have:
- Clear, specific title
- Description with problem statement and recommended fix
- At least one label
- Milestone or backlog designation for substantial work

## Milestone Structure

(To be documented as milestones are created/reviewed)
